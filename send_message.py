import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# === Telegram Ayarları ===
BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4876457193"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

MAIN_WEBHOOK = "https://burhan-bot.onrender.com/webhook"

@app.route("/send", methods=["POST"])
def send():
    try:
        data = request.get_json()

        # Telegram'a mesaj gönder
        payload = {
            "chat_id": CHAT_ID,
            "text": f"TradingView verisi geldi:\n\n{data}",
            "parse_mode": "HTML"
        }
        telegram_response = requests.post(TELEGRAM_URL, json=payload)

        # 🧠 Aynı veriyi main.py'ye de POST et!
        main_response = requests.post(MAIN_WEBHOOK, json=data)

        return jsonify({
            "status": "ok",
            "telegram_status": telegram_response.status_code,
            "main_status": main_response.status_code
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)