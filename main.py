from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
from config import api_key, api_secret

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("Webhook verisi alındı:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    entry = data.get("entry")
    sl = data.get("sl")
    tp = data.get("tp")

    # Eksik veri varsa
    if not all([symbol, side, entry, sl, tp]):
        return jsonify({"status": "error", "message": "Eksik veri: entry, sl veya tp eksik."}), 400

    try:
        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
    except ValueError:
        return jsonify({"status": "error", "message": "Sayısal veriler float'a çevrilemedi."}), 400

    # Pozisyon büyüklüğü hesaplama
    risk_dolar = 10.0
    risk_per_unit = abs(entry - sl)
    if risk_per_unit == 0:
        return jsonify({"status": "error", "message": "Entry ve SL aynı olamaz."}), 400

    quantity = round(risk_dolar / risk_per_unit, 3)

    print(f"EMİR: {side.upper()} | Symbol: {symbol} | Entry: {entry} | SL: {sl} | TP: {tp} | Miktar: {quantity}")

    # Bybit API bağlantısı (gerçek)
    session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

    try:
        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell",
            order_type="Market",
            qty=quantity,
            time_in_force="GoodTillCancel",
            position_idx=1  # One-Way mode
        )
        print("✅ Emir gönderildi:", order)
    except Exception as e:
        print("❌ Emir gönderilirken hata oluştu:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "ok",
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "quantity": quantity
    }), 200

if __name__ == "__main__":
    app.run(debug=True)