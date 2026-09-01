import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✨ Hello! I am Lumi AI.\n\n"
        "🧠 Your intelligent assistant is now online.\n"
        "📊 I will help with market analysis, XAUUSD insights and alerts.\n\n"
        "Use /help to see what I can do."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 LUMI AI COMMANDS\n\n"
        "/start - Start Lumi\n"
        "/help - Show commands\n"
        "/status - Check Lumi status\n"
        "/gold - XAUUSD market section"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 Lumi is online and running."
    )


async def gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟡 XAUUSD / GOLD\n\n"
        "Lumi's market analysis engine is currently being developed.\n"
        "Soon I will provide structured market insights and alerts."
    )


def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("gold", gold))

    print("Lumi AI is starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
