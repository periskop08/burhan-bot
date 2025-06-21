# main.py - Detaylı loglayan, Telegram entegrasyonlu ve açıklamalı Burhan-Bot

import json
import traceback
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os
import decimal
import time
import threading
from queue import Queue

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')

# === Telegram Mesaj Kuyruğu ve Rate Limit Thread'i ===
telegram_message_queue = Queue()
LAST_TELEGRAM_MESSAGE_TIME = 0
TELEGRAM_RATE_LIMIT_DELAY = 2.0

def telegram_message_sender():
    global LAST_TELEGRAM_MESSAGE_TIME
    while True:
        if not telegram_message_queue.empty():
            current_time = time.time()
            if (current_time - LAST_TELEGRAM_MESSAGE_TIME) >= TELEGRAM_RATE_LIMIT_DELAY:
                message_text = telegram_message_queue.get()
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message_text,
                    "parse_mode": "HTML"
                }
                try:
                    requests.post(TELEGRAM_URL, json=payload)
                    LAST_TELEGRAM_MESSAGE_TIME = time.time()
                except requests.exceptions.RequestException as e:
                    print(f"Telegram hatası: {e}")
                finally:
                    telegram_message_queue.task_done()
            else:
                time.sleep(TELEGRAM_RATE_LIMIT_DELAY - (current_time - LAST_TELEGRAM_MESSAGE_TIME))
        else:
            time.sleep(0.1)

threading.Thread(target=telegram_message_sender, daemon=True).start()

def send_telegram_message_to_queue(message_text):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_message_queue.put(message_text)

# === Hassasiyet yuvarlama fonksiyonları ===
def round_to_precision(value, precision_step):
    if value is None:
        return None
    if precision_step <= 0:
        return float(value)
    precision_decimal = decimal.Decimal(str(precision_step))
    return float(decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_HALF_UP))

def round_quantity_to_exchange_precision(value, precision_step):
    if value is None:
        return ""
    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(str(precision_step))
    num_decimals_from_step = abs(d_precision_step.as_tuple().exponent)
    if d_value >= 100:
        safe_decimals = min(num_decimals_from_step, 2)
    elif d_value >= 1:
        safe_decimals = min(num_decimals_from_step, 4)
    else:
        safe_decimals = min(num_decimals_from_step, 6)
    rounded_value = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    return f"{rounded_value:.{safe_decimals}f}"

# === Webhook endpointi ===
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    send_telegram_message_to_queue(f"📩 <b>Webhook sinyali alındı:</b>\n<pre>{json.dumps(data, indent=2)}</pre>")
    try:
        symbol = data.get("symbol")
        side = data.get("side")
        entry = float(data.get("entry"))
        sl = float(data.get("sl"))
        tp = float(data.get("tp"))

        if ":" in symbol:
            symbol = symbol.split(":")[-1]
        if symbol.endswith(".P"):
            symbol = symbol[:-2]
        symbol = symbol.upper()

        send_telegram_message_to_queue(f"🔧 <b>Temizlenmiş Sembol:</b> {symbol}")

        side_for_bybit = "Buy" if side.lower() in ["buy", "long"] else "Sell"
        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        info = session.get_instruments_info(category="linear", symbol=symbol)['result']['list'][0]
        tick_size = float(info['priceFilter']['tickSize'])
        lot_size = float(info['lotFilter']['qtyStep'])
        min_order_value = float(info['lotFilter'].get('minOrderValue', 0.0))

        entry_r = round_to_precision(entry, tick_size)
        sl_r = round_to_precision(sl, tick_size)
        tp_r = round_to_precision(tp, tick_size)

        notional = 40.0
        qty = notional / entry_r
        qty_str = round_quantity_to_exchange_precision(qty, lot_size)
        qty_f = float(qty_str)

        if qty_f * entry_r < max(10.0, min_order_value):
            send_telegram_message_to_queue("❗ Emir değeri minimumun altında. Emir iptal.")
            return jsonify({"status": "error", "message": "Order value too low."}), 400

        summary = f"<b>📌 Emir Özeti</b>\nSembol: {symbol}\nYön: {side_for_bybit}\nMiktar: {qty_str}\nEntry: {entry_r}\nSL: {sl_r}\nTP: {tp_r}"
        send_telegram_message_to_queue(summary)

        order = session.place_order(
            category="linear",
            symbol=symbol,
            side=side_for_bybit,
            orderType="Market",
            qty=qty_str,
            timeInForce="GoodTillCancel",
            stopLoss=str(sl_r),
            takeProfit=str(tp_r)
        )

        if order['retCode'] == 0:
            send_telegram_message_to_queue(f"✅ <b>Emir başarıyla gönderildi!</b>\nID: {order['result'].get('orderId', 'N/A')}")
            return jsonify({"status": "ok", "order": order})
        else:
            send_telegram_message_to_queue(f"❌ <b>Emir hatası:</b> {order.get('retMsg', 'Bilinmeyen hata')}")
            return jsonify(order), 500

    except Exception as e:
        tb = traceback.format_exc()
        send_telegram_message_to_queue(f"🔥 <b>Genel HATA:</b>\n<pre>{tb}</pre>")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/")
def home():
    return "Burhan-Bot aktif 💪"

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))