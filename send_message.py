import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# === Telegram Ayarları ===
BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4915128956"  # Grubun doğru chat_id'si

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

@app.route("/send", methods=["POST"])
def send_message():
    try:
        data = request.get_json()

        # Gelen veriyi ham olarak Telegram grubuna ilet
        msg = f"{data}"

        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }

        response = requests.post(TELEGRAM_URL, json=payload)
        print("📩 TradingView verisi geldi:", data)
        print("📬 Telegram'a mesaj gönderildi:", response.json())

        # 🔁 Veriyi main.py'deki webhook'a gönder
        webhook_response = requests.post("https://burhan-bot.onrender.com/webhook", json=data)
        print("🎯 main.py'ye iletim durumu:", webhook_response.status_code)

        return jsonify({"status": "ok", "telegram": response.json()}), 200

    except Exception as e:
        print("❌ Hata:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)