
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

    if symbol and side:
        print(f"EMİR GÖNDER: {symbol} için {side.upper()} işlemi 📡")
        return jsonify({"status": "ok", "message": f"{symbol} için {side} işlemi alındı."}), 200
    else:
        return jsonify({"status": "error", "message": "Eksik veri"}), 400

if __name__ == "__main__":
    app.run(debug=True)
