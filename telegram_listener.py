import json
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes
import telegram.ext.filters as filters  # ❗️buraya dikkat!

TELEGRAM_BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
WEBHOOK_URL = "https://burhan-bot.onrender.com/webhook"  # main.py’deki render adresi

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = json.loads(update.message.text)
        response = requests.post(WEBHOOK_URL, json=data)
        await update.message.reply_text(f"✅ Webhook gönderildi! Durum: {response.status_code}")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("🤖 Telegram dinleyici aktif...")
    app.run_polling()