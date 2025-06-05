from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
from config import api_key, api_secret
import json
import traceback
import requests

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
CHAT_ID = "-4915128956"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

@app.route("/webhook", methods=["POST"])
def webhook():
    raw_data = request.get_json()
    print("📩 Webhook verisi alındı:", raw_data)

    try:
        data = raw_data
        if isinstance(data.get("text"), str):
            data = json.loads(data["text"])

        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl")
        tp = data.get("tp")

        if not all([symbol, side, entry, sl, tp]):
            print("❗ Eksik veri:", symbol, side, entry, sl, tp)
            return jsonify({"status": "error", "message": "Eksik veri"}), 400

        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
        risk_dolar = 16.0
        risk_per_unit = abs(entry - sl)
        quantity = round(risk_dolar / risk_per_unit, 3)

        print(f"📢 EMİR: {side.upper()} | Symbol: {symbol} | Miktar: {quantity}")

        session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell",
            order_type="Market",
            qty=quantity,
            time_in_force="GoodTillCancel",
            position_idx=1
        )
        print("✅ Emir gönderildi:", order)
        return jsonify({"status": "ok", "order": order})

    except Exception as e:
        print("🔥 HATA:")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/send", methods=["POST"])
def send_to_telegram():
    data = request.get_json()
    print("📨 TradingView verisi geldi:", data)

    try:
        telegram_payload = {
            "chat_id": CHAT_ID,
            "text": json.dumps(data),
            "parse_mode": "HTML"
        }

        telegram_response = requests.post(TELEGRAM_URL, json=telegram_payload)
        print("📤 Telegram'a mesaj gönderildi:", telegram_response.text)

        # Webhook tekrar tetikleniyor
        webhook_response = requests.post("https://burhan-bot.onrender.com/webhook", json=data)
        print("📡 Webhook'a veri gönderildi:", webhook_response.text)

        return jsonify({
            "status": "ok",
            "telegram_status": telegram_response.status_code,
            "webhook_status": webhook_response.status_code
        })

    except Exception as e:
        print("🔥 Telegram/Bybit yönlendirme hatası:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)