import os
import logging
import sqlite3
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# LUMI AI 2.0 — MARKET INTELLIGENCE ENGINE
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MONITOR_INTERVAL = 300
ALERT_COOLDOWN_SECONDS = 1800
DATABASE_FILE = "lumi.db"

# Automatic alerts require independent confirmation.
MIN_SIGNAL_CONFIDENCE = 80


# ============================================================
# MARKET SYMBOLS
# ============================================================

# IMPORTANT:
# Yahoo Finance does not provide spot XAUUSD through XAUUSD=X.
# GC=F is the Yahoo Finance Gold Futures reference.
YAHOO_SYMBOLS = {
    "XAUUSD": "GC=F",

    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",

    "SPX500": "^GSPC",
    "NAS100": "^NDX",
    "DJ30": "^DJI",
    "GER40": "^GDAXI",
    "UK100": "^FTSE",
    "JP225": "^N225",

    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
}


MARKETS = {
    "XAUUSD": "Gold",
    "BTCUSD": "Bitcoin",
    "ETHUSD": "Ethereum",
    "SPX500": "S&P 500",
    "NAS100": "Nasdaq 100",
    "DJ30": "Dow Jones",
    "GER40": "DAX",
    "UK100": "FTSE 100",
    "JP225": "Nikkei 225",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("LumiAI")


# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(DATABASE_FILE)


def initialize_database():
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                alerts_enabled INTEGER DEFAULT 1,
                created_at TEXT,
                last_alert_key TEXT,
                last_alert_time TEXT
            )
            """
        )


def register_chat(chat_id):
    with db() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO users
            (chat_id, alerts_enabled, created_at)
            VALUES (?, ?, ?)
            """,
            (
                chat_id,
                1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def set_alert_status(chat_id, enabled):
    with db() as connection:
        connection.execute(
            """
            UPDATE users
            SET alerts_enabled = ?
            WHERE chat_id = ?
            """,
            (1 if enabled else 0, chat_id),
        )


def get_alert_status(chat_id):
    with db() as connection:
        row = connection.execute(
            """
            SELECT alerts_enabled
            FROM users
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()

    return bool(row[0]) if row else False


def get_alert_users():
    with db() as connection:
        rows = connection.execute(
            """
            SELECT chat_id
            FROM users
            WHERE alerts_enabled = 1
            """
        ).fetchall()

    return [row[0] for row in rows]


# ============================================================
# ALERT DUPLICATE PROTECTION
# ============================================================

def alert_is_allowed(chat_id, alert_key):

    with db() as connection:
        row = connection.execute(
            """
            SELECT last_alert_key, last_alert_time
            FROM users
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()

    if not row or not row[1]:
        return True

    try:
        previous_time = datetime.fromisoformat(row[1])

        age = (
            datetime.now(timezone.utc) - previous_time
        ).total_seconds()

        if row[0] == alert_key and age < ALERT_COOLDOWN_SECONDS:
            return False

        return True

    except ValueError:
        return True


def save_alert(chat_id, alert_key):

    with db() as connection:
        connection.execute(
            """
            UPDATE users
            SET last_alert_key = ?,
                last_alert_time = ?
            WHERE chat_id = ?
            """,
            (
                alert_key,
                datetime.now(timezone.utc).isoformat(),
                chat_id,
            ),
        )


# ============================================================
# NUMERIC HELPERS
# ============================================================

def safe_float(value, default=None):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def money(value):

    value = safe_float(value)

    if value is None:
        return "—"

    return f"${value:,.2f}"


def pct(value):

    value = safe_float(value)

    if value is None:
        return "—"

    return f"{value:+.2f}%"


# ============================================================
# EMA
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    current = sum(values[:period]) / period

    for price in values[period:]:
        current = (
            (price - current) * multiplier
        ) + current

    return current


# ============================================================
# RSI
# ============================================================

def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ============================================================
# ATR
# ============================================================

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

    return sum(true_ranges[-period:]) / period


# ============================================================
# YAHOO MARKET DATA
# ============================================================

def fetch_market_data(symbol):

    if symbol not in YAHOO_SYMBOLS:
        raise ValueError(
            f"Unsupported market: {symbol}"
        )

    yahoo_symbol = YAHOO_SYMBOLS[symbol]

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + yahoo_symbol
    )

    params = {
        "range": "5d",
        "interval": "1h",
        "includePrePost": "true",
        "events": "div,splits",
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/130 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=20,
    )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get("chart", {})

    results = chart.get("result")

    if not results:
        raise ValueError(
            "Yahoo returned no market data."
        )

    result = results[0]

    timestamps = result.get(
        "timestamp",
        [],
    )

    quote = result.get(
        "indicators",
        {},
    ).get(
        "quote",
        [{}],
    )[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])

    rows = []

    for i in range(len(timestamps)):

        try:

            if (
                opens[i] is None
                or highs[i] is None
                or lows[i] is None
                or closes[i] is None
            ):
                continue

            rows.append(
                {
                    "timestamp": timestamps[i],
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                }
            )

        except (
            IndexError,
            TypeError,
            ValueError,
        ):
            continue

    if len(rows) < 30:
        raise ValueError(
            "Insufficient market candles."
        )

    return rows


# ============================================================
# MARKET INTELLIGENCE
# ============================================================

def analyze_market(symbol):

    candles = fetch_market_data(symbol)

    closes = [
        candle["close"]
        for candle in candles
    ]

    highs = [
        candle["high"]
        for candle in candles
    ]

    lows = [
        candle["low"]
        for candle in candles
    ]

    price = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)

    rsi14 = rsi(closes, 14)

    atr14 = atr(
        highs,
        lows,
        closes,
        14,
    )

    if (
        ema9 is None
        or ema21 is None
        or ema50 is None
    ):
        raise ValueError(
            "Not enough data for trend analysis."
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum5h = None
    momentum20h = None

    if len(closes) >= 6:

        momentum5h = (
            (price - closes[-6])
            / closes[-6]
        ) * 100

    if len(closes) >= 21:

        momentum20h = (
            (price - closes[-21])
            / closes[-21]
        ) * 100

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    structure_window = candles[-30:]

    support = min(
        candle["low"]
        for candle in structure_window
    )

    resistance = max(
        candle["high"]
        for candle in structure_window
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    bullish_trend = (
        ema9 > ema21 > ema50
    )

    bearish_trend = (
        ema9 < ema21 < ema50
    )

    if bullish_trend:
        trend = "BULLISH"

    elif bearish_trend:
        trend = "BEARISH"

    else:
        trend = "MIXED"

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    bullish_momentum = (
        momentum5h is not None
        and momentum20h is not None
        and momentum5h > 0
        and momentum20h > 0
    )

    bearish_momentum = (
        momentum5h is not None
        and momentum20h is not None
        and momentum5h < 0
        and momentum20h < 0
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_bullish = (
        rsi14 is not None
        and 50 <= rsi14 < 70
    )

    rsi_bearish = (
        rsi14 is not None
        and 30 < rsi14 <= 50
    )

    # --------------------------------------------------------
    # EVIDENCE SCORE
    # --------------------------------------------------------

    buy_score = 0
    sell_score = 0

    reasoning = []

    if bullish_trend:

        buy_score += 30

        reasoning.append(
            "EMA structure is bullish."
        )

    elif bearish_trend:

        sell_score += 30

        reasoning.append(
            "EMA structure is bearish."
        )

    else:

        reasoning.append(
            "EMA structure is mixed."
        )

    if bullish_momentum:

        buy_score += 25

        reasoning.append(
            "5h and 20h momentum are positive."
        )

    elif bearish_momentum:

        sell_score += 25

        reasoning.append(
            "5h and 20h momentum are negative."
        )

    if rsi_bullish:

        buy_score += 15

        reasoning.append(
            "RSI supports bullish momentum."
        )

    elif rsi_bearish:

        sell_score += 15

        reasoning.append(
            "RSI supports bearish momentum."
        )

    # --------------------------------------------------------
    # PRICE STRUCTURE
    # --------------------------------------------------------

    if price > resistance:

        buy_score += 20

        reasoning.append(
            "Price is above the recent resistance zone."
        )

    elif price < support:

        sell_score += 20

        reasoning.append(
            "Price is below the recent support zone."
        )

    # --------------------------------------------------------
    # VOLATILITY REGIME
    # --------------------------------------------------------

    if atr14 is None or price == 0:

        regime = "UNKNOWN"

    else:

        volatility_percent = (
            atr14 / price
        ) * 100

        if volatility_percent < 0.20:

            regime = "LOW_VOLATILITY"

        elif volatility_percent < 0.60:

            regime = "NORMAL_VOLATILITY"

        else:

            regime = "HIGH_VOLATILITY"

    # --------------------------------------------------------
    # DECISION
    # --------------------------------------------------------

    setup = "NO_TRADE"

    confidence = max(
        buy_score,
        sell_score,
    )

    if (
        buy_score >= 70
        and buy_score > sell_score
        and rsi14 is not None
        and rsi14 < 70
    ):

        setup = "BUY_SETUP"

    elif (
        sell_score >= 70
        and sell_score > buy_score
        and rsi14 is not None
        and rsi14 > 30
    ):

        setup = "SELL_SETUP"

    else:

        reasoning.append(
            "Evidence is not strong enough for a trade setup."
        )

    # --------------------------------------------------------
    # BIAS
    # --------------------------------------------------------

    if buy_score > sell_score:

        bias = "BULLISH"

    elif sell_score > buy_score:

        bias = "BEARISH"

    else:

        bias = "NEUTRAL"

    # --------------------------------------------------------
    # TRADE LEVELS
    # --------------------------------------------------------

    entry = None
    stop_loss = None
    take_profit1 = None
    take_profit2 = None

    if (
        setup in (
            "BUY_SETUP",
            "SELL_SETUP",
        )
        and atr14
    ):

        entry = price

        risk = atr14 * 1.20

        if setup == "BUY_SETUP":

            stop_loss = entry - risk

            take_profit1 = (
                entry + risk * 1.50
            )

            take_profit2 = (
                entry + risk * 2.50
            )

        else:

            stop_loss = entry + risk

            take_profit1 = (
                entry - risk * 1.50
            )

            take_profit2 = (
                entry - risk * 2.50
            )

    # --------------------------------------------------------
    # SAFETY STATUS
    # --------------------------------------------------------

    # One provider is not independent confirmation.
    market_status = "SINGLE_SOURCE"

    validation = "INDEPENDENT_UNAVAILABLE"

    latest_timestamp = candles[-1]["timestamp"]

    now_timestamp = datetime.now(
        timezone.utc
    ).timestamp()

    freshness = max(
        0,
        int(
            now_timestamp
            - latest_timestamp
        ),
    )

    return {

        "symbol": symbol,

        "price": price,

        "marketDataStatus": market_status,

        "globalSource": (
            f"Yahoo Finance "
            f"({YAHOO_SYMBOLS[symbol]})"
        ),

        "priceValidation": validation,

        "priceDeviationPercent": None,

        "globalFreshnessSeconds": freshness,

        "bias": bias,

        "trend": trend,

        "rsi": rsi14,

        "momentum5h": momentum5h,

        "momentum20h": momentum20h,

        "regime": regime,

        "timeframeConfirmation": confidence,

        "support": support,

        "resistance": resistance,

        "structureBreak": (
            "ABOVE_RESISTANCE"
            if price > resistance
            else
            "BELOW_SUPPORT"
            if price < support
            else
            "WITHIN_RANGE"
        ),

        "setupType": setup,

        "entryPrice": entry,

        "stopLoss": stop_loss,

        "takeProfit1": take_profit1,

        "takeProfit2": take_profit2,

        "confidence": confidence,

        "reasoning": reasoning,

        # XM remains separate until connected properly.
        "brokerPriceStatus": "UNAVAILABLE",

        "brokerSymbol": None,

        "brokerBid": None,

        "brokerAsk": None,

        "brokerSpread": None,
    }


# ============================================================
# MARKET FETCH
# ============================================================

def get_market(symbol):

    symbol = symbol.upper()

    if symbol not in MARKETS:
        raise ValueError(
            f"Unsupported market: {symbol}"
        )

    return analyze_market(symbol)


# ============================================================
# ALERT SAFETY GATE
# ============================================================

def validated_alert(data):

    setup = data.get("setupType")

    market_status = data.get(
        "marketDataStatus",
        "REFERENCE_ONLY",
    )

    validation = data.get(
        "priceValidation",
        "INDEPENDENT_UNAVAILABLE",
    )

    confidence = safe_float(
        data.get("confidence"),
        0,
    )

    return (
        setup in (
            "BUY_SETUP",
            "SELL_SETUP",
        )
        and market_status == "GLOBAL_LIVE"
        and validation == "INDEPENDENT_CONFIRMED"
        and confidence >= MIN_SIGNAL_CONFIDENCE
    )


# ============================================================
# MARKET REPORT
# ============================================================

def format_market_report(data):

    setup = data.get(
        "setupType",
        "NO_TRADE",
    )

    if setup == "BUY_SETUP":

        decision = "BUY"

    elif setup == "SELL_SETUP":

        decision = "SELL"

    else:

        decision = "NO TRADE"

    lines = [

        f"🟡 LUMI {data.get('symbol', 'MARKET')} INTELLIGENCE",

        "",

        f"💰 Market price: "
        f"{money(data.get('price'))}",

        f"📡 Data status: "
        f"{data.get('marketDataStatus')}",

        f"🌐 Source: "
        f"{data.get('globalSource')}",

        f"🔎 Validation: "
        f"{data.get('priceValidation')}",

        f"⏱ Quote age: "
        f"{data.get('globalFreshnessSeconds', '—')}s",

        "",

        "━━━━━━━━━━━━━━━━━━",
        "📊 MARKET STRUCTURE",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"Bias: {data.get('bias')}",

        f"Trend: {data.get('trend')}",

        f"RSI 14: "
        f"{safe_float(data.get('rsi'), 0):.1f}",

        f"Momentum 5h: "
        f"{pct(data.get('momentum5h'))}",

        f"Momentum 20h: "
        f"{pct(data.get('momentum20h'))}",

        f"Regime: "
        f"{data.get('regime')}",

        "",

        "━━━━━━━━━━━━━━━━━━",
        "📍 KEY LEVELS",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"Support: "
        f"{money(data.get('support'))}",

        f"Resistance: "
        f"{money(data.get('resistance'))}",

        f"Structure: "
        f"{data.get('structureBreak')}",

        "",

        "━━━━━━━━━━━━━━━━━━",
        "🎯 LUMI SETUP",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"Decision: {decision}",

        f"Evidence score: "
        f"{data.get('confidence', 0)}/100",
    ]

    if setup != "NO_TRADE":

        lines.extend(
            [

                f"Entry: "
                f"{money(data.get('entryPrice'))}",

                f"Stop Loss: "
                f"{money(data.get('stopLoss'))}",

                f"TP1: "
                f"{money(data.get('takeProfit1'))}",

                f"TP2: "
                f"{money(data.get('takeProfit2'))}",

                "TP1 R:R: 1:1.5",

                "TP2 R:R: 1:2.5",
            ]
        )

    else:

        lines.append(
            "No executable setup is currently validated."
        )

    reasoning = data.get(
        "reasoning",
        [],
    )

    if reasoning:

        lines.extend(
            [
                "",
                "🧠 LUMI REASONING",
            ]
        )

        for item in reasoning[:8]:

            lines.append(
                f"• {item}"
            )

    lines.extend(
        [

            "",

            "━━━━━━━━━━━━━━━━━━",
            "🛡 SAFETY",
            "━━━━━━━━━━━━━━━━━━",

            "",

            "Independent confirmation: NOT AVAILABLE",

            "Automatic trade alerts: BLOCKED",

            "",

            "⚠️ Market intelligence only.",

            "Not guaranteed financial advice.",

            "Verify executable broker pricing before acting.",
        ]
    )

    return "\n".join(lines)


# ============================================================
# TRADE ALERT
# ============================================================

def format_trade_alert(data):

    side = (
        "BUY"
        if data.get("setupType") == "BUY_SETUP"
        else "SELL"
    )

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    return "\n".join(
        [

            "🚨 LUMI TRADE ALERT",

            "",

            f"🟡 {data.get('symbol')}",

            f"{icon} {side} SETUP VALIDATED",

            "",

            f"Entry: "
            f"{money(data.get('entryPrice'))}",

            f"Stop Loss: "
            f"{money(data.get('stopLoss'))}",

            f"TP1: "
            f"{money(data.get('takeProfit1'))}",

            f"TP2: "
            f"{money(data.get('takeProfit2'))}",

            "",

            f"Evidence score: "
            f"{data.get('confidence')}/100",

            "",

            "⚠️ Verify broker pricing and risk before acting.",
        ]
    )


# ============================================================
# SEND ALERT
# ============================================================

async def send_alert(
    application,
    chat_id,
    data,
):

    if not validated_alert(data):
        return

    alert_key = (
        f"{data.get('symbol')}:"
        f"{data.get('setupType')}:"
        f"{data.get('entryPrice')}:"
        f"{data.get('confidence')}"
    )

    if not alert_is_allowed(
        chat_id,
        alert_key,
    ):
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=format_trade_alert(data),
    )

    save_alert(
        chat_id,
        alert_key,
    )


# ============================================================
# AUTOMATIC MARKET MONITOR
# ============================================================

async def market_monitor(
    context: ContextTypes.DEFAULT_TYPE,
):

    users = get_alert_users()

    if not users:
        return

    for symbol in MARKETS:

        try:

            data = get_market(symbol)

            if not validated_alert(data):
                continue

            for chat_id in users:

                try:

                    await send_alert(
                        context.application,
                        chat_id,
                        data,
                    )

                except Exception:

                    logger.exception(
                        "Telegram alert failed."
                    )

        except Exception:

            logger.exception(
                "Market monitor failed for %s",
                symbol,
            )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    register_chat(chat_id)

    await update.message.reply_text(

        "🤖 LUMI AI 2.0\n\n"

        "Market intelligence engine is online.\n\n"

        "📡 Monitoring 14 markets.\n"
        "🧠 Technical analysis active.\n"
        "🛡 Safety gate active.\n"
        "🚨 Alerts require independent confirmation.\n\n"

        "Use /help for commands."
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        "🤖 LUMI AI 2.0\n\n"

        "MARKETS\n"
        "/gold — XAUUSD\n"
        "/market SYMBOL\n\n"

        "SIGNALS\n"
        "/signal\n"
        "/signal XAUUSD\n"
        "/signal NAS100\n\n"

        "ALERTS\n"
        "/alerts\n"
        "/alerts on\n"
        "/alerts off\n\n"

        "SYSTEM\n"
        "/status\n"
        "/help"
    )


# ============================================================
# /MARKET
# ============================================================

async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_chat(
        update.effective_chat.id
    )

    symbol = (
        context.args[0].upper()
        if context.args
        else "XAUUSD"
    )

    if symbol not in MARKETS:

        await update.message.reply_text(

            "⚠️ Unsupported market.\n\n"

            "Use /help to see supported markets."
        )

        return

    await update.message.reply_text(

        f"🧠 Lumi is analysing {symbol} "
        "using direct market data..."
    )

    try:

        data = get_market(symbol)

        await update.message.reply_text(
            format_market_report(data)
        )

    except Exception as error:

        logger.exception(
            "Market analysis failed."
        )

        await update.message.reply_text(

            "⚠️ Lumi could not retrieve "
            "reliable market data.\n\n"

            "No market signal will be invented.\n\n"

            f"System detail: {error}"
        )


# ============================================================
# /GOLD
# ============================================================

async def gold_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    context.args = ["XAUUSD"]

    await market_command(
        update,
        context,
    )


# ============================================================
# /SIGNAL
# ============================================================

async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    symbol = (
        context.args[0].upper()
        if context.args
        and context.args[0].upper() in MARKETS
        else "XAUUSD"
    )

    context.args = [symbol]

    await market_command(
        update,
        context,
    )


# ============================================================
# /ALERTS
# ============================================================

async def alerts_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    register_chat(chat_id)

    option = (
        context.args[0].lower()
        if context.args
        else None
    )

    if option == "on":

        set_alert_status(
            chat_id,
            True,
        )

        await update.message.reply_text(

            "🚨 LUMI ALERTS: ON\n\n"

            "Lumi will monitor the supported markets.\n\n"

            "Automatic alerts still require:\n"

            "• Global live data\n"
            "• Independent confirmation\n"
            "• Evidence score ≥ 80\n"
            "• No validation failure"
        )

        return

    if option == "off":

        set_alert_status(
            chat_id,
            False,
        )

        await update.message.reply_text(
            "🔕 LUMI ALERTS: OFF"
        )

        return

    status = (
        "ON 🟢"
        if get_alert_status(chat_id)
        else "OFF 🔴"
    )

    await update.message.reply_text(

        "🚨 LUMI ALERT STATUS\n\n"

        f"Automatic alerts: {status}\n"

        f"Markets monitored: {len(MARKETS)}\n"

        f"Check interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n"

        f"Cooldown: "
        f"{ALERT_COOLDOWN_SECONDS // 60} minutes\n\n"

        "Alert gate:\n"

        "GLOBAL_LIVE\n"
        "+ INDEPENDENT_CONFIRMED\n"
        f"+ SCORE ≥ {MIN_SIGNAL_CONFIDENCE}"
    )


# ============================================================
# /STATUS
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_chat(
        update.effective_chat.id
    )

    await update.message.reply_text(

        "🟢 LUMI AI 2.0 STATUS\n\n"

        "Telegram: ACTIVE\n"
        "Market Engine: ACTIVE\n"
        "Direct Market Source: ACTIVE\n"
        "Independent Validation: NOT CONNECTED\n"
        "Signal Safety Gate: ACTIVE\n\n"

        f"Supported Markets: {len(MARKETS)}\n"

        f"Monitoring: "
        f"{MONITOR_INTERVAL // 60} minutes\n"

        f"Minimum Evidence: "
        f"{MIN_SIGNAL_CONFIDENCE}/100\n\n"

        "Automatic trade execution: OFF\n"

        "Automatic alerts: BLOCKED until "
        "independent confirmation.\n\n"

        "No signal will be invented."
    )


# ============================================================
# NATURAL LANGUAGE
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if (
        not update.message
        or not update.message.text
    ):
        return

    text = (
        update.message.text
        .strip()
        .lower()
    )

    if (
        "gold" in text
        or "xauusd" in text
        or "xau/usd" in text
    ):

        await gold_command(
            update,
            context,
        )

        return

    for symbol in MARKETS:

        if symbol.lower() in text.replace(
            "/",
            "",
        ):

            context.args = [symbol]

            await market_command(
                update,
                context,
            )

            return

    if (
        "signal" in text
        or "trade setup" in text
        or "buy or sell" in text
    ):

        await signal_command(
            update,
            context,
        )

        return

    await update.message.reply_text(

        "🤖 Lumi AI 2.0 is online.\n\n"

        "Try:\n"

        "/gold\n"
        "/market XAUUSD\n"
        "/market NAS100\n"
        "/signal\n"
        "/alerts\n"
        "/status\n"
        "/help"
    )


# ============================================================
# STARTUP
# ============================================================

async def post_init(
    application: Application,
):

    initialize_database()

    if application.job_queue is None:

        logger.error(
            "JobQueue unavailable. "
            "Check python-telegram-bot[job-queue]."
        )

        return

    application.job_queue.run_repeating(

        market_monitor,

        interval=MONITOR_INTERVAL,

        first=10,

        name="lumi_market_monitor",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable "
            "is missing."
        )

    application = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "gold",
            gold_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "market",
            market_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "signal",
            signal_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "alerts",
            alerts_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info(
        "Lumi AI 2.0 starting."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
