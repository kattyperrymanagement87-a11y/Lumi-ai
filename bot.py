import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✨ Hello! I am Lumi AI.\n\n"
        "🧠 Your intelligent assistant is online and ready.\n"
        "📊 I am being developed to assist with XAUUSD market monitoring, "
        "structured analysis and alerts.\n\n"
        "Use /help to see my available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 LUMI AI COMMANDS\n\n"
        "/start - Start Lumi\n"
        "/help - Show available commands\n"
        "/status - Check Lumi's system status\n"
        "/gold - Open XAUUSD section\n\n"
        "💬 You can also talk to me normally!"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 SYSTEM STATUS\n\n"
        "Lumi AI: Online\n"
        "Telegram Connection: Active\n"
        "Core System: Running\n"
        "Market Intelligence: Under Development"
    )


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟡 XAUUSD / GOLD CENTER\n\n"
        "📊 Market intelligence module is being developed.\n\n"
        "Future capabilities:\n"
        "• Market bias\n"
        "• Price monitoring\n"
        "• Structured analysis\n"
        "• Alert conditions\n\n"
        "⚠️ Lumi will present analysis and alerts, not guaranteed profits."
    )


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.lower()

    if any(word in message for word in ["hello", "hi", "hey"]):
        reply = (
            "Hello ❤️ I'm Lumi.\n\n"
            "I'm online and ready to assist you. "
            "You can ask me about my status or use /help to explore my commands."
        )

    elif "how are you" in message:
        reply = (
            "I'm doing great 😄🧠\n\n"
            "My systems are online and I'm ready to work."
        )

    elif any(phrase in message for phrase in ["who are you", "what are you"]):
        reply = (
            "I'm Lumi AI ✨🧠\n\n"
            "I'm an intelligent assistant currently being developed with "
            "Telegram communication and XAUUSD market-monitoring capabilities."
        )

    elif "gold" in message or "xauusd" in message:
        reply = (
            "🟡 XAUUSD detected.\n\n"
            "My market intelligence system is currently being expanded. "
            "Soon I'll be able to provide structured market observations and alerts."
        )

    elif "thank" in message:
        reply = "You're always welcome ❤️ I'm here with you."

    else:
        reply = (
            "I'm still learning and expanding my capabilities 🧠✨\n\n"
            "Try /help to see what I can currently do."
        )

    await update.message.reply_text(reply)


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("gold", gold))

    # Normal conversation
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chat)
    )

    print("Lumi AI is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
