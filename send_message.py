import requests
from flask import Flask, request, jsonify
import json

app = Flask(__name__)

# === Telegram Ayarları ===
BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4915128956"  # Grup chat_id

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

@app.route("/send-message", methods=["POST"])
def send_message():
    try:
        data = request.get_json()

        # JSON verisini düzgün string olarak (çift tırnaklı) gönder
        text_json = json.dumps(data)

        payload = {
            "chat_id": CHAT_ID,
            "text": text_json,
            "parse_mode": "HTML"
        }

        response = requests.post(TELEGRAM_URL, json=payload)

        if response.status_code == 200:
            print("📤 Telegram'a mesaj gönderildi:", payload)
            return jsonify({"status": "ok", "telegram_response": response.json()})
        else:
            print("❌ Telegram gönderim hatası:", response.text)
            return jsonify({"status": "error", "details": response.text}), 500

    except Exception as e:
        print("🔥 Hata:", e)
        return jsonify({"status": "exception", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)