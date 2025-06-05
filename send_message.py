import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# === Telegram Ayarları ===
BOT_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_GROUP_CHAT_ID"  # Başında - işareti olabilir, örn: -1001234567890

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

@app.route("/send-message", methods=["POST"])
def send_message():
    try:
        data = request.get_json()

        # Gelen veriyi ham olarak Telegram grubuna ilet
        msg = f"Yeni TradingView Sinyali:\n\n{data}"

        payload = {
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }

        response = requests.post(TELEGRAM_URL, json=payload)

        if response.status_code == 200:
            return jsonify({"status": "ok", "telegram_response": response.json()})
        else:
            return jsonify({"status": "error", "details": response.text}), 500

    except Exception as e:
        return jsonify({"status": "exception", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)