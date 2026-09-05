import os
import logging
import math
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# LUMI AI — PRODUCTION FOUNDATION
# Telegram + XAUUSD Structural Intelligence
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("LumiAI")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Yahoo Finance chart endpoint.
# XAUUSD=X is used as the spot-gold reference symbol.
MARKET_SYMBOL = "XAUUSD=X"
MARKET_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + urllib.parse.quote(MARKET_SYMBOL)
)

DATA_INTERVAL = "1h"
DATA_RANGE = "1mo"


# ============================================================
# BASIC MATH
# ============================================================

def mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    current = mean(values[:period])

    for price in values[period:]:
        current = ((price - current) * multiplier) + current

    return current


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    value = mean(true_ranges[:period])

    for tr in true_ranges[period:]:
        value = ((value * (period - 1)) + tr) / period

    return value


# ============================================================
# MARKET DATA
# ============================================================

def fetch_market_data():
    params = urllib.parse.urlencode(
        {
            "interval": DATA_INTERVAL,
            "range": DATA_RANGE,
        }
    )

    url = f"{MARKET_URL}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8")

    payload = json.loads(raw)

    result = payload["chart"]["result"]

    if not result:
        raise RuntimeError("Market data provider returned no result.")

    chart = result[0]

    timestamps = chart.get("timestamp", [])
    quote = chart["indicators"]["quote"][0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])

    rows = []

    for i in range(len(timestamps)):
        if (
            i < len(opens)
            and i < len(highs)
            and i < len(lows)
            and i < len(closes)
            and opens[i] is not None
            and highs[i] is not None
            and lows[i] is not None
            and closes[i] is not None
        ):
            rows.append(
                {
                    "time": timestamps[i],
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                }
            )

    if len(rows) < 60:
        raise RuntimeError(
            f"Not enough market candles received: {len(rows)}"
        )

    return rows


# ============================================================
# MARKET STRUCTURE
# ============================================================

def detect_structure(highs, lows):
    """
    Simple swing-structure model.

    Uses recent swing highs/lows to classify:
    - Bullish
    - Bearish
    - Neutral
    """

    if len(highs) < 20 or len(lows) < 20:
        return "Neutral"

    recent_highs = highs[-20:]
    recent_lows = lows[-20:]

    first_half_high = max(recent_highs[:10])
    second_half_high = max(recent_highs[10:])

    first_half_low = min(recent_lows[:10])
    second_half_low = min(recent_lows[10:])

    higher_high = second_half_high > first_half_high
    higher_low = second_half_low > first_half_low

    lower_high = second_half_high < first_half_high
    lower_low = second_half_low < first_half_low

    if higher_high and higher_low:
        return "Bullish"

    if lower_high and lower_low:
        return "Bearish"

    return "Neutral"


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def calculate_levels(highs, lows, closes):
    window = min(48, len(closes))

    recent_highs = highs[-window:]
    recent_lows = lows[-window:]
    current_price = closes[-1]

    resistance = max(recent_highs)
    support = min(recent_lows)

    # Additional nearby levels using shorter windows.
    short_window = min(12, len(closes))

    short_resistance = max(highs[-short_window:])
    short_support = min(lows[-short_window:])

    levels = {
        "major_support": support,
        "major_resistance": resistance,
        "near_support": short_support,
        "near_resistance": short_resistance,
        "price": current_price,
    }

    return levels


# ============================================================
# MARKET INTELLIGENCE ENGINE
# ============================================================

def analyze_market(rows):
    closes = [x["close"] for x in rows]
    highs = [x["high"] for x in rows]
    lows = [x["low"] for x in rows]

    price = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi14 = rsi(closes, 14)
    atr14 = atr(highs, lows, closes, 14)

    structure = detect_structure(highs, lows)

    levels = calculate_levels(highs, lows, closes)

    score = 50
    evidence = []

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if structure == "Bullish":
        score += 15
        evidence.append("Higher-timeframe structure is bullish.")

    elif structure == "Bearish":
        score -= 15
        evidence.append("Recent market structure is bearish.")

    else:
        evidence.append("Market structure is mixed/unclear.")

    # --------------------------------------------------------
    # EMA TREND
    # --------------------------------------------------------

    if ema20 and ema50:

        if ema20 > ema50:
            score += 15
            evidence.append("EMA20 is above EMA50.")

        elif ema20 < ema50:
            score -= 15
            evidence.append("EMA20 is below EMA50.")

        if price > ema20:
            score += 5
            evidence.append("Price is above EMA20.")

        else:
            score -= 5
            evidence.append("Price is below EMA20.")

    # --------------------------------------------------------
    # RSI MOMENTUM
    # --------------------------------------------------------

    if rsi14 is not None:

        if 50 <= rsi14 <= 68:
            score += 8
            evidence.append("RSI supports bullish momentum.")

        elif 32 <= rsi14 < 50:
            score -= 8
            evidence.append("RSI supports bearish momentum.")

        elif rsi14 > 70:
            evidence.append(
                "RSI is overbought; upside momentum may be extended."
            )

        elif rsi14 < 30:
            evidence.append(
                "RSI is oversold; downside momentum may be extended."
            )

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE LOCATION
    # --------------------------------------------------------

    support = levels["major_support"]
    resistance = levels["major_resistance"]

    range_size = resistance - support

    if range_size > 0:

        position = (price - support) / range_size

        if position < 0.30:
            score += 5
            evidence.append("Price is positioned relatively close to support.")

        elif position > 0.70:
            score -= 5
            evidence.append(
                "Price is positioned relatively close to resistance."
            )

    # --------------------------------------------------------
    # FINAL SCORE
    # --------------------------------------------------------

    score = max(0, min(100, round(score)))

    if score >= 70:
        bias = "BULLISH"
        strength = "HIGH"

    elif score >= 58:
        bias = "BULLISH"
        strength = "MODERATE"

    elif score <= 30:
        bias = "BEARISH"
        strength = "HIGH"

    elif score <= 42:
        bias = "BEARISH"
        strength = "MODERATE"

    else:
        bias = "NEUTRAL"
        strength = "LOW"

    # --------------------------------------------------------
    # SCENARIO LEVELS
    # --------------------------------------------------------

    buy_trigger = None
    sell_trigger = None
    invalidation = None
    target_1 = None
    target_2 = None

    if atr14:

        if bias == "BULLISH":

            buy_trigger = max(price, levels["near_resistance"])

            invalidation = buy_trigger - (atr14 * 1.2)

            target_1 = buy_trigger + (atr14 * 1.5)
            target_2 = buy_trigger + (atr14 * 2.5)

        elif bias == "BEARISH":

            sell_trigger = min(price, levels["near_support"])

            invalidation = sell_trigger + (atr14 * 1.2)

            target_1 = sell_trigger - (atr14 * 1.5)
            target_2 = sell_trigger - (atr14 * 2.5)

    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi14,
        "atr": atr14,
        "structure": structure,
        "support": support,
        "resistance": resistance,
        "bias": bias,
        "strength": strength,
        "score": score,
        "evidence": evidence,
        "buy_trigger": buy_trigger,
        "sell_trigger": sell_trigger,
        "invalidation": invalidation,
        "target_1": target_1,
        "target_2": target_2,
    }


# ============================================================
# FORMATTING
# ============================================================

def money(value):
    if value is None:
        return "N/A"

    return f"{value:,.2f}"


def build_market_report():

    rows = fetch_market_data()

    analysis = analyze_market(rows)

    now = datetime.now(timezone.utc)

    lines = [
        "🟡 LUMI XAUUSD INTELLIGENCE",
        "",
        f"💰 Price: {money(analysis['price'])}",
        f"🧭 Bias: {analysis['bias']}",
        f"📊 Structural Score: {analysis['score']}/100",
        f"🎯 Signal Strength: {analysis['strength']}",
        "",
        "🏗 MARKET STRUCTURE",
        f"• Structure: {analysis['structure']}",
        f"• EMA20: {money(analysis['ema20'])}",
        f"• EMA50: {money(analysis['ema50'])}",
        "",
        "📈 MOMENTUM",
        f"• RSI(14): {money(analysis['rsi'])}",
        f"• ATR(14): {money(analysis['atr'])}",
        "",
        "🎯 KEY LEVELS",
        f"• Support: {money(analysis['support'])}",
        f"• Resistance: {money(analysis['resistance'])}",
        "",
        "🧠 STRUCTURAL EVIDENCE",
    ]

    for item in analysis["evidence"]:
        lines.append(f"• {item}")

    lines.extend(
        [
            "",
            "📌 TRADE SCENARIO",
        ]
    )

    if analysis["bias"] == "BULLISH":

        lines.extend(
            [
                f"• Confirmation area: {money(analysis['buy_trigger'])}",
                f"• Invalidation: {money(analysis['invalidation'])}",
                f"• Scenario target 1: {money(analysis['target_1'])}",
                f"• Scenario target 2: {money(analysis['target_2'])}",
                "",
                "🟢 Preferred direction: BUY AFTER CONFIRMATION",
            ]
        )

    elif analysis["bias"] == "BEARISH":

        lines.extend(
            [
                f"• Confirmation area: {money(analysis['sell_trigger'])}",
                f"• Invalidation: {money(analysis['invalidation'])}",
                f"• Scenario target 1: {money(analysis['target_1'])}",
                f"• Scenario target 2: {money(analysis['target_2'])}",
                "",
                "🔴 Preferred direction: SELL AFTER CONFIRMATION",
            ]
        )

    else:

        lines.extend(
            [
                "• No strong directional setup confirmed.",
                "• Wait for structure and momentum alignment.",
            ]
        )

    lines.extend(
        [
            "",
            f"🕐 Data checked: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "📡 Reference: XAUUSD=X market feed",
            "",
            "⚠️ Lumi's score is a technical model score, NOT a guaranteed probability of profit.",
            "⚠️ Always manage risk. Market conditions can change rapidly.",
        ]
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "✨ Hello! I am Lumi AI.\n\n"
        "🧠 Your intelligent assistant is online.\n"
        "🟡 My XAUUSD structural intelligence system is active.\n\n"
        "Use /help to see my commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🤖 LUMI AI COMMANDS\n\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n"
        "/status - System status\n"
        "/gold - XAUUSD intelligence\n"
        "/market - Market analysis\n"
        "/analyze - Full XAUUSD analysis\n"
        "/about - About Lumi\n\n"
        "💬 You can also talk to me normally."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🟢 LUMI SYSTEM STATUS\n\n"
        "Lumi AI: Online\n"
        "Telegram: Connected\n"
        "XAUUSD Engine: Active\n"
        "Structural Analysis: Active\n"
        "EMA Analysis: Active\n"
        "RSI Analysis: Active\n"
        "Support/Resistance: Active\n"
        "Risk Framework: Active\n\n"
        "📡 Live market data is requested when analysis is run."
    )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🧠 ABOUT LUMI AI\n\n"
        "Lumi is an AI assistant designed to combine "
        "conversation with structured market intelligence.\n\n"
        "Her analysis is based on measurable market information "
        "rather than random predictions.\n\n"
        "⚠️ Market analysis is probabilistic and never guaranteed."
    )


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🟡 Lumi is analyzing XAUUSD...\n\n"
        "Checking price, structure, trend, momentum and key levels."
    )

    try:

        report = build_market_report()

        await update.message.reply_text(report)

    except Exception as exc:

        logger.exception("Gold analysis failed")

        await update.message.reply_text(
            "⚠️ Lumi could not retrieve reliable XAUUSD data right now.\n\n"
            "No market signal will be invented.\n\n"
            f"System detail: {str(exc)[:180]}"
        )


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await gold(update, context)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await gold(update, context)


# ============================================================
# NATURAL LANGUAGE
# ============================================================

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (update.message.text or "").lower().strip()

    if any(word in text for word in ["hello", "hi", "hey", "good morning", "good evening"]):

        await update.message.reply_text(
            "✨ Hello. I'm Lumi AI.\n\n"
            "I'm online and ready."
        )
        return

    if "who are you" in text or "what are you" in text:

        await update.message.reply_text(
            "🧠 I'm Lumi AI — your intelligent assistant.\n\n"
            "I can communicate with you and analyze XAUUSD "
            "using structured market information."
        )
        return

    if any(word in text for word in [
        "gold",
        "xauusd",
        "xau",
        "forex",
        "trading",
        "market",
    ]):

        await update.message.reply_text(
            "🟡 I can analyze XAUUSD using live market data.\n\n"
            "Use /gold or /analyze for the full structural report."
        )
        return

    if "status" in text:

        await status(update, context)
        return

    if "thank" in text:

        await update.message.reply_text(
            "❤️ You're welcome. Lumi is here."
        )
        return

    await update.message.reply_text(
        "🧠 I'm Lumi AI.\n\n"
        "I understand you. You can ask me about my capabilities "
        "or ask for an XAUUSD analysis with /gold."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is missing."
        )

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("gold", gold))
    application.add_handler(CommandHandler("market", market))
    application.add_handler(CommandHandler("analyze", analyze))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat,
        )
    )

    logger.info("Lumi AI is starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
