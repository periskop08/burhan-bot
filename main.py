from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
from config import api_key, api_secret
import requests
import traceback

TELEGRAM_BOT_TOKEN = "7555166060:AAF57LlQMX_K4-RMnktR0jMEsTxcd1FK4jw"
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("\n📩 Webhook verisi alındı:", data)
    print("📦 Veri tipi:", type(data))

    symbol = data.get("symbol")
    side = data.get("side")
    entry = data.get("entry")
    sl = data.get("sl")
    tp = data.get("tp")

    if not all([symbol, side, entry, sl, tp]):
        print("❗ Eksik veri:", symbol, side, entry, sl, tp)
        return jsonify({"status": "error", "message": "Eksik veri: entry, sl veya tp eksik."}), 400

    try:
        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
    except ValueError:
        print("❗ Sayıya çevrilemedi:", entry, sl, tp)
        return jsonify({"status": "error", "message": "Entry, SL veya TP sayıya çevrilemedi."}), 400

    risk_dolar = 16.0
    risk_per_unit = abs(entry - sl)
    if risk_per_unit == 0:
        print("❗ Entry ve SL aynı değer, pozisyon açılamaz.")
        return jsonify({"status": "error", "message": "Entry ve SL aynı."}), 400

    quantity = round(risk_dolar / risk_per_unit, 3)
    if quantity <= 0:
        print("❗ Miktar sıfır veya negatif:", quantity)
        return jsonify({"status": "error", "message": "Miktar geçersiz."}), 400

    print(f"📢 EMİR: {side.upper()} | {symbol} | Entry: {entry} | SL: {sl} | TP: {tp} | Miktar: {quantity}")

    session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

    try:
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
    except Exception:
        print("🔥 Emir gönderilirken hata oluştu:")
        traceback.print_exc()

    return jsonify({
        "status": "ok",
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "quantity": quantity
    }), 200

@app.route("/send", methods=["POST"])
def send_to_telegram():
    data = request.get_json()
    print("📨 TradingView verisi geldi:", data)

    telegram_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(telegram_api, json=data)
        print("📤 Telegram'a mesaj gönderildi:", response.text)
        return jsonify({"status": "ok", "telegram_status": response.status_code})
    except Exception as e:
        print("❌ Telegram mesaj gönderim hatası:", e)
        return jsonify({"status": "error", "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)