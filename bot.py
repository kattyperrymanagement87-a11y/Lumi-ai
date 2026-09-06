import os
import logging

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# LOGGING
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("LumiAI")


# =========================
# CONFIGURATION
# =========================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKET_SYMBOL = "GC=F"

MARKET_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + MARKET_SYMBOL
)

DATA_INTERVAL = "1h"
DATA_RANGE = "1mo"


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 LUMI AI\n\n"
        "Lumi is online and ready.\n\n"
        "Available commands:\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n\n"
        "🟡 MARKET INTELLIGENCE\n"
        "• XAUUSD / Gold\n"
        "• Trend analysis\n"
        "• Momentum analysis\n"
        "• Support & resistance\n"
        "• Market bias\n"
        "• Confidence assessment\n\n"
        "⚠️ Lumi does not guarantee profits."
    )

    await update.message.reply_text(message)


# =========================
# HELP
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 LUMI AI COMMANDS\n\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n\n"
        "Market analysis:\n"
        "• Gold\n"
        "• XAUUSD\n"
        "• Analyze XAUUSD\n"
        "• Gold analysis"
    )

    await update.message.reply_text(message)


# =========================
# GET GOLD DATA
# =========================

def get_gold_data():

    params = {
        "interval": DATA_INTERVAL,
        "range": DATA_RANGE,
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
    }

    response = requests.get(
        MARKET_URL,
        params=params,
        headers=headers,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    result = data.get("chart", {}).get("result")

    if not result:
        raise ValueError(
            "Yahoo Finance returned no market data."
        )

    result = result[0]

    meta = result.get("meta", {})

    indicators = result.get("indicators", {})
    quote = indicators.get("quote", [])

    if not quote:
        raise ValueError(
            "No price candles were returned."
        )

    candles = quote[0]

    closes = candles.get("close", [])
    highs = candles.get("high", [])
    lows = candles.get("low", [])

    clean_closes = [
        float(value)
        for value in closes
        if value is not None
    ]

    clean_highs = [
        float(value)
        for value in highs
        if value is not None
    ]

    clean_lows = [
        float(value)
        for value in lows
        if value is not None
    ]

    if len(clean_closes) < 20:
        raise ValueError(
            "Not enough candles for reliable analysis."
        )

    return {
        "price": clean_closes[-1],
        "closes": clean_closes,
        "highs": clean_highs,
        "lows": clean_lows,
        "currency": meta.get("currency", "USD"),
    }


# =========================
# MARKET ANALYSIS
# =========================

def analyze_gold():

    market = get_gold_data()

    price = market["price"]
    closes = market["closes"]
    highs = market["highs"]
    lows = market["lows"]

    # ---------------------------------
    # SHORT-TERM CHANGE
    # ---------------------------------

    previous_price = closes[-2]

    change = price - previous_price

    if previous_price != 0:
        change_percent = (
            change / previous_price
        ) * 100
    else:
        change_percent = 0


    # ---------------------------------
    # MOVING AVERAGES
    # ---------------------------------

    short_period = 10
    long_period = 20

    short_average = (
        sum(closes[-short_period:])
        / short_period
    )

    long_average = (
        sum(closes[-long_period:])
        / long_period
    )


    # ---------------------------------
    # TREND
    # ---------------------------------

    if short_average > long_average and price > short_average:

        trend = "🟢 Bullish"

    elif short_average < long_average and price < short_average:

        trend = "🔴 Bearish"

    else:

        trend = "🟡 Neutral / Mixed"


    # ---------------------------------
    # MOMENTUM
    # ---------------------------------

    momentum_lookback = 5

    momentum_start = closes[-momentum_lookback - 1]

    momentum_change = price - momentum_start

    if momentum_start != 0:

        momentum_percent = (
            momentum_change
            / momentum_start
        ) * 100

    else:

        momentum_percent = 0


    if momentum_percent > 0.20:

        momentum = "🟢 Positive"

    elif momentum_percent < -0.20:

        momentum = "🔴 Negative"

    else:

        momentum = "🟡 Weak / Flat"


    # ---------------------------------
    # SUPPORT & RESISTANCE
    # ---------------------------------

    recent_highs = highs[-20:]
    recent_lows = lows[-20:]

    resistance = max(recent_highs)
    support = min(recent_lows)


    # ---------------------------------
    # MARKET BIAS
    # ---------------------------------

    bullish_points = 0
    bearish_points = 0

    if price > short_average:
        bullish_points += 1
    else:
        bearish_points += 1

    if short_average > long_average:
        bullish_points += 1
    else:
        bearish_points += 1

    if momentum_percent > 0:
        bullish_points += 1
    elif momentum_percent < 0:
        bearish_points += 1


    if bullish_points > bearish_points:

        bias = "🟢 BULLISH"

    elif bearish_points > bullish_points:

        bias = "🔴 BEARISH"

    else:

        bias = "🟡 NEUTRAL"


    # ---------------------------------
    # CONFIDENCE
    # ---------------------------------

    total_points = (
        bullish_points
        + bearish_points
    )

    if total_points > 0:

        confidence = (
            max(
                bullish_points,
                bearish_points
            )
            / total_points
        ) * 100

    else:

        confidence = 50


    # ---------------------------------
    # STRUCTURE
    # ---------------------------------

    if price > resistance * 0.995:

        structure = (
            "Price is trading close to "
            "the recent resistance area."
        )

    elif price < support * 1.005:

        structure = (
            "Price is trading close to "
            "the recent support area."
        )

    else:

        structure = (
            "Price is trading within the "
            "recent support/resistance range."
        )


    # ---------------------------------
    # FINAL REPORT
    # ---------------------------------

    message = (
        "🟡 XAUUSD / GOLD CENTER\n\n"

        f"💰 Current price: ${price:,.2f}\n"
        f"📊 Previous candle: ${previous_price:,.2f}\n"
        f"📈 Change: {change:+.2f} "
        f"({change_percent:+.2f}%)\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📊 MARKET STRUCTURE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Trend: {trend}\n"
        f"Momentum: {momentum}\n\n"

        f"Support: ${support:,.2f}\n"
        f"Resistance: ${resistance:,.2f}\n\n"

        f"Structure:\n{structure}\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "🧠 LUMI MARKET BIAS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Bias: {bias}\n"
        f"Confidence: {confidence:.0f}%\n\n"

        "📌 Lumi observation:\n"
        f"Short-term momentum change: "
        f"{momentum_percent:+.2f}%\n\n"

        "⚠️ This is algorithmic market intelligence, "
        "not a guaranteed prediction or financial advice."
    )

    return message


# =========================
# MESSAGE HANDLER
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip().lower()

    gold_keywords = [
        "gold",
        "xauusd",
        "xau/usd",
        "analyze gold",
        "analyse gold",
        "analyze xauusd",
        "analyse xauusd",
        "gold analysis",
    ]

    if any(
        keyword in text
        for keyword in gold_keywords
    ):

        await update.message.reply_text(
            "🟡 Lumi intelligence:\n\n"
            "Lumi is analyzing XAUUSD...\n\n"
            "Checking price, trend, momentum, "
            "structure and key levels..."
        )

        try:

            analysis = analyze_gold()

            await update.message.reply_text(
                analysis
            )

        except Exception as error:

            logger.exception(
                "Gold analysis error: %s",
                error,
            )

            await update.message.reply_text(
                "⚠️ Lumi could not retrieve reliable "
                "XAUUSD data right now.\n\n"
                "No market signal will be invented.\n\n"
                f"System detail: {error}"
            )

        return


    await update.message.reply_text(
        "🤖 Lumi is online.\n\n"
        "I can currently analyze "
        "XAUUSD / Gold.\n\n"
        "Try:\n"
        "• Gold\n"
        "• XAUUSD\n"
        "• Analyze XAUUSD\n\n"
        "Use /help for commands."
    )


# =========================
# MAIN
# =========================

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
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    logger.info(
        "Lumi AI is starting..."
    )

    application.run_polling()


# =========================
# RUN
# =========================

if __name__ == "__main__":
    main()
