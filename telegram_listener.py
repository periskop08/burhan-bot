import json
import re
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

TELEGRAM_BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
WEBHOOK_URL = "https://burhan-bot.onrender.com/webhook"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        print("⚠️ Mesaj içeriği boş geldi.")
        return

    text = update.message.text
    print("GELEN MESAJ:", text)

    match = re.search(r'\{.*\}', text)
    if not match:
        await update.message.reply_text("❌ JSON bulunamadı.")
        return

    try:
        data = json.loads(match.group())
        response = requests.post(WEBHOOK_URL, json=data)
        await update.message.reply_text(f"✅ Webhook gönderildi! Durum: {response.status_code}")
    except Exception as e:
        print("🔥 Hata:", e)
        await update.message.reply_text(f"❌ Hata oluştu: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    print("🤖 Telegram dinleyici aktif...")
    app.run_polling()