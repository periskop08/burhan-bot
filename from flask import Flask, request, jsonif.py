from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 🎯"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("Webhook verisi alındı:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    entry = float(data.get("entry"))
    sl = float(data.get("sl"))
    tp = float(data.get("tp"))

    # Risk başına pozisyon büyüklüğü hesaplama
    risk_dolar = 10.0
    risk_per_unit = abs(entry - sl)

    if risk_per_unit == 0:
        return jsonify({"status": "error", "message": "Giriş ve SL aynı, pozisyon büyüklüğü hesaplanamaz."}), 400

    quantity = risk_dolar / risk_per_unit
    quantity = round(quantity, 3)

    print(f"EMİR: {side.upper()} | Symbol: {symbol} | Entry: {entry} | SL: {sl} | TP: {tp} | Miktar: {quantity}")

    # Buraya Bybit API emir entegrasyonu eklenecek
    # bybit.place_order(symbol, side, entry, sl, tp, quantity)

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