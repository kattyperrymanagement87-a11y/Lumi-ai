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


# ==================================================
# CONFIGURATION
# ==================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKET_SYMBOL = "GC=F"

MARKET_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + MARKET_SYMBOL
)

DATA_INTERVAL = "1h"
DATA_RANGE = "1mo"

MONITOR_INTERVAL = 300
ALERT_COOLDOWN_SECONDS = 1800

DATABASE_FILE = "lumi.db"


# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("LumiAI")


# ==================================================
# DATABASE
# ==================================================

def get_db_connection():
    return sqlite3.connect(DATABASE_FILE)


def initialize_database():

    connection = get_db_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            alerts_enabled INTEGER DEFAULT 1,
            created_at TEXT,
            last_alert_type TEXT,
            last_alert_time TEXT
        )
        """
    )

    connection.commit()
    connection.close()

    logger.info("Database initialized.")


def register_chat(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO users (
            chat_id,
            alerts_enabled,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            chat_id,
            1,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    connection.commit()
    connection.close()


def set_alert_status(chat_id, enabled):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE users
        SET alerts_enabled = ?
        WHERE chat_id = ?
        """,
        (
            1 if enabled else 0,
            chat_id,
        ),
    )

    connection.commit()
    connection.close()


def get_alert_status(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT alerts_enabled
        FROM users
        WHERE chat_id = ?
        """,
        (chat_id,),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return False

    return bool(result[0])


def get_alert_users():

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT chat_id
        FROM users
        WHERE alerts_enabled = 1
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [row[0] for row in rows]


def get_last_alert(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            last_alert_type,
            last_alert_time
        FROM users
        WHERE chat_id = ?
        """,
        (chat_id,),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None, None

    return result[0], result[1]


def save_alert(chat_id, alert_type):

    connection = get_db_connection()
    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor.execute(
        """
        UPDATE users
        SET
            last_alert_type = ?,
            last_alert_time = ?
        WHERE chat_id = ?
        """,
        (
            alert_type,
            now,
            chat_id,
        ),
    )

    connection.commit()
    connection.close()


# ==================================================
# MARKET DATA
# ==================================================

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

    result = data.get(
        "chart",
        {}
    ).get(
        "result"
    )

    if not result:
        raise ValueError(
            "Yahoo Finance returned no market data."
        )

    result = result[0]

    indicators = result.get(
        "indicators",
        {}
    )

    quote = indicators.get(
        "quote",
        []
    )

    if not quote:
        raise ValueError(
            "No candle data returned."
        )

    candles = quote[0]

    closes = candles.get(
        "close",
        []
    )

    highs = candles.get(
        "high",
        []
    )

    lows = candles.get(
        "low",
        []
    )

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

    if len(clean_closes) < 60:
        raise ValueError(
            "Not enough candles for analysis."
        )

    return {
        "price": clean_closes[-1],
        "closes": clean_closes,
        "highs": clean_highs,
        "lows": clean_lows,
    }


# ==================================================
# EMA
# ==================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(
        values[:period]
    ) / period

    for value in values[period:]:

        ema = (
            (value - ema)
            * multiplier
        ) + ema

    return ema


# ==================================================
# RSI
# ==================================================

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    changes = []

    for index in range(
        len(closes) - period,
        len(closes)
    ):

        change = (
            closes[index]
            - closes[index - 1]
        )

        changes.append(change)

    gains = [
        change
        if change > 0
        else 0
        for change in changes
    ]

    losses = [
        abs(change)
        if change < 0
        else 0
        for change in changes
    ]

    average_gain = (
        sum(gains) / period
    )

    average_loss = (
        sum(losses) / period
    )

    if average_loss == 0:
        return 100.0

    rs = (
        average_gain
        / average_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


# ==================================================
# ATR / VOLATILITY
# ==================================================

def calculate_atr(
    highs,
    lows,
    closes,
    period=14,
):

    if len(closes) < period + 1:
        return None

    true_ranges = []

    start = len(closes) - period

    for index in range(
        start,
        len(closes)
    ):

        high = highs[index]
        low = lows[index]

        previous_close = (
            closes[index - 1]
        )

        true_range = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            ),
        )

        true_ranges.append(
            true_range
        )

    return (
        sum(true_ranges)
        / period
    )


# ==================================================
# MARKET ANALYSIS ENGINE
# ==================================================

def analyze_market():

    market = get_gold_data()

    price = market["price"]
    closes = market["closes"]
    highs = market["highs"]
    lows = market["lows"]

    previous_price = closes[-2]

    # ----------------------------------------------
    # EMA
    # ----------------------------------------------

    ema_9 = calculate_ema(
        closes,
        9,
    )

    ema_21 = calculate_ema(
        closes,
        21,
    )

    ema_50 = calculate_ema(
        closes,
        50,
    )

    # ----------------------------------------------
    # RSI
    # ----------------------------------------------

    rsi = calculate_rsi(
        closes,
        14,
    )

    # ----------------------------------------------
    # ATR
    # ----------------------------------------------

    atr = calculate_atr(
        highs,
        lows,
        closes,
        14,
    )

    volatility_percent = (
        atr / price
    ) * 100

    # ----------------------------------------------
    # MOMENTUM
    # ----------------------------------------------

    momentum_5 = (
        (
            price
            - closes[-6]
        )
        / closes[-6]
    ) * 100

    momentum_20 = (
        (
            price
            - closes[-21]
        )
        / closes[-21]
    ) * 100

    # ----------------------------------------------
    # SUPPORT / RESISTANCE
    # ----------------------------------------------

    recent_lows = lows[-30:]
    recent_highs = highs[-30:]

    support = min(
        recent_lows
    )

    resistance = max(
        recent_highs
    )

    # ----------------------------------------------
    # SCORING
    # ----------------------------------------------

    bullish_score = 0
    bearish_score = 0

    bullish_reasons = []
    bearish_reasons = []

    # Price vs EMA 9

    if price > ema_9:

        bullish_score += 1

        bullish_reasons.append(
            "price above EMA 9"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "price below EMA 9"
        )

    # EMA alignment

    if ema_9 > ema_21:

        bullish_score += 1

        bullish_reasons.append(
            "EMA 9 above EMA 21"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "EMA 9 below EMA 21"
        )

    # Medium trend

    if ema_21 > ema_50:

        bullish_score += 1

        bullish_reasons.append(
            "EMA 21 above EMA 50"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "EMA 21 below EMA 50"
        )

    # RSI

    if rsi >= 55:

        bullish_score += 1

        bullish_reasons.append(
            "RSI above bullish threshold"
        )

    elif rsi <= 45:

        bearish_score += 1

        bearish_reasons.append(
            "RSI below bearish threshold"
        )

    # Short momentum

    if momentum_5 > 0.15:

        bullish_score += 1

        bullish_reasons.append(
            "positive short-term momentum"
        )

    elif momentum_5 < -0.15:

        bearish_score += 1

        bearish_reasons.append(
            "negative short-term momentum"
        )

    # Medium momentum

    if momentum_20 > 0:

        bullish_score += 1

        bullish_reasons.append(
            "positive medium-term movement"
        )

    elif momentum_20 < 0:

        bearish_score += 1

        bearish_reasons.append(
            "negative medium-term movement"
        )

    # ----------------------------------------------
    # BIAS
    # ----------------------------------------------

    total_score = (
        bullish_score
        + bearish_score
    )

    if bullish_score > bearish_score:

        bias = "🟢 BULLISH"

    elif bearish_score > bullish_score:

        bias = "🔴 BEARISH"

    else:

        bias = "🟡 NEUTRAL"

    if total_score > 0:

        confidence = (
            max(
                bullish_score,
                bearish_score,
            )
            / total_score
        ) * 100

    else:

        confidence = 50

    # ----------------------------------------------
    # TREND
    # ----------------------------------------------

    if (
        price > ema_9
        and ema_9 > ema_21
        and ema_21 > ema_50
    ):

        trend = "🟢 Strong Bullish"

    elif (
        price < ema_9
        and ema_9 < ema_21
        and ema_21 < ema_50
    ):

        trend = "🔴 Strong Bearish"

    elif bullish_score > bearish_score:

        trend = "🟢 Bullish / Mixed"

    elif bearish_score > bullish_score:

        trend = "🔴 Bearish / Mixed"

    else:

        trend = "🟡 Ranging / Mixed"

    # ----------------------------------------------
    # MARKET CONDITION
    # ----------------------------------------------

    if volatility_percent > 1.5:

        market_condition = (
            "⚡ High volatility"
        )

    elif volatility_percent > 0.7:

        market_condition = (
            "📊 Moderate volatility"
        )

    else:

        market_condition = (
            "🟡 Low volatility"
        )

    # ----------------------------------------------
    # REASONS
    # ----------------------------------------------

    if bullish_score > bearish_score:

        reasons = bullish_reasons

    elif bearish_score > bullish_score:

        reasons = bearish_reasons

    else:

        reasons = [
            "indicators are not strongly aligned"
        ]

    return {
        "price": price,
        "previous_price": previous_price,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "rsi": rsi,
        "atr": atr,
        "volatility": volatility_percent,
        "momentum_5": momentum_5,
        "momentum_20": momentum_20,
        "support": support,
        "resistance": resistance,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "bias": bias,
        "confidence": confidence,
        "trend": trend,
        "market_condition": market_condition,
        "reasons": reasons,
    }


# ==================================================
# FORMAT MARKET REPORT
# ==================================================

def format_analysis(analysis):

    price = analysis["price"]

    previous = (
        analysis["previous_price"]
    )

    change = (
        price - previous
    )

    change_percent = (
        change / previous
    ) * 100

    reasons = "\n• ".join(
        analysis["reasons"][:4]
    )

    return (
        "🟡 XAUUSD / GOLD INTELLIGENCE\n\n"

        f"💰 Price: ${price:,.2f}\n"
        f"📈 Change: {change:+.2f} "
        f"({change_percent:+.2f}%)\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📊 TREND & STRUCTURE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Trend: {analysis['trend']}\n"
        f"Bias: {analysis['bias']}\n"
        f"Indicator agreement: "
        f"{analysis['confidence']:.0f}%\n\n"

        f"RSI: {analysis['rsi']:.1f}\n"
        f"Momentum (5h): "
        f"{analysis['momentum_5']:+.2f}%\n"
        f"Momentum (20h): "
        f"{analysis['momentum_20']:+.2f}%\n\n"

        f"Market condition: "
        f"{analysis['market_condition']}\n"
        f"ATR volatility: "
        f"{analysis['volatility']:.2f}%\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📍 KEY LEVELS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Support: "
        f"${analysis['support']:,.2f}\n"

        f"Resistance: "
        f"${analysis['resistance']:,.2f}\n\n"

        "🧠 Lumi reasoning:\n"
        f"• {reasons}\n\n"

        "⚠️ Indicator agreement is not a probability "
        "of profit. This is automated market analysis, "
        "not guaranteed financial advice."
    )


# ==================================================
# ALERT ENGINE
# ==================================================

def detect_alert(analysis):

    price = analysis["price"]

    confidence = (
        analysis["confidence"]
    )

    momentum = (
        analysis["momentum_5"]
    )

    rsi = analysis["rsi"]

    support = analysis["support"]

    resistance = analysis["resistance"]

    bias = analysis["bias"]

    # Strong bullish alignment

    if (
        "BULLISH" in bias
        and confidence >= 80
        and momentum > 0.20
    ):

        return (
            "STRONG_BULLISH",
            "🟢 Strong bullish indicator alignment."
        )

    # Strong bearish alignment

    if (
        "BEARISH" in bias
        and confidence >= 80
        and momentum < -0.20
    ):

        return (
            "STRONG_BEARISH",
            "🔴 Strong bearish indicator alignment."
        )

    # RSI extreme

    if rsi <= 30:

        return (
            "OVERSOLD",
            "🟢 RSI entered an oversold zone."
        )

    if rsi >= 70:

        return (
            "OVERBOUGHT",
            "🔴 RSI entered an overbought zone."
        )

    # Distance to support

    support_distance = (
        abs(price - support)
        / support
    ) * 100

    if support_distance <= 0.15:

        return (
            "NEAR_SUPPORT",
            "📍 Price is very close to support."
        )

    # Distance to resistance

    resistance_distance = (
        abs(resistance - price)
        / resistance
    ) * 100

    if resistance_distance <= 0.15:

        return (
            "NEAR_RESISTANCE",
            "📍 Price is very close to resistance."
        )

    return None


# ==================================================
# ALERT COOLDOWN
# ==================================================

def alert_is_allowed(
    chat_id,
    alert_type,
):

    previous_type, previous_time = (
        get_last_alert(chat_id)
    )

    if not previous_time:
        return True

    try:

        previous_time = (
            datetime.fromisoformat(
                previous_time
            )
        )

    except ValueError:

        return True

    now = datetime.now(
        timezone.utc
    )

    seconds_passed = (
        now - previous_time
    ).total_seconds()

    if (
        previous_type == alert_type
        and seconds_passed
        < ALERT_COOLDOWN_SECONDS
    ):

        return False

    return True


# ==================================================
# SEND MARKET ALERT
# ==================================================

async def send_alert(
    application,
    chat_id,
    alert_type,
    alert_message,
    analysis,
):

    if not alert_is_allowed(
        chat_id,
        alert_type,
    ):
        return

    message = (
        "🚨 LUMI MARKET ALERT\n\n"

        "🟡 XAUUSD / GOLD\n\n"

        f"{alert_message}\n\n"

        f"💰 Price: "
        f"${analysis['price']:,.2f}\n"

        f"📊 Trend: "
        f"{analysis['trend']}\n"

        f"🧠 Bias: "
        f"{analysis['bias']}\n"

        f"🎯 Indicator agreement: "
        f"{analysis['confidence']:.0f}%\n"

        f"📈 Momentum: "
        f"{analysis['momentum_5']:+.2f}%\n"

        f"RSI: "
        f"{analysis['rsi']:.1f}\n\n"

        f"📍 Support: "
        f"${analysis['support']:,.2f}\n"

        f"📍 Resistance: "
        f"${analysis['resistance']:,.2f}\n\n"

        "⚠️ Automated market observation. "
        "Not a guaranteed trade signal."
    )

    await application.bot.send_message(
        chat_id=chat_id,
        text=message,
    )

    save_alert(
        chat_id,
        alert_type,
    )


# ==================================================
# BACKGROUND MARKET MONITOR
# ==================================================

async def market_monitor(context):

    application = (
        context.application
    )

    logger.info(
        "Running scheduled market check."
    )

    try:

        users = get_alert_users()

        if not users:
            return

        analysis = analyze_market()

        alert = detect_alert(
            analysis
        )

        if not alert:
            return

        alert_type, alert_message = alert

        for chat_id in users:

            try:

                await send_alert(
                    application,
                    chat_id,
                    alert_type,
                    alert_message,
                    analysis,
                )

            except Exception as error:

                logger.exception(
                    "Failed to alert %s: %s",
                    chat_id,
                    error,
                )

    except Exception as error:

        logger.exception(
            "Market monitor error: %s",
            error,
        )


# ==================================================
# COMMANDS
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    await update.message.reply_text(
        "🤖 LUMI AI\n\n"
        "Lumi Intelligence is online.\n\n"
        "✅ This chat has been registered.\n"
        "🚨 Automatic market alerts are ON.\n\n"
        "Commands:\n"
        "/gold - Full Gold analysis\n"
        "/status - System status\n"
        "/alerts - Alert status\n"
        "/alerts on - Enable alerts\n"
        "/alerts off - Disable alerts\n"
        "/help - All commands\n\n"
        "⚠️ Lumi provides automated market "
        "intelligence, not guaranteed profits."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🤖 LUMI COMMAND CENTER\n\n"

        "MARKET:\n"
        "/gold - Analyze XAUUSD\n"
        "Gold - Analyze XAUUSD\n"
        "XAUUSD - Analyze XAUUSD\n\n"

        "ALERTS:\n"
        "/alerts - Check alert status\n"
        "/alerts on - Enable alerts\n"
        "/alerts off - Disable alerts\n\n"

        "SYSTEM:\n"
        "/status - Lumi status\n"
        "/start - Register chat\n"
        "/help - Show commands"
    )


async def gold_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_chat(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🟡 Lumi Intelligence is analyzing "
        "XAUUSD...\n\n"
        "Checking EMA alignment, RSI, "
        "momentum, volatility and "
        "key price levels..."
    )

    try:

        analysis = analyze_market()

        report = format_analysis(
            analysis
        )

        await update.message.reply_text(
            report
        )

    except Exception as error:

        logger.exception(
            "Gold analysis error: %s",
            error,
        )

        await update.message.reply_text(
            "⚠️ Lumi could not retrieve "
            "reliable market data.\n\n"
            "No analysis will be invented.\n\n"
            f"System detail: {error}"
        )


async def alerts_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    arguments = context.args

    if arguments:

        option = (
            arguments[0]
            .lower()
        )

        if option == "on":

            set_alert_status(
                chat_id,
                True,
            )

            await update.message.reply_text(
                "🚨 Lumi automatic alerts: ON\n\n"
                "Lumi will monitor XAUUSD and "
                "notify you when defined market "
                "conditions are detected."
            )

            return

        if option == "off":

            set_alert_status(
                chat_id,
                False,
            )

            await update.message.reply_text(
                "🔕 Lumi automatic alerts: OFF"
            )

            return

    status = get_alert_status(
        chat_id
    )

    status_text = (
        "ON 🟢"
        if status
        else "OFF 🔴"
    )

    await update.message.reply_text(
        "🚨 LUMI ALERT STATUS\n\n"
        f"Automatic alerts: {status_text}\n\n"
        f"Monitoring interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n"
        "Duplicate alert cooldown: "
        f"{ALERT_COOLDOWN_SECONDS // 60} minutes"
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    alerts = get_alert_status(
        chat_id
    )

    alert_status = (
        "Active 🟢"
        if alerts
        else "Disabled 🔴"
    )

    await update.message.reply_text(
        "🟢 LUMI SYSTEM STATUS\n\n"
        "Telegram Connection: Active\n"
        "Market Intelligence: Active\n"
        "XAUUSD Monitor: Active\n"
        f"Alerts: {alert_status}\n"
        f"Check interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n\n"
        "Core modules:\n"
        "• EMA trend engine\n"
        "• RSI analysis\n"
        "• Momentum analysis\n"
        "• Volatility measurement\n"
        "• Support & resistance\n"
        "• Alert detection"
    )


# ==================================================
# TEXT MESSAGE HANDLER
# ==================================================

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

        await gold_command(
            update,
            context,
        )

        return

    await update.message.reply_text(
        "🤖 Lumi is online.\n\n"
        "Try:\n"
        "/gold\n"
        "Gold\n"
        "XAUUSD\n\n"
        "Use /help for commands."
    )


# ==================================================
# STARTUP
# ==================================================

async def post_init(application):

    initialize_database()

    if application.job_queue is None:

        logger.error(
            "JobQueue is unavailable. "
            "Check requirements.txt."
        )

        return

    application.job_queue.run_repeating(
        market_monitor,
        interval=MONITOR_INTERVAL,
        first=10,
        name="xauusd_monitor",
    )

    logger.info(
        "Lumi background monitor enabled."
    )


# ==================================================
# MAIN
# ==================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment "
            "variable is missing."
        )

    application = (
        Application.builder()
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
            filters.TEXTimport os
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


# ==================================================
# CONFIGURATION
# ==================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

MARKET_SYMBOL = "GC=F"

MARKET_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + MARKET_SYMBOL
)

DATA_INTERVAL = "1h"
DATA_RANGE = "1mo"

MONITOR_INTERVAL = 300
ALERT_COOLDOWN_SECONDS = 1800

DATABASE_FILE = "lumi.db"


# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("LumiAI")


# ==================================================
# DATABASE
# ==================================================

def get_db_connection():
    return sqlite3.connect(DATABASE_FILE)


def initialize_database():

    connection = get_db_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            alerts_enabled INTEGER DEFAULT 1,
            created_at TEXT,
            last_alert_type TEXT,
            last_alert_time TEXT
        )
        """
    )

    connection.commit()
    connection.close()

    logger.info("Database initialized.")


def register_chat(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO users (
            chat_id,
            alerts_enabled,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (
            chat_id,
            1,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    connection.commit()
    connection.close()


def set_alert_status(chat_id, enabled):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        UPDATE users
        SET alerts_enabled = ?
        WHERE chat_id = ?
        """,
        (
            1 if enabled else 0,
            chat_id,
        ),
    )

    connection.commit()
    connection.close()


def get_alert_status(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT alerts_enabled
        FROM users
        WHERE chat_id = ?
        """,
        (chat_id,),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return False

    return bool(result[0])


def get_alert_users():

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT chat_id
        FROM users
        WHERE alerts_enabled = 1
        """
    )

    rows = cursor.fetchall()

    connection.close()

    return [row[0] for row in rows]


def get_last_alert(chat_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            last_alert_type,
            last_alert_time
        FROM users
        WHERE chat_id = ?
        """,
        (chat_id,),
    )

    result = cursor.fetchone()

    connection.close()

    if result is None:
        return None, None

    return result[0], result[1]


def save_alert(chat_id, alert_type):

    connection = get_db_connection()
    cursor = connection.cursor()

    now = datetime.now(
        timezone.utc
    ).isoformat()

    cursor.execute(
        """
        UPDATE users
        SET
            last_alert_type = ?,
            last_alert_time = ?
        WHERE chat_id = ?
        """,
        (
            alert_type,
            now,
            chat_id,
        ),
    )

    connection.commit()
    connection.close()


# ==================================================
# MARKET DATA
# ==================================================

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

    result = data.get(
        "chart",
        {}
    ).get(
        "result"
    )

    if not result:
        raise ValueError(
            "Yahoo Finance returned no market data."
        )

    result = result[0]

    indicators = result.get(
        "indicators",
        {}
    )

    quote = indicators.get(
        "quote",
        []
    )

    if not quote:
        raise ValueError(
            "No candle data returned."
        )

    candles = quote[0]

    closes = candles.get(
        "close",
        []
    )

    highs = candles.get(
        "high",
        []
    )

    lows = candles.get(
        "low",
        []
    )

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

    if len(clean_closes) < 60:
        raise ValueError(
            "Not enough candles for analysis."
        )

    return {
        "price": clean_closes[-1],
        "closes": clean_closes,
        "highs": clean_highs,
        "lows": clean_lows,
    }


# ==================================================
# EMA
# ==================================================

def calculate_ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    ema = sum(
        values[:period]
    ) / period

    for value in values[period:]:

        ema = (
            (value - ema)
            * multiplier
        ) + ema

    return ema


# ==================================================
# RSI
# ==================================================

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return None

    changes = []

    for index in range(
        len(closes) - period,
        len(closes)
    ):

        change = (
            closes[index]
            - closes[index - 1]
        )

        changes.append(change)

    gains = [
        change
        if change > 0
        else 0
        for change in changes
    ]

    losses = [
        abs(change)
        if change < 0
        else 0
        for change in changes
    ]

    average_gain = (
        sum(gains) / period
    )

    average_loss = (
        sum(losses) / period
    )

    if average_loss == 0:
        return 100.0

    rs = (
        average_gain
        / average_loss
    )

    return 100 - (
        100 / (1 + rs)
    )


# ==================================================
# ATR / VOLATILITY
# ==================================================

def calculate_atr(
    highs,
    lows,
    closes,
    period=14,
):

    if len(closes) < period + 1:
        return None

    true_ranges = []

    start = len(closes) - period

    for index in range(
        start,
        len(closes)
    ):

        high = highs[index]
        low = lows[index]

        previous_close = (
            closes[index - 1]
        )

        true_range = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            ),
        )

        true_ranges.append(
            true_range
        )

    return (
        sum(true_ranges)
        / period
    )


# ==================================================
# MARKET ANALYSIS ENGINE
# ==================================================

def analyze_market():

    market = get_gold_data()

    price = market["price"]
    closes = market["closes"]
    highs = market["highs"]
    lows = market["lows"]

    previous_price = closes[-2]

    # ----------------------------------------------
    # EMA
    # ----------------------------------------------

    ema_9 = calculate_ema(
        closes,
        9,
    )

    ema_21 = calculate_ema(
        closes,
        21,
    )

    ema_50 = calculate_ema(
        closes,
        50,
    )

    # ----------------------------------------------
    # RSI
    # ----------------------------------------------

    rsi = calculate_rsi(
        closes,
        14,
    )

    # ----------------------------------------------
    # ATR
    # ----------------------------------------------

    atr = calculate_atr(
        highs,
        lows,
        closes,
        14,
    )

    volatility_percent = (
        atr / price
    ) * 100

    # ----------------------------------------------
    # MOMENTUM
    # ----------------------------------------------

    momentum_5 = (
        (
            price
            - closes[-6]
        )
        / closes[-6]
    ) * 100

    momentum_20 = (
        (
            price
            - closes[-21]
        )
        / closes[-21]
    ) * 100

    # ----------------------------------------------
    # SUPPORT / RESISTANCE
    # ----------------------------------------------

    recent_lows = lows[-30:]
    recent_highs = highs[-30:]

    support = min(
        recent_lows
    )

    resistance = max(
        recent_highs
    )

    # ----------------------------------------------
    # SCORING
    # ----------------------------------------------

    bullish_score = 0
    bearish_score = 0

    bullish_reasons = []
    bearish_reasons = []

    # Price vs EMA 9

    if price > ema_9:

        bullish_score += 1

        bullish_reasons.append(
            "price above EMA 9"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "price below EMA 9"
        )

    # EMA alignment

    if ema_9 > ema_21:

        bullish_score += 1

        bullish_reasons.append(
            "EMA 9 above EMA 21"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "EMA 9 below EMA 21"
        )

    # Medium trend

    if ema_21 > ema_50:

        bullish_score += 1

        bullish_reasons.append(
            "EMA 21 above EMA 50"
        )

    else:

        bearish_score += 1

        bearish_reasons.append(
            "EMA 21 below EMA 50"
        )

    # RSI

    if rsi >= 55:

        bullish_score += 1

        bullish_reasons.append(
            "RSI above bullish threshold"
        )

    elif rsi <= 45:

        bearish_score += 1

        bearish_reasons.append(
            "RSI below bearish threshold"
        )

    # Short momentum

    if momentum_5 > 0.15:

        bullish_score += 1

        bullish_reasons.append(
            "positive short-term momentum"
        )

    elif momentum_5 < -0.15:

        bearish_score += 1

        bearish_reasons.append(
            "negative short-term momentum"
        )

    # Medium momentum

    if momentum_20 > 0:

        bullish_score += 1

        bullish_reasons.append(
            "positive medium-term movement"
        )

    elif momentum_20 < 0:

        bearish_score += 1

        bearish_reasons.append(
            "negative medium-term movement"
        )

    # ----------------------------------------------
    # BIAS
    # ----------------------------------------------

    total_score = (
        bullish_score
        + bearish_score
    )

    if bullish_score > bearish_score:

        bias = "🟢 BULLISH"

    elif bearish_score > bullish_score:

        bias = "🔴 BEARISH"

    else:

        bias = "🟡 NEUTRAL"

    if total_score > 0:

        confidence = (
            max(
                bullish_score,
                bearish_score,
            )
            / total_score
        ) * 100

    else:

        confidence = 50

    # ----------------------------------------------
    # TREND
    # ----------------------------------------------

    if (
        price > ema_9
        and ema_9 > ema_21
        and ema_21 > ema_50
    ):

        trend = "🟢 Strong Bullish"

    elif (
        price < ema_9
        and ema_9 < ema_21
        and ema_21 < ema_50
    ):

        trend = "🔴 Strong Bearish"

    elif bullish_score > bearish_score:

        trend = "🟢 Bullish / Mixed"

    elif bearish_score > bullish_score:

        trend = "🔴 Bearish / Mixed"

    else:

        trend = "🟡 Ranging / Mixed"

    # ----------------------------------------------
    # MARKET CONDITION
    # ----------------------------------------------

    if volatility_percent > 1.5:

        market_condition = (
            "⚡ High volatility"
        )

    elif volatility_percent > 0.7:

        market_condition = (
            "📊 Moderate volatility"
        )

    else:

        market_condition = (
            "🟡 Low volatility"
        )

    # ----------------------------------------------
    # REASONS
    # ----------------------------------------------

    if bullish_score > bearish_score:

        reasons = bullish_reasons

    elif bearish_score > bullish_score:

        reasons = bearish_reasons

    else:

        reasons = [
            "indicators are not strongly aligned"
        ]

    return {
        "price": price,
        "previous_price": previous_price,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "rsi": rsi,
        "atr": atr,
        "volatility": volatility_percent,
        "momentum_5": momentum_5,
        "momentum_20": momentum_20,
        "support": support,
        "resistance": resistance,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "bias": bias,
        "confidence": confidence,
        "trend": trend,
        "market_condition": market_condition,
        "reasons": reasons,
    }


# ==================================================
# FORMAT MARKET REPORT
# ==================================================

def format_analysis(analysis):

    price = analysis["price"]

    previous = (
        analysis["previous_price"]
    )

    change = (
        price - previous
    )

    change_percent = (
        change / previous
    ) * 100

    reasons = "\n• ".join(
        analysis["reasons"][:4]
    )

    return (
        "🟡 XAUUSD / GOLD INTELLIGENCE\n\n"

        f"💰 Price: ${price:,.2f}\n"
        f"📈 Change: {change:+.2f} "
        f"({change_percent:+.2f}%)\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📊 TREND & STRUCTURE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Trend: {analysis['trend']}\n"
        f"Bias: {analysis['bias']}\n"
        f"Indicator agreement: "
        f"{analysis['confidence']:.0f}%\n\n"

        f"RSI: {analysis['rsi']:.1f}\n"
        f"Momentum (5h): "
        f"{analysis['momentum_5']:+.2f}%\n"
        f"Momentum (20h): "
        f"{analysis['momentum_20']:+.2f}%\n\n"

        f"Market condition: "
        f"{analysis['market_condition']}\n"
        f"ATR volatility: "
        f"{analysis['volatility']:.2f}%\n\n"

        "━━━━━━━━━━━━━━━━━━\n"
        "📍 KEY LEVELS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"Support: "
        f"${analysis['support']:,.2f}\n"

        f"Resistance: "
        f"${analysis['resistance']:,.2f}\n\n"

        "🧠 Lumi reasoning:\n"
        f"• {reasons}\n\n"

        "⚠️ Indicator agreement is not a probability "
        "of profit. This is automated market analysis, "
        "not guaranteed financial advice."
    )


# ==================================================
# ALERT ENGINE
# ==================================================

def detect_alert(analysis):

    price = analysis["price"]

    confidence = (
        analysis["confidence"]
    )

    momentum = (
        analysis["momentum_5"]
    )

    rsi = analysis["rsi"]

    support = analysis["support"]

    resistance = analysis["resistance"]

    bias = analysis["bias"]

    # Strong bullish alignment

    if (
        "BULLISH" in bias
        and confidence >= 80
        and momentum > 0.20
    ):

        return (
            "STRONG_BULLISH",
            "🟢 Strong bullish indicator alignment."
        )

    # Strong bearish alignment

    if (
        "BEARISH" in bias
        and confidence >= 80
        and momentum < -0.20
    ):

        return (
            "STRONG_BEARISH",
            "🔴 Strong bearish indicator alignment."
        )

    # RSI extreme

    if rsi <= 30:

        return (
            "OVERSOLD",
            "🟢 RSI entered an oversold zone."
        )

    if rsi >= 70:

        return (
            "OVERBOUGHT",
            "🔴 RSI entered an overbought zone."
        )

    # Distance to support

    support_distance = (
        abs(price - support)
        / support
    ) * 100

    if support_distance <= 0.15:

        return (
            "NEAR_SUPPORT",
            "📍 Price is very close to support."
        )

    # Distance to resistance

    resistance_distance = (
        abs(resistance - price)
        / resistance
    ) * 100

    if resistance_distance <= 0.15:

        return (
            "NEAR_RESISTANCE",
            "📍 Price is very close to resistance."
        )

    return None


# ==================================================
# ALERT COOLDOWN
# ==================================================

def alert_is_allowed(
    chat_id,
    alert_type,
):

    previous_type, previous_time = (
        get_last_alert(chat_id)
    )

    if not previous_time:
        return True

    try:

        previous_time = (
            datetime.fromisoformat(
                previous_time
            )
        )

    except ValueError:

        return True

    now = datetime.now(
        timezone.utc
    )

    seconds_passed = (
        now - previous_time
    ).total_seconds()

    if (
        previous_type == alert_type
        and seconds_passed
        < ALERT_COOLDOWN_SECONDS
    ):

        return False

    return True


# ==================================================
# SEND MARKET ALERT
# ==================================================

async def send_alert(
    application,
    chat_id,
    alert_type,
    alert_message,
    analysis,
):

    if not alert_is_allowed(
        chat_id,
        alert_type,
    ):
        return

    message = (
        "🚨 LUMI MARKET ALERT\n\n"

        "🟡 XAUUSD / GOLD\n\n"

        f"{alert_message}\n\n"

        f"💰 Price: "
        f"${analysis['price']:,.2f}\n"

        f"📊 Trend: "
        f"{analysis['trend']}\n"

        f"🧠 Bias: "
        f"{analysis['bias']}\n"

        f"🎯 Indicator agreement: "
        f"{analysis['confidence']:.0f}%\n"

        f"📈 Momentum: "
        f"{analysis['momentum_5']:+.2f}%\n"

        f"RSI: "
        f"{analysis['rsi']:.1f}\n\n"

        f"📍 Support: "
        f"${analysis['support']:,.2f}\n"

        f"📍 Resistance: "
        f"${analysis['resistance']:,.2f}\n\n"

        "⚠️ Automated market observation. "
        "Not a guaranteed trade signal."
    )

    await application.bot.send_message(
        chat_id=chat_id,
        text=message,
    )

    save_alert(
        chat_id,
        alert_type,
    )


# ==================================================
# BACKGROUND MARKET MONITOR
# ==================================================

async def market_monitor(context):

    application = (
        context.application
    )

    logger.info(
        "Running scheduled market check."
    )

    try:

        users = get_alert_users()

        if not users:
            return

        analysis = analyze_market()

        alert = detect_alert(
            analysis
        )

        if not alert:
            return

        alert_type, alert_message = alert

        for chat_id in users:

            try:

                await send_alert(
                    application,
                    chat_id,
                    alert_type,
                    alert_message,
                    analysis,
                )

            except Exception as error:

                logger.exception(
                    "Failed to alert %s: %s",
                    chat_id,
                    error,
                )

    except Exception as error:

        logger.exception(
            "Market monitor error: %s",
            error,
        )


# ==================================================
# COMMANDS
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    await update.message.reply_text(
        "🤖 LUMI AI\n\n"
        "Lumi Intelligence is online.\n\n"
        "✅ This chat has been registered.\n"
        "🚨 Automatic market alerts are ON.\n\n"
        "Commands:\n"
        "/gold - Full Gold analysis\n"
        "/status - System status\n"
        "/alerts - Alert status\n"
        "/alerts on - Enable alerts\n"
        "/alerts off - Disable alerts\n"
        "/help - All commands\n\n"
        "⚠️ Lumi provides automated market "
        "intelligence, not guaranteed profits."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🤖 LUMI COMMAND CENTER\n\n"

        "MARKET:\n"
        "/gold - Analyze XAUUSD\n"
        "Gold - Analyze XAUUSD\n"
        "XAUUSD - Analyze XAUUSD\n\n"

        "ALERTS:\n"
        "/alerts - Check alert status\n"
        "/alerts on - Enable alerts\n"
        "/alerts off - Disable alerts\n\n"

        "SYSTEM:\n"
        "/status - Lumi status\n"
        "/start - Register chat\n"
        "/help - Show commands"
    )


async def gold_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_chat(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🟡 Lumi Intelligence is analyzing "
        "XAUUSD...\n\n"
        "Checking EMA alignment, RSI, "
        "momentum, volatility and "
        "key price levels..."
    )

    try:

        analysis = analyze_market()

        report = format_analysis(
            analysis
        )

        await update.message.reply_text(
            report
        )

    except Exception as error:

        logger.exception(
            "Gold analysis error: %s",
            error,
        )

        await update.message.reply_text(
            "⚠️ Lumi could not retrieve "
            "reliable market data.\n\n"
            "No analysis will be invented.\n\n"
            f"System detail: {error}"
        )


async def alerts_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    arguments = context.args

    if arguments:

        option = (
            arguments[0]
            .lower()
        )

        if option == "on":

            set_alert_status(
                chat_id,
                True,
            )

            await update.message.reply_text(
                "🚨 Lumi automatic alerts: ON\n\n"
                "Lumi will monitor XAUUSD and "
                "notify you when defined market "
                "conditions are detected."
            )

            return

        if option == "off":

            set_alert_status(
                chat_id,
                False,
            )

            await update.message.reply_text(
                "🔕 Lumi automatic alerts: OFF"
            )

            return

    status = get_alert_status(
        chat_id
    )

    status_text = (
        "ON 🟢"
        if status
        else "OFF 🔴"
    )

    await update.message.reply_text(
        "🚨 LUMI ALERT STATUS\n\n"
        f"Automatic alerts: {status_text}\n\n"
        f"Monitoring interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n"
        "Duplicate alert cooldown: "
        f"{ALERT_COOLDOWN_SECONDS // 60} minutes"
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    register_chat(chat_id)

    alerts = get_alert_status(
        chat_id
    )

    alert_status = (
        "Active 🟢"
        if alerts
        else "Disabled 🔴"
    )

    await update.message.reply_text(
        "🟢 LUMI SYSTEM STATUS\n\n"
        "Telegram Connection: Active\n"
        "Market Intelligence: Active\n"
        "XAUUSD Monitor: Active\n"
        f"Alerts: {alert_status}\n"
        f"Check interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n\n"
        "Core modules:\n"
        "• EMA trend engine\n"
        "• RSI analysis\n"
        "• Momentum analysis\n"
        "• Volatility measurement\n"
        "• Support & resistance\n"
        "• Alert detection"
    )


# ==================================================
# TEXT MESSAGE HANDLER
# ==================================================

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

        await gold_command(
            update,
            context,
        )

        return

    await update.message.reply_text(
        "🤖 Lumi is online.\n\n"
        "Try:\n"
        "/gold\n"
        "Gold\n"
        "XAUUSD\n\n"
        "Use /help for commands."
    )


# ==================================================
# STARTUP
# ==================================================

async def post_init(application):

    initialize_database()

    if application.job_queue is None:

        logger.error(
            "JobQueue is unavailable. "
            "Check requirements.txt."
        )

        return

    application.job_queue.run_repeating(
        market_monitor,
        interval=MONITOR_INTERVAL,
        first=10,
        name="xauusd_monitor",
    )

    logger.info(
        "Lumi background monitor enabled."
    )


# ==================================================
# MAIN
# ==================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN environment "
            "variable is missing."
        )

    application = (
        Application.builder()
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
            filters.TEXT
            & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info(
        "Lumi AI starting..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
            & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info(
        "Lumi AI starting..."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
