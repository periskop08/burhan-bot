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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')

telegram_message_queue = Queue()
LAST_TELEGRAM_MESSAGE_TIME = 0
TELEGRAM_RATE_LIMIT_DELAY = 1.0

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
                    response = requests.post(TELEGRAM_URL, json=payload)
                    response.raise_for_status()
                    print(f"\U0001f4e4 Telegram'a mesaj gönderildi: {message_text[:100]}...")
                    LAST_TELEGRAM_MESSAGE_TIME = current_time
                except requests.exceptions.RequestException as e:
                    print(f"\U0001f525 Telegram mesajı gönderilirken hata oluştu: {e}.")
                finally:
                    telegram_message_queue.task_done()
            else:
                time.sleep(TELEGRAM_RATE_LIMIT_DELAY - (current_time - LAST_TELEGRAM_MESSAGE_TIME))
        else:
            time.sleep(0.1)

telegram_sender_thread = threading.Thread(target=telegram_message_sender, daemon=True)
telegram_sender_thread.start()

def send_telegram_message_to_queue(message_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram BOT_TOKEN veya CHAT_ID ortam değişkenlerinde tanımlı değil.")
        return
    telegram_message_queue.put(message_text)

def round_to_precision(value, precision_step):
    if value is None:
        return None
    if precision_step <= 0:
        return float(value)
    precision_decimal = decimal.Decimal(str(precision_step))
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_HALF_UP)
    return float(rounded_value)

def round_to_precision_str(value, precision_step):
    if value is None:
        return ""
    if precision_step <= 0:
        return str(float(value))
    s_precision_step = str(precision_step)
    num_decimals_from_step = 0
    if 'e' in s_precision_step:
        parts = s_precision_step.split('e')
        if '.' in parts[0]:
            num_decimals_from_step = len(parts[0].split('.')[1])
        num_decimals_from_step += abs(int(parts[1]))
    elif '.' in s_precision_step:
        num_decimals_from_step = len(s_precision_step.split('.')[1])
    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(s_precision_step)
    rounded_d_value = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    return f"{rounded_d_value:.{num_decimals_from_step}f}"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print(f"\U0001f4e9 Webhook verisi alındı: {data}")
    try:
        send_telegram_message_to_queue(f"<b>\U0001f514 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>")
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl")
        tp = data.get("tp")

        if not all([symbol, side, entry, sl, tp]):
            msg = f"Eksik veri: {symbol}, {side}, {entry}, {sl}, {tp}"
            send_telegram_message_to_queue(f"\U0001f6a8 {msg}")
            return jsonify({"error": msg}), 400

        if ":" in symbol:
            symbol = symbol.split(":")[-1]
        if symbol.endswith(".P"):
            symbol = symbol[:-2]
        symbol = symbol.upper()
        send_telegram_message_to_queue(f"ℹ️ Nihai işlem sembolü: <b>{symbol}</b>")

        entry = float(entry)
        sl = float(sl)
        tp = float(tp)

        side_for_bybit = "Buy" if side.lower() in ["buy", "long"] else "Sell"

        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        precision = session.get_instruments_info(category="linear", symbol=symbol)
        lot_info = precision['result']['list'][0]['lotFilter']
        tick_info = precision['result']['list'][0]['priceFilter']

        tick_size = float(tick_info.get("tickSize", 0.0001))
        lot_size = float(lot_info.get("qtyStep", 0.0001))

        entry_rounded = round_to_precision(entry, tick_size)
        sl_rounded = round_to_precision(sl, tick_size)
        tp_rounded = round_to_precision(tp, tick_size)

        reward_dollar = 10.0
        risk_dollar = 5.0
        risk_per_unit = abs(entry_rounded - sl_rounded)
        reward_per_unit = abs(tp_rounded - entry_rounded)

        quantity_risk = risk_dollar / risk_per_unit if risk_per_unit > 0 else float('inf')
        quantity_reward = reward_dollar / reward_per_unit if reward_per_unit > 0 else float('inf')
        quantity_max_notional = 100.0 / entry_rounded

        qty = min(quantity_risk, quantity_reward, quantity_max_notional)
        qty_str = round_to_precision_str(qty, lot_size)

        send_telegram_message_to_queue(
            f"\U0001f4ca Qty hesaplaması: Risk bazlı: {quantity_risk:.4f}, Ödül bazlı: {quantity_reward:.4f}, Notional: {quantity_max_notional:.4f} → Seçilen: {qty_str}"
        )

        send_telegram_message_to_queue(
            f"\U0001f4cc Emir Özeti:\n"
            f"Symbol: <b>{symbol}</b>\n"
            f"Yön: <b>{side_for_bybit}</b>\n"
            f"Giriş: <b>{entry_rounded}</b>\n"
            f"SL: <b>{sl_rounded}</b>\n"
            f"TP: <b>{tp_rounded}</b>\n"
            f"Miktar: <b>{qty_str}</b>"
        )

        try:
            order = session.place_order(
                category="linear",
                symbol=symbol,
                side=side_for_bybit,
                orderType="Market",
                qty=qty_str,
                timeInForce="GoodTillCancel",
                stopLoss=str(sl_rounded),
                takeProfit=str(tp_rounded)
            )
        except Exception as e:
            tb = traceback.format_exc()
            send_telegram_message_to_queue(f"\U0001f6a8 Bybit Emir Gönderiminde İstisna:\n<pre>{tb}</pre>")
            print("❌ Bybit'e emir gönderilirken istisna:", e)
            return jsonify({"error": str(e)}), 500

        if order.get('retCode') == 0:
            send_telegram_message_to_queue(
                f"✅ Emir Başarılı: <b>{symbol}</b>\n"
                f"Yön: <b>{side_for_bybit}</b>\n"
                f"Miktar: <b>{qty_str}</b>\n"
                f"SL: <b>{sl_rounded}</b> | TP: <b>{tp_rounded}</b>\n"
                f"\U0001f4c4 Emir ID: <code>{order.get('result', {}).get('orderId', 'N/A')}</code>"
            )
            return jsonify(order)
        else:
            hata_mesaji = order.get('retMsg', 'Bilinmeyen hata')
            detayli_log = json.dumps(order, indent=2)
            send_telegram_message_to_queue(
                f"\U0001f6a8 Bybit Emir Reddedildi:\n"
                f"\U0001f4dd Hata Mesajı: <b>{hata_mesaji}</b>\n"
                f"<pre>{detayli_log}</pre>"
            )
            print("❌ Bybit emir hatası:", detayli_log)
            return jsonify(order), 400

    except Exception as e:
        tb = traceback.format_exc()
        send_telegram_message_to_queue(f"\U0001f525 HATA:\n<pre>{tb}</pre>")
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return 'Burhan-Bot aktif 💪'