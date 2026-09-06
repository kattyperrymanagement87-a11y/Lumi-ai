import os
import logging
from datetime import datetime

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
# START COMMAND
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 LUMI AI\n\n"
        "Lumi is online and ready.\n\n"
        "Available commands:\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n\n"
        "Market intelligence:\n"
        "• XAUUSD / Gold\n"
        "• Market price monitoring\n"
        "• Trend observation\n"
        "• Momentum observation\n"
        "• Structured market analysis\n\n"
        "⚠️ Lumi does not guarantee profits."
    )

    await update.message.reply_text(message)


# =========================
# HELP COMMAND
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🤖 LUMI AI COMMANDS\n\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n\n"
        "Try sending:\n"
        "• Gold\n"
        "• XAUUSD\n"
        "• Analyze XAUUSD\n"
        "• Gold analysis"
    )

    await update.message.reply_text(message)


# =========================
# GOLD DATA
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
        raise ValueError("Yahoo Finance returned no market data.")

    result = result[0]

    meta = result.get("meta", {})
    indicators = result.get("indicators", {})
    quote = indicators.get("quote", [])

    if not quote:
        raise ValueError("No price candles were returned.")

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

    if len(clean_closes) < 2:
        raise ValueError("Not enough price data for analysis.")

    return {
        "price": clean_closes[-1],
        "previous_price": clean_closes[-2],
        "high": max(clean_highs) if clean_highs else None,
        "low": min(clean_lows) if clean_lows else None,
        "currency": meta.get("currency", "USD"),
    }


# =========================
# GOLD ANALYSIS
# =========================

def analyze_gold():
    market = get_gold_data()

    price = market["price"]
    previous_price = market["previous_price"]

    change = price - previous_price

    if previous_price != 0:
        change_percent = (change / previous_price) * 100
    else:
        change_percent = 0

    if change > 0:
        direction = "🟢 Short-term upward movement"
    elif change < 0:
        direction = "🔴 Short-term downward movement"
    else:
        direction = "🟡 Short-term movement is flat"

    message = (
        "🟡 XAUUSD / GOLD CENTER\n\n"
        f"Current reference price: ${price:,.2f}\n"
        f"Previous candle: ${previous_price:,.2f}\n"
        f"Change: {change:+.2f} ({change_percent:+.2f}%)\n\n"
        f"Market observation:\n"
        f"{direction}\n\n"
    )

    if market["high"] is not None:
        message += f"Period high: ${market['high']:,.2f}\n"

    if market["low"] is not None:
        message += f"Period low: ${market['low']:,.2f}\n"

    message += (
        "\n⚠️ This is market intelligence, not a guaranteed "
        "trading signal or financial advice."
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

    if any(keyword in text for keyword in gold_keywords):
        await update.message.reply_text(
            "🟡 Lumi is analyzing XAUUSD...\n\n"
            "Checking price, structure and recent movement..."
        )

        try:
            analysis = analyze_gold()
            await update.message.reply_text(analysis)

        except Exception as error:
            logger.exception("Gold data error: %s", error)

            await update.message.reply_text(
                "⚠️ Lumi could not retrieve reliable XAUUSD data "
                "right now.\n\n"
                "No market signal will be invented.\n\n"
                f"System detail: {error}"
            )

        return

    await update.message.reply_text(
        "🤖 Lumi is online.\n\n"
        "I can currently help with XAUUSD / Gold market "
        "intelligence.\n\n"
        "Try sending:\n"
        "• Gold\n"
        "• XAUUSD\n"
        "• Analyze XAUUSD\n\n"
        "Use /help to see commands."
    )


# =========================
# MAIN
# =========================

def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment variable is missing."
        )

    application = Application.builder().token(TOKEN).build()

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("Lumi AI is starting...")

    application.run_polling()


if __name__ == "__main__":
    main()
