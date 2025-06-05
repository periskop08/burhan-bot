import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4915128956"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
MAIN_WEBHOOK = "https://burhan-bot.onrender.com/webhook"

@app.route("/send", methods=["POST"])
def send():
    try:
        raw_data = request.get_json()
        print("📨 TradingView verisi geldi:", raw_data)

        # Telegram mesajı
        telegram_payload = {
            "chat_id": CHAT_ID,
            "text": json.dumps(raw_data),
            "parse_mode": "HTML"
        }

        telegram_response = requests.post(TELEGRAM_URL, json=telegram_payload)
        print("📤 Telegram'a mesaj gönderildi:", telegram_response.text)

        webhook_response = requests.post(MAIN_WEBHOOK, json=raw_data)
        print("📡 Webhook'a veri gönderildi:", webhook_response.text)

        return jsonify({
            "status": "ok",
            "telegram_status": telegram_response.status_code,
            "webhook_status": webhook_response.status_code
        })

    except Exception as e:
        print("🔥 Hata:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)