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
entry = data.get("entry")
sl = data.get("sl")
tp = data.get("tp")

# Eğer bu bilgiler eksikse hata döndür
if not all([symbol, side, entry, sl, tp]):
    return jsonify({"status": "error", "message": "Eksik veri: entry, sl veya tp boş geldi."}), 400

# Sayıya çevir
entry = float(entry)
sl = float(sl)
tp = float(tp)

    # Risk başına pozisyon büyüklüğü hesaplama
    risk_dolar = 10.0
    risk_per_unit = abs(entry - sl)

    if risk_per_unit == 0:
        return jsonify({"status": "error", "message": "Giriş ve SL aynı, pozisyon büyüklüğü hesaplanamaz."}), 400

    quantity = risk_dolar / risk_per_unit
    quantity = round(quantity, 3)

    print(f"EMİR: {side.upper()} | Symbol: {symbol} | Entry: {entry} | SL: {sl} | TP: {tp} | Miktar: {quantity}")

    # Buraya Bybit API entegrasyonu eklenecek
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