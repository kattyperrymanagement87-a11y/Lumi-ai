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
# LUMI AI — TELEGRAM MARKET INTELLIGENCE BOT
# ============================================================

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

LUMI_API_URL = os.getenv(
    "LUMI_API_URL",
    "https://54524a26-e9f6-42bd-8b63-89e6df7d3479.sandbox.floot.app",
).rstrip("/")

MONITOR_INTERVAL = 300
ALERT_COOLDOWN_SECONDS = 1800
DATABASE_FILE = "lumi.db"

# Automatic Telegram alerts require this minimum evidence score.
MIN_SIGNAL_CONFIDENCE = 80

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
# DUPLICATE ALERT PROTECTION
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
# LUMI MARKET API
# ============================================================

def get_market(symbol):

    symbol = symbol.upper()

    if symbol not in MARKETS:
        raise ValueError(
            f"Unsupported market: {symbol}"
        )

    response = requests.get(
        f"{LUMI_API_URL}/_api/market_intelligence",
        params={"symbol": symbol},
        timeout=25,
    )

    response.raise_for_status()

    data = response.json()

    if not data or "symbol" not in data:
        raise ValueError(
            "Lumi returned no market intelligence."
        )

    return data


# ============================================================
# FORMATTING
# ============================================================

def money(value):

    if value is None:
        return "—"

    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pct(value):

    if value is None:
        return "—"

    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"


def safe_number(value, default=0):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ============================================================
# HARD ALERT SAFETY GATE
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

    confidence = safe_number(
        data.get("confidence"),
        0,
    )

    # Lumi automatic alerts require ALL conditions.
    return (
        setup in ("BUY_SETUP", "SELL_SETUP")
        and market_status == "GLOBAL_LIVE"
        and validation == "INDEPENDENT_CONFIRMED"
        and confidence >= MIN_SIGNAL_CONFIDENCE
    )


# ============================================================
# FULL MARKET REPORT
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

        f"💰 Global market price: "
        f"{money(data.get('price'))}",

        f"🟢 Market data: "
        f"{data.get('marketDataStatus', 'REFERENCE_ONLY')}",

        f"🌐 Primary source: "
        f"{data.get('globalSource') or data.get('source') or '—'}",

        f"🔎 Secondary validation: "
        f"{data.get('priceValidation', 'INDEPENDENT_UNAVAILABLE')}",

        f"📏 Cross-feed deviation: "
        f"{pct(data.get('priceDeviationPercent'))}",

        f"⏱ Quote age: "
        f"{data.get('globalFreshnessSeconds', '—')}s",

        "",

        "━━━━━━━━━━━━━━━━━━",
        "📊 MARKET STRUCTURE",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"Bias: {data.get('bias', '—')}",
        f"Trend: {data.get('trend', '—')}",

        f"RSI 14: "
        f"{safe_number(data.get('rsi')):.1f}",

        f"Momentum 5h: "
        f"{pct(data.get('momentum5h'))}",

        f"Momentum 20h: "
        f"{pct(data.get('momentum20h'))}",

        f"Regime: "
        f"{data.get('regime', '—')}",

        f"Timeframe confirmation: "
        f"{data.get('timeframeConfirmation', '—')}%",

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
        f"{data.get('structureBreak', '—')}",

        "",

        "━━━━━━━━━━━━━━━━━━",
        "🎯 LUMI SETUP",
        "━━━━━━━━━━━━━━━━━━",

        "",

        f"Decision: {decision}",
    ]

    if setup != "NO_TRADE":

        entry = data.get("entryPrice")
        stop = data.get("stopLoss")

        if entry is not None and stop is not None:
            risk = abs(
                safe_number(entry)
                - safe_number(stop)
            )
        else:
            risk = None

        lines.extend(
            [
                f"Entry: {money(entry)}",

                f"Stop Loss: "
                f"{money(stop)}",

                f"TP1: "
                f"{money(data.get('takeProfit1'))}",

                f"TP2: "
                f"{money(data.get('takeProfit2'))}",

                f"Risk distance: "
                f"{money(risk)}",

                "TP1 R:R: 1:1.5",
                "TP2 R:R: 1:2.5",

                f"Evidence score: "
                f"{data.get('confidence', 0)}/100",
            ]
        )

    else:

        lines.extend(
            [
                f"Evidence score: "
                f"{data.get('confidence', 0)}/100",

                "No executable setup is currently validated.",
            ]
        )

    reasoning = data.get("reasoning") or []

    if reasoning:

        lines.extend(
            [
                "",
                "🧠 LUMI REASONING",
            ]
        )

        for item in reasoning[:8]:
            lines.append(f"• {item}")

    # XM stays separate from Lumi's global intelligence.
    lines.extend(
        [
            "",
            "━━━━━━━━━━━━━━━━━━",
            "⚙️ EXECUTION CHECK",
            "━━━━━━━━━━━━━━━━━━",

            "",

            f"XM status: "
            f"{data.get('brokerPriceStatus', 'UNAVAILABLE')}",

            f"XM symbol: "
            f"{data.get('brokerSymbol') or '—'}",

            f"XM bid: "
            f"{money(data.get('brokerBid'))}",

            f"XM ask: "
            f"{money(data.get('brokerAsk'))}",

            f"XM spread: "
            f"{money(data.get('brokerSpread'))}",
        ]
    )

    lines.extend(
        [
            "",
            "⚠️ Automated market intelligence, "
            "not guaranteed financial advice.",

            "Evidence score measures indicator "
            "agreement, not probability of profit.",

            "Verify the executable broker price "
            "before acting.",
        ]
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM TRADE ALERT
# ============================================================

def format_trade_alert(data):

    side = (
        "BUY"
        if data.get("setupType") == "BUY_SETUP"
        else "SELL"
    )

    icon = "🟢" if side == "BUY" else "🔴"

    lines = [

        "🚨 LUMI TRADE ALERT",

        "",

        f"🟡 {data.get('symbol', 'MARKET')}",

        f"{icon} {side} SETUP VALIDATED",

        "",

        f"💰 Global price: "
        f"{money(data.get('price'))}",

        f"🎯 Entry: "
        f"{money(data.get('entryPrice'))}",

        f"🛑 Stop Loss: "
        f"{money(data.get('stopLoss'))}",

        f"💰 TP1: "
        f"{money(data.get('takeProfit1'))}",

        f"💰 TP2: "
        f"{money(data.get('takeProfit2'))}",

        "",

        "📊 ANALYSIS",

        f"Evidence score: "
        f"{data.get('confidence', 0)}/100",

        f"Bias: "
        f"{data.get('bias', '—')}",

        f"Trend: "
        f"{data.get('trend', '—')}",

        f"RSI: "
        f"{safe_number(data.get('rsi')):.1f}",

        f"Momentum 5h: "
        f"{pct(data.get('momentum5h'))}",

        f"Momentum 20h: "
        f"{pct(data.get('momentum20h'))}",

        f"Support: "
        f"{money(data.get('support'))}",

        f"Resistance: "
        f"{money(data.get('resistance'))}",

        f"Regime: "
        f"{data.get('regime', '—')}",

        f"MTF confirmation: "
        f"{data.get('timeframeConfirmation', '—')}%",

        "",

        "🛡 VALIDATION",

        f"Global data: "
        f"{data.get('marketDataStatus')}",

        f"Secondary feed: "
        f"{data.get('priceValidation')}",

        f"Cross-feed deviation: "
        f"{pct(data.get('priceDeviationPercent'))}",

        f"Quote age: "
        f"{data.get('globalFreshnessSeconds', '—')}s",

        "",

        "⚙️ EXECUTION CHECK",

        f"XM status: "
        f"{data.get('brokerPriceStatus', 'UNAVAILABLE')}",

        f"XM bid: "
        f"{money(data.get('brokerBid'))}",

        f"XM ask: "
        f"{money(data.get('brokerAsk'))}",

        f"XM spread: "
        f"{money(data.get('brokerSpread'))}",

        "",

        "⚠️ Validated market-intelligence alert.",
        "This is not a guaranteed trade outcome.",
        "Verify executable broker pricing and risk before acting.",
    ]

    return "\n".join(lines)


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

            # Never alert on an invalid/stale/reference-only setup.
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
                        "Telegram alert delivery failed "
                        "for %s / %s",
                        symbol,
                        chat_id,
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

        "🤖 LUMI AI\n\n"

        "Global-market intelligence is online.\n\n"

        "🚨 Telegram is Lumi's alert channel.\n"
        "📡 Monitoring 14 supported markets.\n"
        "🛡 Alerts require GLOBAL_LIVE + "
        "independent confirmation.\n\n"

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

        "🤖 LUMI COMMAND CENTER\n\n"

        "MARKETS\n"
        "/gold — XAUUSD\n"
        "/market SYMBOL — any supported market\n\n"

        "SUPPORTED MARKETS\n"
        "XAUUSD\n"
        "BTCUSD\n"
        "ETHUSD\n"
        "SPX500\n"
        "NAS100\n"
        "DJ30\n"
        "GER40\n"
        "UK100\n"
        "JP225\n"
        "EURUSD\n"
        "GBPUSD\n"
        "USDJPY\n"
        "AUDUSD\n"
        "USDCAD\n\n"

        "SIGNALS\n"
        "/signal — current validated setup\n"
        "/signal NAS100\n"
        "/signal XAUUSD\n\n"

        "ALERTS\n"
        "/alerts — alert status\n"
        "/alerts on — enable alerts\n"
        "/alerts off — disable alerts\n\n"

        "SYSTEM\n"
        "/status — Lumi system status\n"
        "/help — commands"
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
            "Use /help to see Lumi's "
            "supported symbols."
        )

        return

    await update.message.reply_text(
        f"🧠 Lumi is validating {symbol} "
        "using global market data..."
    )

    try:

        data = get_market(symbol)

        await update.message.reply_text(
            format_market_report(data)
        )

    except Exception as error:

        logger.exception(
            "Market analysis failed"
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

            "🚨 LUMI TELEGRAM ALERTS: ON\n\n"

            "Lumi will monitor all supported "
            "markets every 5 minutes.\n\n"

            "Alerts require:\n"
            "• Global data LIVE\n"
            "• Independent confirmation\n"
            "• Evidence score ≥ 80\n"
            "• No divergence warning"
        )

        return

    if option == "off":

        set_alert_status(
            chat_id,
            False,
        )

        await update.message.reply_text(
            "🔕 LUMI TELEGRAM ALERTS: OFF"
        )

        return

    status = (
        "ON 🟢"
        if get_alert_status(chat_id)
        else "OFF 🔴"
    )

    await update.message.reply_text(

        "🚨 LUMI TELEGRAM ALERT STATUS\n\n"

        f"Automatic alerts: {status}\n"

        f"Markets monitored: "
        f"{len(MARKETS)}\n"

        f"Check interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n"

        f"Duplicate cooldown: "
        f"{ALERT_COOLDOWN_SECONDS // 60} minutes\n\n"

        "ALERT GATE\n"
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

        "🟢 LUMI SYSTEM STATUS\n\n"

        "Telegram Connection: Active\n"
        "Global Market Intelligence: Active\n"
        "Independent Validation: Active\n"
        "Signal Safety Gate: Active\n\n"

        f"Supported Markets: "
        f"{len(MARKETS)}\n"

        f"Monitoring Interval: "
        f"{MONITOR_INTERVAL // 60} minutes\n"

        f"Minimum Evidence Score: "
        f"{MIN_SIGNAL_CONFIDENCE}/100\n\n"

        "ALERT RULES\n"
        "• Global market data must be LIVE\n"
        "• Secondary feed must confirm\n"
        "• Divergence blocks alerts\n"
        "• Stale data blocks alerts\n"
        "• Reference-only data blocks alerts\n"
        "• XM is optional execution validation\n"
        "• No automatic trade execution"
    )


# ============================================================
# NATURAL LANGUAGE MESSAGES
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
        "🤖 Lumi is online.\n\n"
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
# STARTUP / JOB QUEUE
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

        name="global_market_monitor",
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
        "Lumi AI starting with global market intelligence."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
