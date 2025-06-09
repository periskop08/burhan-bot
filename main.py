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

# === Yardımcı Fonksiyon: Telegram'a Mesaj Gönderme ===
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

# === Yardımcı Fonksiyon: Fiyatları hassasiyete yuvarlama (float döndürür) ===
def round_to_precision(value, precision_step):
    if value is None:
        return None
    if precision_step <= 0: 
        return float(value) 

    precision_decimal = decimal.Decimal(str(precision_step))
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_HALF_UP)
    return float(rounded_value)

# === YENİ Yardımcı Fonksiyon: Miktarı hassasiyete yuvarlama ve string olarak döndürme ===
# Bu fonksiyon Bybit'in beklediği ondalık hassasiyeti tam olarak yakalamayı hedefler.
def round_to_precision_str(value, precision_step):
    if value is None:
        return ""
    if precision_step <= 0:
        return str(float(value))

    # precision_step'ten ondalık basamak sayısını doğru bir şekilde alalım
    s = str(precision_step)
    if 'e' in s: # Bilimsel gösterim varsa (örn: '1e-06' -> 6 ondalık basamak)
        num_decimals = abs(int(s.split('e')[-1]))
    elif '.' in s: # Normal ondalık sayı (örn: '0.0001' -> 4 ondalık basamak)
        num_decimals = len(s.split('.')[1])
    else: # Tam sayı (örn: '1.0' veya '1')
        num_decimals = 0
    
    # Decimal kütüphanesi ile yüksek hassasiyetle yuvarlama
    # Daha sonra f-string ile istenen ondalık basamak sayısına formatla
    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(str(precision_step))
    
    # Yuvarlama işlemini Decimal üzerinde yap
    # Bu, floating point hatalarını minimize eder
    rounded_d_value = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    
    # F-string formatlaması ile doğrudan string'e dönüştür
    # .trim_zeros() ile sondaki gereksiz sıfırları kaldırıyoruz (örn: 10.0000 -> 10)
    # Bu, Bybit'in bazı API'lerde istediği daha temiz formatı sağlayabilir
    return f"{rounded_d_value.normalize():f}"


# === Ana Webhook Endpoint'i (TradingView Sinyallerini İşler) ===
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
            return jsonify({"status": "error", "message": "Eksik sinyal verisi"}), 400

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
        # Hedeflenen sabit dolar riski
        risk_dolar = 5.0 
        
        # Giriş fiyatı ve SL aynı veya çok yakınsa emir gönderme
        # Bu durumda risk anlamsız olur ve borsalar reddeder.
        if abs(entry - sl) < 0.0000000001: 
            error_msg = f"❗ GİRİŞ FİYATI ({entry}) ve STOP LOSS FİYATI ({sl}) AYNI VEYA ÇOK YAKIN. Risk anlamsız olduğu için emir gönderilmiyor. Lütfen Pine Script stratejinizi kontrol edin."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Pozisyon büyüklüğünü (adet olarak) doğrudan risk_dolar ve SL mesafesine göre hesapla
        calculated_quantity_initial = risk_dolar / abs(entry - sl) 
        send_telegram_message(f"DEBUG: Hedef Risk ({risk_dolar}$), Giriş Fiyatı ({entry}), SL ({sl}). Ham hesaplanan miktar: {calculated_quantity_initial}")


        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        tick_size = 0.000001 # Varsayılan: çok küçük bir değer
        lot_size = 0.000001  # Varsayılan: çok küçük bir değer
        min_order_qty = 0.0  # Varsayılan: minimum emir miktarı
        max_order_qty = float('inf') # Sonsuz olarak başlat
        min_order_value = 0.0 # USDT bazında minimum emir değeri
        
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
                elif 'minTradingQty' in lot_filter: 
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

        # Fiyatları Bybit'in hassasiyetine yuvarla (float olarak kalırlar)
        entry = round_to_precision(entry, tick_size)
        sl = round_to_precision(sl, tick_size)
        tp = round_to_precision(tp, tick_size)
        
        # Miktarı lot_size'a göre yuvarla ve string olarak al. 
        # Bu string, Bybit'e doğrudan gönderilecek formattır.
        quantity_str_for_bybit = round_to_precision_str(calculated_quantity_initial, lot_size)
        
        # Limit kontrollerini yapmak için string'i tekrar float'a çeviriyoruz
        quantity_float_for_checks = float(quantity_str_for_bybit)

        # Yuvarlandıktan sonra limit kontrollerini tekrar yap (float haliyle)
        if quantity_float_for_checks < min_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) minimum emir miktarı ({min_order_qty}) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
        
        if quantity_float_for_checks > max_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) maksimum emir miktarı ({max_order_qty}) üstündedir. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        if quantity_float_for_checks <= 0: 
            error_msg = f"❗ Nihai hesaplanan miktar sıfır veya negatif ({quantity_float_for_checks}). Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Gizli minimum işlem değerini kontrol etmek için
        implied_min_order_value = max(10.0, min_order_value) 

        order_value = quantity_float_for_checks * entry
        if implied_min_order_value > 0 and order_value < implied_min_order_value:
            error_msg = f"❗ Nihai pozisyon değeri ({order_value:.2f} USDT) belirlenen minimum emir değeri ({implied_min_order_value} USDT) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        actual_risk_if_sl_hit = abs(quantity_float_for_checks * (entry - sl))
        if actual_risk_if_sl_hit > risk_dolar:
            send_telegram_message(f"⚠️ DİKKAT: Hesaplanan fiili risk (${actual_risk_if_sl_hit:.2f}) hedef risk (${risk_dolar:.2f}) üzerindedir.")
        
        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (Risk: ${risk_dolar:.2f}):</b>\n" # Başlık güncellendi
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_bybit.upper()}\n" 
            f"<b>Miktar (Adet):</b> {quantity_float_for_checks}\n" # Debug için float hali
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
            qty=quantity_str_for_bybit,  # Bybit'e string hali gönderildi
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
            error_message_telegram = f"<b>🚨 Bybit Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
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
