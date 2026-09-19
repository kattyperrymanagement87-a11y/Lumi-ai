import os
import math
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests
import pandas as pd
import numpy as np

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ============================================================
# LUMI AI 2.2
# XAUUSD / GOLD MARKET INTELLIGENCE ENGINE
# ============================================================

VERSION = "2.2.0"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Optional market API.
# Keep your key in Railway/Vercel environment variables.
MARKET_API_KEY = os.getenv("MARKET_API_KEY", "").strip()

# Optional Twelve Data key if you use Twelve Data.
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()

# Optional Alpha Vantage key.
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()

# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

SYMBOL = os.getenv("LUMI_SYMBOL", "XAUUSD").strip().upper()

# Yahoo's gold futures symbol is GC=F.
YAHOO_SYMBOL = os.getenv("YAHOO_SYMBOL", "GC=F").strip()

TIMEFRAME = os.getenv("LUMI_TIMEFRAME", "1h").strip()

MONITOR_INTERVAL = int(
    os.getenv("MONITOR_INTERVAL", "300")
)

ALERT_COOLDOWN = int(
    os.getenv("ALERT_COOLDOWN", "1800")
)

MIN_SIGNAL_CONFIDENCE = float(
    os.getenv("MIN_SIGNAL_CONFIDENCE", "80")
)

ATR_STOP_MULTIPLIER = float(
    os.getenv("ATR_STOP_MULTIPLIER", "1.20")
)

TP1_R_MULTIPLIER = float(
    os.getenv("TP1_R_MULTIPLIER", "1.50")
)

TP2_R_MULTIPLIER = float(
    os.getenv("TP2_R_MULTIPLIER", "2.50")
)

MAX_DATA_AGE_MINUTES = float(
    os.getenv("MAX_DATA_AGE_MINUTES", "90")
)

DATABASE = os.getenv(
    "LUMI_DATABASE",
    "lumi.db"
)

PORT = int(
    os.getenv("PORT", "8000")
)

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("lumi")


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Lumi AI",
    version=VERSION,
    description="Lumi AI XAUUSD Market Intelligence API",
)


@app.get("/")
async def root():
    return {
        "name": "Lumi AI",
        "version": VERSION,
        "status": "online",
        "symbol": SYMBOL,
        "engine": "Lumi 2.2",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "Lumi AI",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/status")
async def api_status():
    return JSONResponse({
        "status": "online",
        "version": VERSION,
        "market_symbol": SYMBOL,
        "reference_source": "Yahoo Finance GC=F",
        "market_api_configured": bool(MARKET_API_KEY),
        "telegram_configured": bool(BOT_TOKEN),
        "engine": "Lumi Intelligence 2.2",
    })


# ============================================================
# DATABASE
# ============================================================

def init_db():
    conn = sqlite3.connect(DATABASE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            last_seen TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            direction TEXT,
            entry REAL,
            stop_loss REAL,
            tp1 REAL,
            tp2 REAL,
            confidence REAL,
            timestamp TEXT
        )
    """)

    conn.commit()
    conn.close()


def register_user(update: Update):
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    username = ""
    first_name = ""

    if update.effective_user:
        username = update.effective_user.username or ""
        first_name = update.effective_user.first_name or ""

    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO users (
            chat_id,
            username,
            first_name,
            active,
            created_at,
            last_seen
        )
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(chat_id)
        DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            active = 1,
            last_seen = excluded.last_seen
    """, (
        chat_id,
        username,
        first_name,
        now,
        now,
    ))

    conn.commit()
    conn.close()


def get_active_users() -> List[int]:
    conn = sqlite3.connect(DATABASE)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT chat_id
        FROM users
        WHERE active = 1
    """)

    rows = cursor.fetchall()

    conn.close()

    return [int(row[0]) for row in rows]


def save_alert(
    chat_id: int,
    direction: str,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    confidence: float,
):
    conn = sqlite3.connect(DATABASE)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO alerts (
            chat_id,
            direction,
            entry,
            stop_loss,
            tp1,
            tp2,
            confidence,
            timestamp
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        chat_id,
        direction,
        entry,
        stop_loss,
        tp1,
        tp2,
        confidence,
        datetime.now(timezone.utc).isoformat(),
    ))

    conn.commit()
    conn.close()


# ============================================================
# MARKET DATA
# ============================================================

def fetch_yahoo_data(
    symbol: str = YAHOO_SYMBOL,
    interval: str = TIMEFRAME,
    range_period: str = "1mo",
) -> Optional[pd.DataFrame]:

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}"
    )

    params = {
        "interval": interval,
        "range": range_period,
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        response.raise_for_status()

        payload = response.json()

        result = payload.get("chart", {}).get("result")

        if not result:
            logger.warning("Yahoo returned no chart data.")
            return None

        result = result[0]

        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [])

        if not timestamps or not quote:
            return None

        quote = quote[0]

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
            ),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        })

        df = df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )

        df = df.sort_values(
            "timestamp"
        )

        df = df.reset_index(
            drop=True
        )

        return df

    except Exception as exc:
        logger.exception(
            "Yahoo data error: %s",
            exc,
        )

        return None


# ============================================================
# OPTIONAL EXTERNAL XAUUSD DATA
# ============================================================

def fetch_external_xauusd() -> Optional[Dict[str, Any]]:
    """
    Optional external feed.

    Configure MARKET_API_KEY if your selected market
    provider uses this endpoint.

    This function intentionally fails safely rather
    than pretending a price is live.
    """

    if not MARKET_API_KEY:
        return None

    # Generic endpoint can be overridden through environment.
    url = os.getenv(
        "MARKET_API_URL",
        ""
    ).strip()

    if not url:
        return None

    try:

        response = requests.get(
            url,
            params={
                "symbol": SYMBOL,
                "apikey": MARKET_API_KEY,
            },
            timeout=15,
        )

        response.raise_for_status()

        data = response.json()

        price = (
            data.get("price")
            or data.get("close")
            or data.get("last")
            or data.get("rate")
        )

        if price is None:
            return None

        return {
            "price": float(price),
            "source": "External Market API",
            "timestamp": datetime.now(timezone.utc),
            "fresh": True,
        }

    except Exception as exc:

        logger.warning(
            "External market API failed: %s",
            exc,
        )

        return None


# ============================================================
# DATA VALIDATION
# ============================================================

def calculate_data_age(
    timestamp: pd.Timestamp,
) -> float:

    now = pd.Timestamp.now(
        tz="UTC"
    )

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(
            "UTC"
        )

    age = (
        now - timestamp
    ).total_seconds() / 60

    return max(
        0,
        age,
    )


def validate_market_data(
    df: pd.DataFrame,
) -> Dict[str, Any]:

    if df is None or df.empty:
        return {
            "valid": False,
            "reason": "NO_DATA",
        }

    required = [
        "open",
        "high",
        "low",
        "close",
    ]

    for column in required:

        if column not in df.columns:
            return {
                "valid": False,
                "reason": f"MISSING_{column.upper()}",
            }

    latest_timestamp = df["timestamp"].iloc[-1]

    age_minutes = calculate_data_age(
        latest_timestamp
    )

    price = float(
        df["close"].iloc[-1]
    )

    if not math.isfinite(price):
        return {
            "valid": False,
            "reason": "INVALID_PRICE",
        }

    if price <= 0:
        return {
            "valid": False,
            "reason": "INVALID_PRICE",
        }

    fresh = age_minutes <= MAX_DATA_AGE_MINUTES

    return {
        "valid": True,
        "price": price,
        "timestamp": latest_timestamp,
        "age_minutes": age_minutes,
        "fresh": fresh,
        "status": (
            "LIVE_REFERENCE"
            if fresh
            else "REFERENCE_ONLY"
        ),
    }


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_rsi(
    series: pd.Series,
    period: int = 14,
) -> pd.Series:

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


def calculate_atr(
    df: pd.DataFrame,
    period: int = 14,
) -> pd.Series:

    previous_close = df["close"].shift(1)

    tr1 = (
        df["high"] -
        df["low"]
    )

    tr2 = (
        df["high"] -
        previous_close
    ).abs()

    tr3 = (
        df["low"] -
        previous_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1,
    ).max(axis=1)

    return true_range.rolling(
        period
    ).mean()


def add_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    df = df.copy()

    df["ema9"] = df["close"].ewm(
        span=9,
        adjust=False,
    ).mean()

    df["ema21"] = df["close"].ewm(
        span=21,
        adjust=False,
    ).mean()

    df["ema50"] = df["close"].ewm(
        span=50,
        adjust=False,
    ).mean()

    df["rsi"] = calculate_rsi(
        df["close"],
        14,
    )

    df["atr"] = calculate_atr(
        df,
        14,
    )

    df["momentum_5h"] = (
        df["close"].pct_change(5)
        * 100
    )

    df["momentum_20h"] = (
        df["close"].pct_change(20)
        * 100
    )

    df["support"] = (
        df["low"]
        .rolling(30)
        .min()
    )

    df["resistance"] = (
        df["high"]
        .rolling(30)
        .max()
    )

    return df


# ============================================================
# INTELLIGENCE ENGINE
# ============================================================

def clamp(
    value: float,
    minimum: float,
    maximum: float,
) -> float:

    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


def analyze_market(
    df: pd.DataFrame,
) -> Dict[str, Any]:

    if df is None or len(df) < 60:

        return {
            "ready": False,
            "reason": "INSUFFICIENT_DATA",
        }

    df = add_indicators(df)

    row = df.iloc[-1]

    price = float(
        row["close"]
    )

    ema9 = float(
        row["ema9"]
    )

    ema21 = float(
        row["ema21"]
    )

    ema50 = float(
        row["ema50"]
    )

    rsi = float(
        row["rsi"]
    )

    atr = float(
        row["atr"]
    )

    momentum5 = float(
        row["momentum_5h"]
    )

    momentum20 = float(
        row["momentum_20h"]
    )

    support = float(
        row["support"]
    )

    resistance = float(
        row["resistance"]
    )

    if not all(
        math.isfinite(x)
        for x in [
            price,
            ema9,
            ema21,
            ema50,
            rsi,
            atr,
            momentum5,
            momentum20,
            support,
            resistance,
        ]
    ):

        return {
            "ready": False,
            "reason": "INDICATOR_ERROR",
        }

    bullish_points = 0.0
    bearish_points = 0.0

    reasons_long = []
    reasons_short = []

    # --------------------------------------------------------
    # EMA structure
    # --------------------------------------------------------

    if ema9 > ema21:
        bullish_points += 15
        reasons_long.append(
            "EMA 9 above EMA 21"
        )
    else:
        bearish_points += 15
        reasons_short.append(
            "EMA 9 below EMA 21"
        )

    if ema21 > ema50:
        bullish_points += 15
        reasons_long.append(
            "EMA 21 above EMA 50"
        )
    else:
        bearish_points += 15
        reasons_short.append(
            "EMA 21 below EMA 50"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if 50 <= rsi <= 70:
        bullish_points += 12
        reasons_long.append(
            "RSI supports bullish momentum"
        )

    elif 30 <= rsi < 50:
        bearish_points += 12
        reasons_short.append(
            "RSI supports bearish momentum"
        )

    elif rsi > 70:
        bearish_points += 8
        reasons_short.append(
            "RSI is elevated"
        )

    elif rsi < 30:
        bullish_points += 8
        reasons_long.append(
            "RSI is deeply oversold"
        )

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    if momentum5 > 0:
        bullish_points += 10
        reasons_long.append(
            "5-candle momentum positive"
        )
    else:
        bearish_points += 10
        reasons_short.append(
            "5-candle momentum negative"
        )

    if momentum20 > 0:
        bullish_points += 10
        reasons_long.append(
            "20-candle momentum positive"
        )
    else:
        bearish_points += 10
        reasons_short.append(
            "20-candle momentum negative"
        )

    # --------------------------------------------------------
    # Price vs EMA50
    # --------------------------------------------------------

    if price > ema50:
        bullish_points += 10
        reasons_long.append(
            "Price above EMA 50"
        )
    else:
        bearish_points += 10
        reasons_short.append(
            "Price below EMA 50"
        )

    # --------------------------------------------------------
    # Market structure
    # --------------------------------------------------------

    if price > support:
        bullish_points += 4

    if price < resistance:
        bearish_points += 4

    # --------------------------------------------------------
    # Determine direction
    # --------------------------------------------------------

    total_direction_points = (
        bullish_points +
        bearish_points
    )

    if total_direction_points <= 0:
        return {
            "ready": False,
            "reason": "NO_DIRECTION",
        }

    if bullish_points > bearish_points:

        direction = "BUY"

        confidence = (
            bullish_points /
            total_direction_points
        ) * 100

        reasons = reasons_long

    elif bearish_points > bullish_points:

        direction = "SELL"

        confidence = (
            bearish_points /
            total_direction_points
        ) * 100

        reasons = reasons_short

    else:

        return {
            "ready": True,
            "signal": "NO_TRADE",
            "confidence": 50,
            "price": price,
            "reason": "BALANCED_MARKET",
        }

    confidence = clamp(
        confidence,
        0,
        100,
    )

    # --------------------------------------------------------
    # Require meaningful trend agreement
    # --------------------------------------------------------

    signal = (
        direction
        if confidence >= MIN_SIGNAL_CONFIDENCE
        else "NO_TRADE"
    )

    # --------------------------------------------------------
    # Entry / SL / TP
    # --------------------------------------------------------

    entry = price

    risk = atr * ATR_STOP_MULTIPLIER

    if risk <= 0:
        return {
            "ready": False,
            "reason": "INVALID_ATR",
        }

    if direction == "BUY":

        stop_loss = entry - risk

        tp1 = entry + (
            risk * TP1_R_MULTIPLIER
        )

        tp2 = entry + (
            risk * TP2_R_MULTIPLIER
        )

    else:

        stop_loss = entry + risk

        tp1 = entry - (
            risk * TP1_R_MULTIPLIER
        )

        tp2 = entry - (
            risk * TP2_R_MULTIPLIER
        )

    return {
        "ready": True,
        "signal": signal,
        "direction": direction,
        "confidence": round(
            confidence,
            1,
        ),
        "price": round(
            price,
            2,
        ),
        "entry": round(
            entry,
            2,
        ),
        "stop_loss": round(
            stop_loss,
            2,
        ),
        "tp1": round(
            tp1,
            2,
        ),
        "tp2": round(
            tp2,
            2,
        ),
        "atr": round(
            atr,
            2,
        ),
        "rsi": round(
            rsi,
            1,
        ),
        "momentum5": round(
            momentum5,
            3,
        ),
        "momentum20": round(
            momentum20,
            3,
        ),
        "support": round(
            support,
            2,
        ),
        "resistance": round(
            resistance,
            2,
        ),
        "ema9": round(
            ema9,
            2,
        ),
        "ema21": round(
            ema21,
            2,
        ),
        "ema50": round(
            ema50,
            2,
        ),
        "reasons": reasons[:5],
    }


# ============================================================
# FULL LUMI INTELLIGENCE
# ============================================================

def get_lumi_analysis() -> Dict[str, Any]:

    # Try external source first.
    external = fetch_external_xauusd()

    # Yahoo remains the technical-analysis reference feed.
    df = fetch_yahoo_data()

    if df is None:

        return {
            "ok": False,
            "error": "MARKET_DATA_UNAVAILABLE",
            "message": (
                "Lumi could not retrieve market data."
            ),
        }

    validation = validate_market_data(
        df
    )

    if not validation["valid"]:

        return {
            "ok": False,
            "error": validation["reason"],
        }

    # Add technical intelligence.
    analysis = analyze_market(
        df
    )

    if not analysis.get("ready"):

        return {
            "ok": False,
            "error": analysis.get(
                "reason",
                "ANALYSIS_FAILED",
            ),
        }

    # --------------------------------------------------------
    # Price source logic
    # --------------------------------------------------------

    if external:

        market_price = external["price"]
        price_source = external["source"]

    else:

        market_price = validation["price"]
        price_source = "Yahoo Finance (GC=F)"

    # IMPORTANT:
    # If Yahoo is stale and no independent live source exists,
    # Lumi must not pretend the price is live.
    data_status = (
        "LIVE"
        if external
        else validation["status"]
    )

    independent_validation = (
        "AVAILABLE"
        if external
        else "UNAVAILABLE"
    )

    return {
        "ok": True,
        "version": VERSION,
        "symbol": SYMBOL,
        "price": round(
            market_price,
            2,
        ),
        "analysis_price": analysis["price"],
        "data_status": data_status,
        "source": price_source,
        "validation": independent_validation,
        "quote_age_minutes": round(
            validation["age_minutes"],
            1,
        ),
        "fresh": validation["fresh"],
        "signal": analysis.get(
            "signal",
            "NO_TRADE",
        ),
        "direction": analysis.get(
            "direction",
            "NONE",
        ),
        "confidence": analysis.get(
            "confidence",
            0,
        ),
        "entry": analysis.get(
            "entry"
        ),
        "stop_loss": analysis.get(
            "stop_loss"
        ),
        "tp1": analysis.get(
            "tp1"
        ),
        "tp2": analysis.get(
            "tp2"
        ),
        "atr": analysis.get(
            "atr"
        ),
        "rsi": analysis.get(
            "rsi"
        ),
        "momentum5": analysis.get(
            "momentum5"
        ),
        "momentum20": analysis.get(
            "momentum20"
        ),
        "support": analysis.get(
            "support"
        ),
        "resistance": analysis.get(
            "resistance"
        ),
        "ema9": analysis.get(
            "ema9"
        ),
        "ema21": analysis.get(
            "ema21"
        ),
        "ema50": analysis.get(
            "ema50"
        ),
        "reasons": analysis.get(
            "reasons",
            [],
        ),
    }


# ============================================================
# TELEGRAM FORMATTING
# ============================================================

def format_analysis(
    data: Dict[str, Any],
) -> str:

    if not data.get("ok"):

        return (
            "🟡 <b>LUMI XAUUSD INTELLIGENCE</b>\n\n"
            "⚠️ Market analysis unavailable.\n\n"
            f"Reason: <code>{data.get('error')}</code>"
        )

    signal = data.get(
        "signal",
        "NO_TRADE",
    )

    if signal == "BUY":
        signal_text = "🟢 BUY SETUP"

    elif signal == "SELL":
        signal_text = "🔴 SELL SETUP"

    else:
        signal_text = "⚪ NO TRADE"

    freshness = (
        "🟢 FRESH"
        if data.get("fresh")
        else "🔴 STALE"
    )

    reasons = data.get(
        "reasons",
        [],
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons
    )

    return (
        "🟡 <b>LUMI XAUUSD INTELLIGENCE 2.2</b>\n\n"

        f"💰 <b>Market price:</b> "
        f"${data['price']:,.2f}\n"

        f"📡 <b>Data status:</b> "
        f"{data['data_status']}\n"

        f"🌐 <b>Source:</b> "
        f"{data['source']}\n"

        f"🔎 <b>Validation:</b> "
        f"{data['validation']}\n"

        f"⏱ <b>Quote age:</b> "
        f"{data['quote_age_minutes']} min\n"

        f"📊 <b>Data freshness:</b> "
        f"{freshness}\n\n"

        f"🎯 <b>Signal:</b> "
        f"{signal_text}\n"

        f"📈 <b>Confidence:</b> "
        f"{data['confidence']:.1f}%\n\n"

        f"💵 <b>Entry:</b> "
        f"${data['entry']:,.2f}\n"

        f"🛑 <b>Stop Loss:</b> "
        f"${data['stop_loss']:,.2f}\n"

        f"🎯 <b>TP1:</b> "
        f"${data['tp1']:,.2f}\n"

        f"🎯 <b>TP2:</b> "
        f"${data['tp2']:,.2f}\n\n"

        f"📐 <b>ATR:</b> "
        f"{data['atr']:.2f}\n"

        f"📊 <b>RSI:</b> "
        f"{data['rsi']:.1f}\n"

        f"📈 <b>EMA 9:</b> "
        f"{data['ema9']:,.2f}\n"

        f"📈 <b>EMA 21:</b> "
        f"{data['ema21']:,.2f}\n"

        f"📈 <b>EMA 50:</b> "
        f"{data['ema50']:,.2f}\n\n"

        f"🟢 <b>Support:</b> "
        f"${data['support']:,.2f}\n"

        f"🔴 <b>Resistance:</b> "
        f"${data['resistance']:,.2f}\n\n"

        f"<b>Lumi reasoning:</b>\n"
        f"{reason_text or 'No strong confirmation.'}\n\n"

        "⚠️ <i>Lumi provides market intelligence, "
        "not guaranteed predictions or financial advice.</i>"
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    await update.message.reply_text(
        (
            "🟡 <b>Lumi AI 2.2</b>\n\n"
            "Welcome to Lumi AI.\n\n"
            "I monitor XAUUSD and analyze market "
            "structure, momentum, EMA, RSI, ATR, "
            "support and resistance.\n\n"
            "Use /gold for the latest intelligence.\n"
            "Use /status for system status.\n"
            "Use /help for available commands.\n\n"
            "⚠️ Lumi does not guarantee market outcomes."
        ),
        parse_mode="HTML",
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    await update.message.reply_text(
        (
            "🟡 <b>LUMI AI 2.2 COMMANDS</b>\n\n"
            "/start — Start Lumi\n"
            "/gold — XAUUSD intelligence\n"
            "/status — System status\n"
            "/help — Show commands\n\n"
            "Lumi analyzes market conditions using "
            "technical indicators and only reports "
            "a trade setup when its configured "
            "confidence threshold is reached."
        ),
        parse_mode="HTML",
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    external = bool(
        MARKET_API_KEY
    )

    await update.message.reply_text(
        (
            "🟡 <b>LUMI AI STATUS</b>\n\n"
            "🟢 Lumi AI: Online\n"
            "🟢 Telegram: Connected\n"
            "🟢 Core Engine: Running\n"
            f"🟢 Engine Version: {VERSION}\n"
            f"📊 Symbol: {SYMBOL}\n"
            f"🌐 Yahoo Reference: Active\n"
            f"🔗 Independent API: "
            f"{'Configured' if external else 'Not configured'}\n"
            f"🎯 Minimum Confidence: "
            f"{MIN_SIGNAL_CONFIDENCE:.0f}%\n"
            f"⏱ Monitor Interval: "
            f"{MONITOR_INTERVAL}s\n"
        ),
        parse_mode="HTML",
    )


async def gold_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    register_user(update)

    message = await update.message.reply_text(
        "🟡 Lumi is analyzing XAUUSD..."
    )

    data = get_lumi_analysis()

    text = format_analysis(
        data
    )

    await message.edit_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ALERT ENGINE
# ============================================================

last_alert_signature = None
last_alert_time = 0.0


def create_alert_signature(
    data: Dict[str, Any],
) -> str:

    return (
        f"{data.get('direction')}:"
        f"{data.get('entry')}:"
        f"{data.get('stop_loss')}:"
        f"{data.get('tp1')}:"
        f"{data.get('tp2')}"
    )


def alert_is_allowed(
    data: Dict[str, Any],
) -> bool:

    global last_alert_signature
    global last_alert_time

    if not data.get("ok"):
        return False

    if data.get("signal") not in (
        "BUY",
        "SELL",
    ):
        return False

    if data.get("confidence", 0) < MIN_SIGNAL_CONFIDENCE:
        return False

    # Never alert from stale reference data.
    if not data.get("fresh"):
        return False

    signature = create_alert_signature(
        data
    )

    now = time.time()

    if signature == last_alert_signature:

        if (
            now - last_alert_time
            < ALERT_COOLDOWN
        ):
            return False

    last_alert_signature = signature
    last_alert_time = now

    return True


def format_alert(
    data: Dict[str, Any],
) -> str:

    if data["signal"] == "BUY":

        icon = "🟢"
        action = "BUY"

    else:

        icon = "🔴"
        action = "SELL"

    return (
        "🚨 <b>LUMI XAUUSD ALERT</b>\n\n"

        f"{icon} <b>{action} SETUP DETECTED</b>\n\n"

        f"💰 <b>Entry:</b> "
        f"${data['entry']:,.2f}\n"

        f"🛑 <b>Stop Loss:</b> "
        f"${data['stop_loss']:,.2f}\n"

        f"🎯 <b>TP1:</b> "
        f"${data['tp1']:,.2f}\n"

        f"🎯 <b>TP2:</b> "
        f"${data['tp2']:,.2f}\n\n"

        f"📊 <b>Confidence:</b> "
        f"{data['confidence']:.1f}%\n"

        f"📐 <b>ATR:</b> "
        f"{data['atr']:.2f}\n"

        f"📊 <b>RSI:</b> "
        f"{data['rsi']:.1f}\n\n"

        f"🟢 <b>Support:</b> "
        f"${data['support']:,.2f}\n"

        f"🔴 <b>Resistance:</b> "
        f"${data['resistance']:,.2f}\n\n"

        "⚠️ <i>This is market intelligence, "
        "not a guaranteed prediction or financial advice.</i>"
    )


async def monitor_market(
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        data = get_lumi_analysis()

        if not alert_is_allowed(
            data
        ):
            return

        alert = format_alert(
            data
        )

        users = get_active_users()

        for chat_id in users:

            try:

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=alert,
                    parse_mode="HTML",
                )

                save_alert(
                    chat_id,
                    data["direction"],
                    data["entry"],
                    data["stop_loss"],
                    data["tp1"],
                    data["tp2"],
                    data["confidence"],
                )

            except Exception as exc:

                logger.warning(
                    "Could not send alert to %s: %s",
                    chat_id,
                    exc,
                )

    except Exception as exc:

        logger.exception(
            "Monitor error: %s",
            exc,
        )


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

telegram_app = None


def create_telegram_app():

    global telegram_app

    if not BOT_TOKEN:

        logger.warning(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

        return None

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
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
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "gold",
            gold_command,
        )
    )

    if application.job_queue:

        application.job_queue.run_repeating(
            monitor_market,
            interval=MONITOR_INTERVAL,
            first=30,
            name="lumi_market_monitor",
        )

    else:

        logger.warning(
            "Job queue unavailable. "
            "Install python-telegram-bot[job-queue]."
        )

    telegram_app = application

    return application


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    init_db()

    logger.info(
        "========================================"
    )

    logger.info(
        "LUMI AI %s STARTING",
        VERSION,
    )

    logger.info(
        "Symbol: %s",
        SYMBOL,
    )

    logger.info(
        "Yahoo symbol: %s",
        YAHOO_SYMBOL,
    )

    logger.info(
        "Telegram configured: %s",
        bool(BOT_TOKEN),
    )

    logger.info(
        "External market API configured: %s",
        bool(MARKET_API_KEY),
    )

    logger.info(
        "========================================"
    )


# ============================================================
# LOCAL / RAILWAY ENTRYPOINT
# ============================================================

async def run_telegram():

    application = create_telegram_app()

    if application is None:

        logger.warning(
            "Telegram application not started."
        )

        return

    await application.initialize()

    await application.start()

    await application.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info(
        "Lumi Telegram polling started."
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    import asyncio

    init_db()

    if not BOT_TOKEN:

        logger.error(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    else:

        asyncio.run(
            run_telegram()
        )
