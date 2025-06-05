from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
from config import api_key, api_secret
import json
import traceback

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw_data = request.get_json()
        print("📩 Webhook verisi alındı:", raw_data)

        if isinstance(raw_data.get("text"), str):
            # text içindeki JSON'u ayıkla
            parsed = json.loads(raw_data["text"])
        else:
            parsed = raw_data

        symbol = parsed.get("symbol")
        side = parsed.get("side")
        entry = parsed.get("entry")
        sl = parsed.get("sl")
        tp = parsed.get("tp")

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

if __name__ == "__main__":
    app.run(debug=True)