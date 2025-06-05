import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# === Telegram Ayarları ===
BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4915128956"
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# === Main.py Webhook Adresi ===
MAIN_WEBHOOK = "https://burhan-bot.onrender.com/webhook"

@app.route("/send", methods=["POST"])
def send():
    try:
        data = request.get_json()

        # ✅ Telegram'a düzgün görünmesi için metni stringify et
        pretty_text = json.dumps(data, indent=2)

        # 📩 Telegram'a mesaj gönder
        telegram_payload = {
            "chat_id": CHAT_ID,
            "text": f"<b>📡 Yeni TradingView Sinyali:</b>\n<pre>{pretty_text}</pre>",
            "parse_mode": "HTML"
        }
        telegram_response = requests.post(TELEGRAM_URL, json=telegram_payload)

        # 🧠 main.py webhook'una gönderilecek temiz JSON
        if "text" in data:
            clean_data = json.loads(data["text"])  # Text içinden çözümlü JSON
        else:
            clean_data = data  # Direkt JSON gönderildiyse

        main_response = requests.post(MAIN_WEBHOOK, json=clean_data)

        return jsonify({
            "status": "ok",
            "telegram_status": telegram_response.status_code,
            "main_status": main_response.status_code
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)