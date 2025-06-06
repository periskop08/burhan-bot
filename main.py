import json
import traceback
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os
import decimal # Finansal hesaplamalarda hassasiyet için eklendi

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
# Bu değişkenleri Render.com üzerinde Environment Variables olarak tanımlamalısın.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# Testnet modunu ortam değişkeninden al. Canlı için 'False' olmalı.
# Render'da 'BYBIT_TESTNET_MODE' diye bir değişken eklemezsen varsayılan olarak False olur.
BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')

# === Yardımcı Fonksiyon: Telegram'a Mesaj Gönderme ===
def send_telegram_message(message_text):
    """
    Belirtilen metni Telegram sohbetine HTML formatında gönderir.
    Ortam değişkenlerinde TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID'nin tanımlı olması gerekir.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram BOT_TOKEN veya CHAT_ID ortam değişkenlerinde tanımlı değil.")
        return

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(TELEGRAM_URL, json=payload)
        response.raise_for_status() # HTTP hatalarını yakala (örn. 404, 500)
        print(f"📤 Telegram'a mesaj gönderildi: {message_text[:100]}...") # Mesajın ilk 100 karakteri
    except requests.exceptions.RequestException as e:
        print(f"🔥 Telegram mesajı gönderilirken hata oluştu: {e}")

# === Yardımcı Fonksiyon: Fiyat ve Miktarı Hassasiyete Yuvarlama ===
def round_to_precision(value, precision_step):
    """
    Değeri belirtilen hassasiyet adımına göre yuvarlar.
    Örn: value=0.12345, precision_step=0.001 -> 0.123
    """
    if value is None:
        return None
    if precision_step <= 0: # Sıfır veya negatif hassasiyet adımı durumunda orijinal değeri döndür
        return float(value) # Orijinal değeri float olarak döndür

    # Decimal kütüphanesi ile hassas yuvarlama
    # Adım formatı için 'quantize' fonksiyonuna uygun bir Decimal nesnesi oluştur
    precision_decimal = decimal.Decimal(str(precision_step))
    # Değeri Decimal nesnesine çevir ve yuvarla (ROUND_FLOOR: aşağı yuvarla)
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_FLOOR)
    return float(rounded_value)


# === Ana Webhook Endpoint'i (TradingView Sinyallerini İşler) ===
@app.route("/webhook", methods=["POST"])
def webhook():
    # Only get raw_data once at the beginning
    raw_data = request.get_json()
    print(f"📩 Webhook data received: {raw_data}")

    try:
        # Send raw signal data to Telegram
        signal_message_for_telegram = f"<b>🔔 TradingView Raw Signal:</b>\n<pre>{json.dumps(raw_data, indent=2)}</pre>"
        send_telegram_message(signal_message_for_telegram)

        data = raw_data # Use the initially received raw_data

        # Try to parse the incoming data based on different expected keys (content, message, text)
        if "content" in data and isinstance(data["content"], str):
            try:
                data = json.loads(data["content"])
                print(f"✅ Signal parsed from 'content' field: {data}")
                send_telegram_message(f"<b>✅ Message parsed from 'content' field:</b>\n<pre>{json.dumps(data, indent=2)}</pre>")
            except json.JSONDecodeError as jde:
                error_msg = f"❗ Failed to parse JSON from 'content' field: {jde}. Content: {data['content'][:200]}..."
                print(error_msg)
                send_telegram_message(f"🚨 Bot Error: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 400
        elif "message" in data and isinstance(data["message"], str):
            try:
                data = json.loads(data["message"])
                print(f"✅ Signal parsed from 'message' field: {data}")
                send_telegram_message(f"<b>✅ Message parsed from 'message' field:</b>\n<pre>{json.dumps(data, indent=2)}</pre>")
            except json.JSONDecodeError as jde:
                error_msg = f"❗ Failed to parse JSON from 'message' field: {jde}. Content: {data['message'][:200]}..."
                print(error_msg)
                send_telegram_message(f"🚨 Bot Error: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 400
        elif isinstance(data.get("text"), str):
            try:
                data = json.loads(data["text"])
                print(f"✅ Signal parsed from 'text' field: {data}")
                send_telegram_message(f"<b>✅ Message parsed from 'text' field:</b>\n<pre>{json.dumps(data, indent=2)}</pre>")
            except json.JSONDecodeError as jde:
                error_msg = f"❗ Failed to parse JSON from 'text' field: {jde}. Content: {data['text'][:200]}..."
                print(error_msg)
                send_telegram_message(f"🚨 Bot Error: {error_msg}")
                return jsonify({"status": "error", "message": error_msg}), 400
        # else: data is already in the expected JSON format, no extra parsing needed


        # Get required signal data
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") # Stop Loss
        tp = data.get("tp") # Take Profit

        # Clean symbol from TradingView prefixes or suffixes not expected by Bybit
        if symbol: # Check if symbol is not None
            # Example: "BINANCE:BTCUSDT" -> "BTCUSDT"
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
                print(f"Symbol cleaned from TradingView prefix: {symbol}")
                send_telegram_message(f"ℹ️ Symbol prefix cleaned: <b>{symbol}</b>")
            
            # Example: "BTCUSDT.P" -> "BTCUSDT" (remove .P suffix for Bybit)
            if symbol.endswith(".P"): # If symbol ends with .P
                symbol = symbol[:-2] # Remove the last 2 characters (.P)
                print(f"Symbol cleaned from '.P' suffix: {symbol}")
                send_telegram_message(f"ℹ️ Symbol '.P' suffix cleaned: <b>{symbol}</b>")
            
            # Additional safety step: Convert symbol to uppercase (Bybit symbols are usually uppercase)
            symbol = symbol.upper()
            send_telegram_message(f"ℹ️ Final trading symbol: <b>{symbol}</b>")

        # Check if essential data is missing
        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Missing signal data! Symbol: {symbol}, Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}"
            print(error_msg)
            send_telegram_message(f"🚨 Bot Error: {error_msg}")
            return jsonify({"status": "error", "message": "Missing signal data"}), 400

        # Convert numerical values to float (they might come as string from TradingView)
        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (ValueError, TypeError) as ve:
            error_msg = f"❗ Price data could not be converted to number: Entry={entry}, SL={sl}, TP={tp}. Error: {ve}. Please check TradingView signal format."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Error: {error_msg}")
            return jsonify({"status": "error", "message": "Invalid price format"}), 400

        # Calculate position size based on risk management
        risk_dolar = 16.0
        risk_per_unit = abs(entry - sl)

        # If risk per unit is zero (SL = Entry), halt or use a default quantity
        if risk_per_unit == 0:
            error_msg = "❗ Risk per unit cannot be zero (Entry price equals SL). Quantity cannot be calculated."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Error: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        calculated_quantity = risk_dolar / risk_per_unit

        # Initialize Bybit API session (best to do here to get instrument info)
        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        # Get symbol information from Bybit (for Price and Quantity precision)
        tick_size = 0.000001 # Default: a very small value, usually sufficient for most pairs
        lot_size = 0.000001  # Default: a very small value
        min_order_qty = 0.0  # Default: minimum order quantity
        
        try:
            exchange_info_response = session.get_instruments_info(category="linear", symbol=symbol)
            if exchange_info_response and exchange_info_response['retCode'] == 0 and exchange_info_response['result']['list']:
                # Filters are in the first item of the 'list' in Bybit Unified Trading API
                instrument_info = exchange_info_response['result']['list'][0]
                price_filter = instrument_info.get('priceFilter', {})
                lot_filter = instrument_info.get('lotFilter', {})

                # Price step (tickSize)
                if 'tickSize' in price_filter:
                    tick_size = float(price_filter['tickSize'])
                
                # Quantity step (qtyStep) and Minimum order quantity (minOrderQty)
                if 'qtyStep' in lot_filter:
                    lot_size = float(lot_filter['qtyStep'])
                elif 'minTradingQty' in lot_filter: # Alternatively, we can use minTradingQty
                    lot_size = float(lot_filter['minTradingQty'])

                if 'minOrderQty' in lot_filter:
                    min_order_qty = float(lot_filter['minOrderQty'])

                print(f"Bybit {symbol} API retrieved Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}")
                send_telegram_message(f"ℹ️ Bybit precisions for {symbol} retrieved:\nPrice Step: <code>{tick_size}</code>\nQuantity Step: <code>{lot_size}</code>\nMin Order Quantity: <code>{min_order_qty}</code>")
            else:
                print(f"Warning: Bybit precision info for {symbol} not found. API response: {exchange_info_response}. Using defaults.")
                send_telegram_message(f"⚠️ Bybit precision info for {symbol} not found. Using defaults.")

        except Exception as api_e:
            error_msg_api = f"Error retrieving Bybit symbol/precision info: {api_e}. Using defaults."
            print(error_msg_api)
            send_telegram_message(f"🚨 Bot Error: {error_msg_api}")
            # Default precisions are already defined above in case of error


        # Round prices and quantity to Bybit's precision
        entry = round_to_precision(entry, tick_size)
        sl = round_to_precision(sl, tick_size)
        tp = round_to_precision(tp, tick_size)
        quantity = round_to_precision(calculated_quantity, lot_size)
        
        # If calculated quantity is zero or negative, do not place order
        if quantity <= 0:
            error_msg = f"❗ Calculated quantity is zero or negative ({quantity}). Order not placed."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Error: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
        
        # If quantity is less than minimum order quantity, use the minimum quantity
        if min_order_qty > 0 and quantity < min_order_qty:
            warning_msg = f"⚠️ Calculated quantity ({quantity}) is below minimum order quantity ({min_order_qty}). Using minimum quantity."
            print(warning_msg)
            send_telegram_message(warning_msg)
            quantity = min_order_qty # Use the minimum quantity


        # Send order summary to Telegram (with rounded values)
        trade_summary = (
            f"<b>📢 NEW ORDER PLACEMENT (Rounded & Adjusted Values):</b>\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Side:</b> {side.upper()}\n"
            f"<b>Quantity (Units):</b> {quantity}\n"
            f"<b>Entry Price:</b> {entry}\n"
            f"<b>Stop Loss (SL):</b> {sl}\n"
            f"<b>Take Profit (TP):</b> {tp}\n"
            f"<b>Risk Amount:</b> ${risk_dolar}"
        )
        send_telegram_message(trade_summary)

        # Send order to Bybit
        order = session.place_order(
            category="linear", # 'linear' for perpetual futures, 'spot' for spot market
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell", # 'Buy' or 'Sell' based on signal direction
            orderType="Market", # Market order
            qty=str(quantity),  # Bybit API expects qty as a string
            timeInForce="GoodTillCancel", # Order remains active until cancelled
            stopLoss=str(sl),   # Send SL price as a string
            takeProfit=str(tp)  # Send TP price as a string
        )

        print(f"✅ Order sent: {order}")

        # Notify Telegram about the order result
        if order and order.get('retCode') == 0:
            order_info = order.get('result', {})
            success_message = (
                f"<b>✅ Bybit Order Successful!</b>\n"
                f"<b>Order ID:</b> <code>{order_info.get('orderId', 'N/A')}</code>\n"
                f"<b>Symbol:</b> {order_info.get('symbol', 'N/A')}\n"
                f"<b>Side:</b> {order_info.get('side', 'N/A')}\n"
                f"<b>Quantity:</b> {order_info.get('qty', 'N/A')}\n"
                f"<b>Price:</b> {order_info.get('price', 'N/A')}\n"
                f"<b>Status:</b> {order.get('retMsg', 'Successful')}"
            )
            send_telegram_message(success_message)
            return jsonify({"status": "ok", "order": order})
        else:
            error_response_msg = order.get('retMsg', 'Unknown Bybit error.')
            # Show detailed Bybit error in logs and Telegram
            full_error_details = json.dumps(order, indent=2) 
            error_message_telegram = f"<b>🚨 Bybit Order Error:</b>\n{error_response_msg}\nSignal: {symbol}, {side}, Quantity: {quantity}\n<pre>{full_error_details}</pre>"
            send_telegram_message(error_message_telegram)
            return jsonify({"status": "error", "message": error_response_msg}), 500

    except Exception as e:
        error_message_full = f"🔥 General ERROR while processing webhook: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        send_telegram_message(f"<b>🚨 CRITICAL BOT ERROR!</b>\n<pre>{error_message_full}</pre>")
        return jsonify({"status": "error", "message": str(e)}), 500

# === Home Page (To check if the bot is active) ===
@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot active 💪"

# === Start the application ===
if __name__ == "__main__":
    # Gunicorn is used on Render, this part is only for local testing
    app.run(debug=True, port=os.getenv("PORT", 5000))
