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

# GC=F = Gold futures market-data proxy
MARKET_SYMBOL = "GC=F"

MARKET_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    + MARKET_SYMBOL
)

DATA_INTERVAL = "1h"
DATA_RANGE = "1mo"

# Background market check every 5 minutes
MONITOR_INTERVAL = 300

# Prevent repeated identical alerts
ALERT_COOLDOWN_SECONDS = 1800

DATABASE_FILE = "lumi.db"

# Signal thresholds
MIN_SIGNAL_CONFIDENCE = 80

# ATR-based risk management
STOP_ATR_MULTIPLIER = 1.20
TP1_R_MULTIPLIER = 1.50
TP2_R_MULTIPLIER = 2.50


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

    result = (
        data.get("chart", {})
        .get("result")
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

    # Keep OHLC values aligned by candle.
    clean_closes = []
    clean_highs = []
    clean_lows = []

    candle_count = min(
        len(closes),
        len(highs),
        len(lows),
    )

    for index in range(candle_count):

        close = closes[index]
        high = highs[index]
        low = lows[index]

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        clean_closes.append(
            float(close)
        )

        clean_highs.append(
            float(high)
        )

        clean_lows.append(
            float(low)
        )

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

    start = len(closes) - period

    for index in range(
        start,
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

    if atr is None or atr <= 0:
        raise ValueError(
            "Unable to calculate reliable ATR."
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

    # EMA 9 / EMA 21

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

    # EMA 21 / EMA 50

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

    analysis = {
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

    # Add the trade setup
    analysis["setup"] = build_trade_setup(
        analysis
    )

    return analysis


# ==================================================
# TRADE SETUP ENGINE
# ==================================================

def build_trade_setup(analysis):

    price = analysis["price"]
    atr = analysis["atr"]

    confidence = analysis["confidence"]

    bullish_score = analysis["bullish_score"]
    bearish_score = analysis["bearish_score"]

    rsi = analysis["rsi"]

    momentum_5 = analysis["momentum_5"]
    momentum_20 = analysis["momentum_20"]

    support = analysis["support"]
    resistance = analysis["resistance"]

    # ----------------------------------------------
    # Default = NO TRADE
    # ----------------------------------------------

    setup = {
        "type": "NO_TRADE",
        "label": "🟡 NO TRADE",
        "entry": None,
        "stop_loss": None,
        "tp1": None,
        "tp2": None,
        "risk": None,
        "rr1": None,
        "rr2": None,
        "confidence": confidence,
        "reasons": [],
    }

    # ----------------------------------------------
    # BUY CONDITIONS
    # ----------------------------------------------

    bullish_setup = (
        bullish_score >= 5
        and bullish_score > bearish_score
        and confidence >= MIN_SIGNAL_CONFIDENCE
        and momentum_5 > 0.15
        and momentum_20 > 0
        and rsi >= 50
        and rsi < 70
    )

    # ----------------------------------------------
    # SELL CONDITIONS
    # ----------------------------------------------

    bearish_setup = (
        bearish_score >= 5
        and bearish_score > bullish_score
        and confidence >= MIN_SIGNAL_CONFIDENCE
        and momentum_5 < -0.15
        and momentum_20 < 0
        and rsi <= 50
        and rsi > 30
    )

    # ----------------------------------------------
    # BUY SETUP
    # ----------------------------------------------

    if bullish_setup:

        entry = price

        risk = atr * STOP_ATR_MULTIPLIER

        stop_loss = entry - risk

        tp1 = entry + (
            risk * TP1_R_MULTIPLIER
        )

        tp2 = entry + (
            risk * TP2_R_MULTIPLIER
        )

        # Avoid calling it a clean setup if
        # immediate resistance is too close.
        room_to_resistance = (
            resistance - entry
        )

        reasons = [
            "bullish EMA structure",
            "bullish momentum alignment",
            "RSI supports bullish momentum",
            "medium-term movement is positive",
        ]

        if room_to_resistance < risk:

            reasons.append(
                "resistance is too close for a clean setup"
            )

            return {
                **setup,
                "reasons": reasons,
            }

        return {
            "type": "BUY",
            "label": "🟢 BUY SETUP",
            "entry": entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "risk": risk,
            "rr1": TP1_R_MULTIPLIER,
            "rr2": TP2_R_MULTIPLIER,
            "confidence": confidence,
            "reasons": reasons,
        }

    # ----------------------------------------------
    # SELL SETUP
    # ----------------------------------------------

    if bearish_setup:

        entry = price

        risk = atr * STOP_ATR_MULTIPLIER

        stop_loss = entry + risk

        tp1 = entry - (
            risk * TP1_R_MULTIPLIER
        )

        tp2 = entry - (
            risk * TP2_R_MULTIPLIER
        )

        # Avoid calling it a clean setup if
        # immediate support is too close.
        room_to_support = (
            entry - support
        )

        reasons = [
            "bearish EMA structure",
            "bearish momentum alignment",
            "RSI supports bearish momentum",
            "medium-term movement is negative",
        ]

        if room_to_support < risk:

            reasons.append(
                "support is too close for a clean setup"
            )

            return {
                **setup,
                "reasons": reasons,
            }

        return {
            "type": "SELL",
            "label": "🔴 SELL SETUP",
            "entry": entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "risk": risk,
            "rr1": TP1_R_MULTIPLIER,
            "rr2": TP2_R_MULTIPLIER,
            "confidence": confidence,
            "reasons": reasons,
        }

    # ----------------------------------------------
    # NO TRADE REASONS
    # ----------------------------------------------

    no_trade_reasons = []

    if confidence < MIN_SIGNAL_CONFIDENCE:
        no_trade_reasons.append(
            "indicator agreement is below signal threshold"
        )

    if abs(
        bullish_score - bearish_score
    ) < 2:
        no_trade_reasons.append(
            "bullish and bearish forces are too close"
        )

    if (
        45 < rsi < 55
        and abs(momentum_5) <= 0.15
    ):
        no_trade_reasons.append(
            "momentum is too weak"
        )

    if not no_trade_reasons:
        no_trade_reasons.append(
            "conditions do not meet Lumi's trade criteria"
        )

    setup["reasons"] = no_trade_reasons

    return setup


# ==================================================
# FORMAT MARKET REPORT
# ==================================================

def format_analysis(analysis):

    price = analysis["price"]

    previous = analysis["previous_price"]

    change = (
        price - previous
    )

    change_percent = (
        change / previous
    ) * 100

    reasons = "\n• ".join(
        analysis["reasons"][:4]
    )

    setup = analysis["setup"]

    setup_type = setup["type"]

    message = (
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

        "━━━━━━━━━━━━━━━━━━\n"
        "🎯 LUMI TRADE SETUP\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    # ----------------------------------------------
    # BUY / SELL SETUP
    # ----------------------------------------------

    if setup_type in (
        "BUY",
        "SELL",
    ):

        message += (
            f"{setup['label']}\n\n"

            f"🎯 Entry: "
            f"${setup['entry']:,.2f}\n"

            f"🛑 Stop Loss: "
            f"${setup['stop_loss']:,.2f}\n"

            f"💰 TP1: "
            f"${setup['tp1']:,.2f}\n"

            f"💰 TP2: "
            f"${setup['tp2']:,.2f}\n\n"

            f"📐 Risk distance: "
            f"${setup['risk']:,.2f}\n"

            f"📊 TP1 R:R: "
            f"1:{setup['rr1']:.1f}\n"

            f"📊 TP2 R:R: "
            f"1:{setup['rr2']:.1f}\n\n"

            f"🎯 Setup confidence: "
            f"{setup['confidence']:.0f}%\n\n"

            "🧠 Setup reasoning:\n"
            + "\n".join(
                f"• {reason}"
                for reason in setup["reasons"]
            )
        )

    else:

        message += (
            "🟡 NO TRADE\n\n"

            "Lumi does not currently see a "
            "sufficiently aligned setup.\n\n"

            "Why:\n"
            + "\n".join(
                f"• {reason}"
                for reason in setup["reasons"]
            )
        )

    message += (
        "\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🧠 LUMI REASONING\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"• {reasons}\n\n"

        "⚠️ IMPORTANT\n"
        "This is automated market analysis, "
        "not guaranteed financial advice. "
        "The confidence score measures indicator "
        "agreement, NOT probability of profit. "
        "Always verify the live price and your "
        "broker's XAUUSD price before acting."
    )

    return message


# ==================================================
# DIRECT SIGNAL REPORT
# ==================================================

def format_signal(analysis):

    setup = analysis["setup"]

    price = analysis["price"]

    if setup["type"] == "NO_TRADE":

        return (
            "🟡 LUMI XAUUSD SIGNAL\n\n"

            "🟡 NO TRADE\n\n"

            f"Current price: ${price:,.2f}\n"
            f"Bias: {analysis['bias']}\n"
            f"Indicator agreement: "
            f"{analysis['confidence']:.0f}%\n"
            f"RSI: {analysis['rsi']:.1f}\n"
            f"Momentum (5h): "
            f"{analysis['momentum_5']:+.2f}%\n\n"

            "Lumi is waiting for stronger "
            "confirmation.\n\n"

            "Reason:\n"
            + "\n".join(
                f"• {reason}"
                for reason in setup["reasons"]
            )
            + "\n\n"

            "No trade is preferable to forcing "
            "a weak setup."
        )

    return (
        "🚨 LUMI XAUUSD SIGNAL\n\n"

        f"{setup['label']}\n\n"

        f"💰 Current price: "
        f"${price:,.2f}\n\n"

        f"🎯 Entry: "
        f"${setup['entry']:,.2f}\n"

        f"🛑 Stop Loss: "
        f"${setup['stop_loss']:,.2f}\n"

        f"💰 TP1: "
        f"${setup['tp1']:,.2f}\n"

        f"💰 TP2: "
        f"${setup['tp2']:,.2f}\n\n"

        f"📊 Confidence: "
        f"{setup['confidence']:.0f}%\n"

        f"📐 TP1 R:R: "
        f"1:{setup['rr1']:.1f}\n"

        f"📐 TP2 R:R: "
        f"1:{setup['rr2']:.1f}\n\n"

        "🧠 Confirmation:\n"
        + "\n".join(
            f"• {reason}"
            for reason in setup["reasons"]
        )
        + "\n\n"

        "⚠️ Confidence means indicator agreement, "
        "not probability of profit. Verify live "
        "broker pricing before taking any trade."
    )


# ==================================================
# ALERT ENGINE
# ==================================================

def detect_alert(analysis):

    setup = analysis["setup"]

    if setup["type"] == "BUY":

        return (
            "BUY_SETUP",
            "🟢 BUY setup detected.",
        )

    if setup["type"] == "SELL":

        return (
            "SELL_SETUP",
            "🔴 SELL setup detected.",
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

    setup = analysis["setup"]

    message = (
        "🚨 LUMI TRADE ALERT\n\n"

        "🟡 XAUUSD / GOLD\n\n"

        f"{alert_message}\n\n"

        f"🎯 Setup: "
        f"{setup['label']}\n"

        f"💰 Entry: "
        f"${setup['entry']:,.2f}\n"

        f"🛑 Stop Loss: "
        f"${setup['stop_loss']:,.2f}\n"

        f"💰 TP1: "
        f"${setup['tp1']:,.2f}\n"

        f"💰 TP2: "
        f"${setup['tp2']:,.2f}\n\n"

        f"📊 Confidence: "
        f"{setup['confidence']:.0f}%\n"

        f"📐 TP1 R:R: "
        f"1:{setup['rr1']:.1f}\n"

        f"📐 TP2 R:R: "
        f"1:{setup['rr2']:.1f}\n\n"

        f"📈 Trend: "
        f"{analysis['trend']}\n"

        f"🧠 Bias: "
        f"{analysis['bias']}\n"

        f"RSI: "
        f"{analysis['rsi']:.1f}\n"

        f"Momentum: "
        f"{analysis['momentum_5']:+.2f}%\n\n"

        "Confirmation:\n"
        + "\n".join(
            f"• {reason}"
            for reason in setup["reasons"]
        )
        + "\n\n"

        "⚠️ Automated market alert. "
        "Confidence is indicator agreement, "
        "not probability of profit. "
        "Verify the live broker price before "
        "taking any action."
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
        "🚨 Automatic trade alerts are ON.\n\n"

        "Commands:\n"
        "/gold - Full Gold analysis\n"
        "/signal - Current trade setup\n"
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
        "/gold - Full XAUUSD analysis\n"
        "/signal - Current trade setup\n"
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
        "momentum, volatility, support, "
        "resistance and trade conditions..."
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


async def signal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_chat(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🧠 Lumi is checking the current "
        "XAUUSD trade conditions..."
    )

    try:

        analysis = analyze_market()

        signal = format_signal(
            analysis
        )

        await update.message.reply_text(
            signal
        )

    except Exception as error:

        logger.exception(
            "Signal analysis error: %s",
            error,
        )

        await update.message.reply_text(
            "⚠️ Lumi could not retrieve "
            "reliable market data.\n\n"

            "No signal will be invented.\n\n"

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
                "🚨 Lumi automatic trade alerts: ON\n\n"

                "Lumi will monitor XAUUSD every "
                f"{MONITOR_INTERVAL // 60} minutes "
                "and notify you when a defined "
                "BUY or SELL setup appears."
            )

            return

        if option == "off":

            set_alert_status(
                chat_id,
                False,
            )

            await update.message.reply_text(
                "🔕 Lumi automatic trade alerts: OFF"
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
        f"{ALERT_COOLDOWN_SECONDS // 60} minutes\n\n"

        "Alert types:\n"
        "🟢 BUY setup\n"
        "🔴 SELL setup"
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

        "Gold engine:\n"
        "• EMA 9 / 21 / 50\n"
        "• RSI 14\n"
        "• Momentum analysis\n"
        "• ATR volatility\n"
        "• Support & resistance\n"
        "• BUY/SELL setup engine\n"
        "• ATR-based Stop Loss\n"
        "• TP1 / TP2 calculation\n"
        "• Automatic alerts\n\n"

        "Signal rule:\n"
        f"Minimum agreement: "
        f"{MIN_SIGNAL_CONFIDENCE:.0f}%"
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
        "gold signal",
        "gold setup",
        "gold trade",
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

    signal_keywords = [
        "signal",
        "trade setup",
        "entry",
        "buy or sell",
        "buy sell",
    ]

    if any(
        keyword in text
        for keyword in signal_keywords
    ):

        await signal_command(
            update,
            context,
        )

        return

    await update.message.reply_text(
        "🤖 Lumi is online.\n\n"

        "Try:\n"
        "/gold\n"
        "/signal\n"
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
