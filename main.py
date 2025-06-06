import json
import traceback
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
from config import api_key, api_secret
import os

app = Flask(__name__)

def send_telegram_message(message_text):
    """Telegram'a mesaj gönderir."""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML" # HTML formatında mesaj göndermek için
    }
    try:
        requests.post(TELEGRAM_URL, json=payload)
    except Exception as e:
        print(f"Telegram'a mesaj gönderirken hata oluştu: {e}")


# === Telegram Ayarları ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") # <<< BU SATIRI EKLE
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")   # <<< BU SATIRI EKLE
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage" # <<< BU SATIRI GÜNCELLE
# MAIN_WEBHOOK = "https://burhan-bot.onrender.com/webhook" # <<< BU SATIRI SİL


@app.route("/webhook", methods=["POST"])
def webhook():
    raw_data = request.get_json()
    print("📩 Webhook verisi alındı:", raw_data)

    try:
        # Telegram'a sinyal bilgisi gönderme
        signal_message = f"TradingView Sinyali Alındı:\n<pre>{json.dumps(raw_data, indent=2)}</pre>"
        send_telegram_message(signal_message) # <<< Burayı değiştirdik

        data = raw_data
        if isinstance(data.get("text"), str):
            data = json.loads(data["text"])

        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl")
        tp = data.get("tp")

        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Eksik veri: Symbol:{symbol}, Side:{side}, Entry:{entry}, SL:{sl}, TP:{tp}"
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}") # <<< Hata durumunda da Telegram'a gönder
            return jsonify({"status": "error", "message": error_msg}), 400

        entry = float(entry)
        sl = float(sl)
        tp = float(tp)
        risk_dolar = 16.0
        risk_per_unit = abs(entry - sl)
        quantity = round(risk_dolar / risk_per_unit, 3)

        trade_summary = f"📢 EMİR:\nSide: {side.upper()}\nSymbol: {symbol}\nMiktar: {quantity}\nGiriş: {entry}\nSL: {sl}\nTP: {tp}"
        print(trade_summary)
        send_telegram_message(trade_summary) # <<< İşlem özetini de Telegram'a gönder

       # ... Bybit API kısmı ...
        BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
        BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=False)

        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell",
            order_type="Market",
            qty=quantity,
            time_in_force="GoodTillCancel",
            stopLoss=str(sl), # <<< Bu kısım ekleniyor
            takeProfit=str(tp) # <<< Bu kısım ekleniyor
        )
        print("✅ Emir gönderildi:", order)
        send_telegram_message(f"✅ Bybit Emir Başarılı!\nEmir ID: {order.get('result', {}).get('orderId')}\nDurum: {order.get('retMsg')}") # <<< Başarılı emiri Telegram'a gönder
        return jsonify({"status": "ok", "order": order})

    except Exception as e:
        error_message = f"🔥 HATA webhook: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
        send_telegram_message(f"🚨 Bot Hatası:\n<pre>{error_message}</pre>") # <<< Hata durumunda da Telegram'a detaylı gönder
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

if __name__ == "__main__":
    app.run(debug=True)