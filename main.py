import json
import traceback
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os
import decimal

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')

def send_telegram_message(message_text):
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
        response.raise_for_status() 
        print(f"📤 Telegram'a mesaj gönderildi: {message_text[:100]}...") 
    except requests.exceptions.RequestException as e:
        print(f"🔥 Telegram mesajı gönderilirken hata oluştu: {e}")

def round_to_precision(value, precision_step):
    if value is None:
        return None
    if precision_step <= 0: 
        return float(value) 

    precision_decimal = decimal.Decimal(str(precision_step))
    # ROUND_HALF_UP yuvarlama modunu kullanmak daha güvenli olabilir
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_HALF_UP)
    return float(rounded_value)


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print(f"📩 Webhook verisi alındı: {data}")

    try:
        signal_message_for_telegram = f"<b>🔔 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>"
        send_telegram_message(signal_message_for_telegram)
        
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") 
        tp = data.get("tp") 

        side_for_bybit = ""
        if side and side.lower() == "buy":
            side_for_bybit = "Buy"
        elif side and side.lower() == "sell":
            side_for_bybit = "Sell"
        elif side and side.lower() == "long": 
            side_for_bybit = "Buy"
        elif side and side.lower() == "short": 
            side_for_bybit = "Sell"
        else:
            error_msg = f"❗ Geçersiz işlem yönü (side): {side}. 'Buy' veya 'Sell' bekleniyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        if symbol: 
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
                print(f"Sembol TradingView prefix'inden temizlendi: {symbol}")
                send_telegram_message(f"ℹ️ Sembol prefix temizlendi: <b>{symbol}</b>")
            
            if symbol.endswith(".P"):
                symbol = symbol[:-2] 
                print(f"Sembol '.P' ekinden temizlendi: {symbol}")
                send_telegram_message(f"ℹ️ Sembol '.P' eki temizlendi: <b>{symbol}</b>")
            
            symbol = symbol.upper()
            send_telegram_message(f"ℹ️ Nihai işlem sembolü: <b>{symbol}</b>")
        else:
            error_msg = "❗ Sembol bilgisi eksik!"
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Eksik sinyal verisi! Symbol: {symbol}, Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}"
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "Eksik sinyal verisi"}), 400

        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (ValueError, TypeError) as ve:
            error_msg = f"❗ Fiyat verileri sayıya çevrilemedi: Entry={entry}, SL={sl}, TP={tp}. Hata: {ve}. Lütfen Pine Script alert formatını kontrol edin."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "Geçersiz fiyat formatı"}), 400

        # === RİSK YÖNETİMİ AYARI BURADA ===
        risk_dolar = 5.0 
        target_position_value_usd = 200.0 

        # SL ve Entry aynı ise işlem yapma (risk anlamsız olur)
        if abs(entry - sl) < 0.00000001: # Çok küçük bir eşik değeri (örn. 0.00000001)
            error_msg = f"❗ Giriş fiyatı ({entry}) ve SL fiyatı ({sl}) çok yakın veya aynı. Risk anlamsız olduğu için emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Hedef pozisyon büyüklüğüne göre adet hesapla
        calculated_quantity = target_position_value_usd / entry 

        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        tick_size = 0.000001 
        lot_size = 0.000001  
        min_order_qty = 0.0  
        max_order_qty = float('inf') 
        min_order_value = 0.0 
        
        try:
            exchange_info_response = session.get_instruments_info(category="linear", symbol=symbol)
            if exchange_info_response and exchange_info_response['retCode'] == 0 and exchange_info_response['result']['list']:
                instrument_info = exchange_info_response['result']['list'][0]
                price_filter = instrument_info.get('priceFilter', {})
                lot_filter = instrument_info.get('lotFilter', {})

                if 'tickSize' in price_filter:
                    tick_size = float(price_filter['tickSize'])
                
                if 'qtyStep' in lot_filter:
                    lot_size = float(lot_filter['qtyStep'])
                elif 'minTradingQty' in lot_filter: # Alternatif olarak minTradingQty'yi kullanabiliriz
                    lot_size = float(lot_filter['minTradingQty'])

                if 'minOrderQty' in lot_filter:
                    min_order_qty = float(lot_filter['minOrderQty'])
                
                if 'maxOrderQty' in lot_filter: 
                    max_order_qty = float(lot_filter['maxOrderQty'])

                if 'minOrderValue' in lot_filter: 
                    min_order_value = float(lot_filter['minOrderValue'])

                print(f"Bybit {symbol} için API'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}, Max Order Qty: {max_order_qty}, Min Order Value: {min_order_value}")
                send_telegram_message(f"ℹ️ {symbol} için Bybit hassasiyetleri alındı:\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>\nMin Emir Miktarı: <code>{min_order_qty}</code>\nMax Emir Miktarı: <code>{max_order_qty}</code>\nMin Emir Değeri: <code>{min_order_value} USDT</code>")
            else:
                print(f"Uyarı: {symbol} için Bybit hassasiyet bilgisi bulunamadı. API yanıtı: {exchange_info_response}. Varsayılanlar kullanılıyor.")
                send_telegram_message(f"⚠️ {symbol} için Bybit hassasiyet bilgisi alınamadı. Varsayılanlar kullanılıyor.")

        except Exception as api_e:
            error_msg_api = f"Bybit sembol/hassasiyet bilgisi alınırken hata: {api_e}. Varsayılan hassasiyetler kullanılıyor."
            print(error_msg_api)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg_api}")

        entry = round_to_precision(entry, tick_size)
        sl = round_to_precision(sl, tick_size)
        tp = round_to_precision(tp, tick_size)
        
        # calculate the quantity based on the actual entry price
        # This makes sure the final quantity is based on the rounded entry price
        calculated_quantity = target_position_value_usd / entry 

        # Bybit'in limitlerini karşılamak için miktar ayarlamaları
        if calculated_quantity < min_order_qty:
            error_msg = f"❗ Hesaplanan miktar ({calculated_quantity}) minimum emir miktarı ({min_order_qty}) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
        
        if calculated_quantity > max_order_qty:
            error_msg = f"❗ Hesaplanan miktar ({calculated_quantity}) maksimum emir miktarı ({max_order_qty}) üstündedir. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Nihai miktar, lot_size'a göre yuvarlanmış hali
        quantity = round_to_precision(calculated_quantity, lot_size)
        
        if quantity <= 0: 
            error_msg = f"❗ Nihai hesaplanan miktar sıfır veya negatif ({quantity}). Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        order_value = quantity * entry
        if min_order_value > 0 and order_value < min_order_value:
            error_msg = f"❗ Nihai pozisyon değeri ({order_value:.2f} USDT) minimum emir değeri ({min_order_value} USDT) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Hesaplanan fiili riski de Telegram'a gönder
        actual_risk_if_sl_hit = abs(quantity * (entry - sl))
        if actual_risk_if_sl_hit > risk_dolar:
            send_telegram_message(f"⚠️ DİKKAT: Hesaplanan fiili risk (${actual_risk_if_sl_hit:.2f}) hedef risk (${risk_dolar:.2f}) üzerindedir. Bu, {target_position_value_usd}$ hedef pozisyon büyüklüğünden kaynaklanmaktadır.")
        
        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (Hedef Değer: ${target_position_value_usd:.2f}):</b>\n" 
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_bybit.upper()}\n" 
            f"<b>Miktar (Adet):</b> {quantity}\n"
            f"<b>Giriş Fiyatı:</b> {entry}\n"
            f"<b>Stop Loss (SL):</b> {sl}\n"
            f"<b>Take Profit (TP):</b> {tp}\n"
            f"<b>Hesaplanan Risk (SL vurulursa):</b> ${actual_risk_if_sl_hit:.2f}" 
        )
        send_telegram_message(trade_summary)

        order = session.place_order(
            category="linear", 
            symbol=symbol,
            side=side_for_bybit, 
            orderType="Market", 
            qty=str(quantity),  
            timeInForce="GoodTillCancel", 
            stopLoss=str(sl),   
            takeProfit=str(tp)  
        )

        print(f"✅ Emir gönderildi: {order}")

        if order and order.get('retCode') == 0:
            order_info = order.get('result', {})
            success_message = (
                f"<b>✅ Bybit Emir Başarılı!</b>\n"
                f"<b>Emir ID:</b> <code>{order_info.get('orderId', 'N/A')}</code>\n"
                f"<b>Sembol:</b> {order_info.get('symbol', 'N/A')}\n"
                f"<b>Yön:</b> {order_info.get('side', 'N/A')}\n"
                f"<b>Miktar:</b> {order_info.get('qty', 'N/A')}\n"
                f"<b>Fiyat:</b> {order_info.get('price', 'N/A')}\n"
                f"<b>Durum:</b> {order.get('retMsg', 'Başarılı')}"
            )
            send_telegram_message(success_message)
            return jsonify({"status": "ok", "order": order})
        else:
            error_response_msg = order.get('retMsg', 'Bilinmeyen Bybit hatası.')
            full_error_details = json.dumps(order, indent=2) 
            error_message_telegram = f"<b>🚨 Bybit Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity}\n<pre>{full_error_details}</pre>"
            send_telegram_message(error_message_telegram)
            return jsonify({"status": "error", "message": error_response_msg}), 500

    except Exception as e:
        error_message_full = f"🔥 Genel HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        send_telegram_message(f"<b>🚨 KRİTİK BOT HATASI!</b>\n<pre>{error_message_full}</pre>")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

if __name__ == "__main__":
    app.run(debug=True, port=os.getenv("PORT", 5000))
