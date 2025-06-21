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

# === Telegram Mesaj Kuyruğu ve İşleyici ===
telegram_message_queue = Queue()
LAST_TELEGRAM_MESSAGE_TIME = 0
TELEGRAM_RATE_LIMIT_DELAY = 2.0 # Telegram'a en az 2 saniyede bir mesaj gönder (daha güvenli)

def telegram_message_sender():
    """
    Kuyruktaki Telegram mesajlarını rate limit'e uygun şekilde gönderir.
    """
    global LAST_TELEGRAM_MESSAGE_TIME
    while True:
        if not telegram_message_queue.empty():
            current_time = time.time()
            time_since_last_message = current_time - LAST_TELEGRAM_MESSAGE_TIME
            
            if time_since_last_message >= TELEGRAM_RATE_LIMIT_DELAY:
                message_text = telegram_message_queue.get()
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message_text,
                    "parse_mode": "HTML"
                }
                try:
                    response = requests.post(TELEGRAM_URL, json=payload)
                    response.raise_for_status() 
                    print(f"📤 Telegram'a mesaj gönderildi: {message_text[:100]}...") 
                    LAST_TELEGRAM_MESSAGE_TIME = time.time() # Başarılı gönderimden sonra zamanı güncelle
                except requests.exceptions.RequestException as e:
                    print(f"🔥 Telegram mesajı gönderilirken hata oluştu: {e}. Mesaj KAYBEDİLDİ (kuyruktan çıkarıldı).")
                finally:
                    telegram_message_queue.task_done() 
            else:
                # Gecikme süresi dolmadıysa kalan süreyi bekle
                sleep_duration = TELEGRAM_RATE_LIMIT_DELAY - time_since_last_message
                time.sleep(sleep_duration)
        else:
            time.sleep(0.1) # Kuyruk boşsa kısa bir süre bekle

telegram_sender_thread = threading.Thread(target=telegram_message_sender, daemon=True)
telegram_sender_thread.start()

def send_telegram_message_to_queue(message_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram BOT_TOKEN veya CHAT_ID ortam değişkenlerinde tanımlı değil. Mesaj kuyruğa eklenemedi.")
        return
    telegram_message_queue.put(message_text)


# === Yardımcı Fonksiyon: Fiyatları hassasiyete yuvarlama (float döndürür) ===
def round_to_precision(value, precision_step):
    if value is None:
        return None
    if precision_step <= 0: 
        return float(value) 

    precision_decimal = decimal.Decimal(str(precision_step))
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_HALF_UP)
    return float(rounded_value)

# === Miktarı, borsa adım hassasiyetine göre yuvarlama ve string olarak döndürme ===
def round_quantity_to_exchange_precision(value, precision_step):
    if value is None:
        return ""
    if precision_step <= 0: # Eğer precision_step 0 veya negatifse, direkt stringe çevir
        return str(float(value))

    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(str(precision_step))

    # precision_step'ten ondalık basamak sayısını al
    num_decimals_from_step = abs(d_precision_step.as_tuple().exponent)
    
    # === KRİTİK DEĞİŞİKLİK BURADA: Maksimum ondalık basamak sayısını kısıtla ===
    # Eğer lot_size'dan gelen ondalık basamak sayısı çok fazlaysa (örn. 6'dan fazla),
    # Bybit'in kabul edebileceği daha güvenli bir maksimuma düşür.
    # Örneğin, çoğu Bybit paritesi için 4 ondalık basamak yeterlidir.
    # Eğer num_decimals_from_step 4'ten küçükse onu kullan, değilse 4'ü kullan.
    final_decimals_for_string = min(num_decimals_from_step, 4) 
    
    # Değeri tam olarak precision_step'in katı olacak şekilde yuvarla
    # Bu, Decimal kütüphanesinin ana yuvarlama mantığıdır.
    rounded_d_value_by_step = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    
    # Son olarak, yuvarlanmış değeri belirlenen ondalık basamak sayısıyla stringe dönüştür.
    # .normalize() kullanmıyoruz, çünkü borsalar sondaki sıfırları da isteyebilir.
    return f"{rounded_d_value_by_step:.{final_decimals_for_string}f}"


# === Ana Webhook Endpoint'i (TradingView Sinyallerini İşler) ===
@app.route("/webhook", methods=["POST"])
def webhook():
    # --- JSON Ayrıştırma ve Hata Yakalama ---
    data = None
    try:
        # force=True: Content-Type headerını yok sayar, yine de JSON olarak ayrıştırmaya çalışır.
        # silent=True: Hata durumunda None döndürür, exception fırlatmaz.
        data = request.get_json(force=True, silent=True) 
        
        if data is None: 
            raw_data = request.get_data(as_text=True)
            headers = dict(request.headers)
            error_msg = f"❗ Webhook verisi JSON olarak ayrıştırılamadı. Muhtemelen geçersiz JSON formatı.\n" \
                        f"Headers: <pre>{json.dumps(headers, indent=2)}</pre>\n" \
                        f"Raw Data (ilk 500 karakter): <pre>{raw_data[:500]}</pre>" 
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "JSON ayrıştırma hatası veya geçersiz veri"}), 400
        
        print(f"📩 Webhook verisi alındı: {data}")
        signal_message_for_telegram = f"<b>🔔 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>"
        send_telegram_message_to_queue(signal_message_for_telegram)

        # --- Sinyal Verilerini Çekme ---
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") 
        tp = data.get("tp") 

        # Giriş verilerini kontrol et (None kontrolü)
        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Eksik sinyal verisi! Symbol: {symbol}, Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}"
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Side (işlem yönü) kontrolü
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
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Sembol temizliği
        if ":" in symbol:
            symbol = symbol.split(":")[-1]
            print(f"Sembol TradingView prefix'inden temizlendi: {symbol}")
            send_telegram_message_to_queue(f"ℹ️ Sembol prefix temizlendi: <b>{symbol}</b>")
        
        if symbol.endswith(".P"):
            symbol = symbol[:-2] 
            print(f"Sembol '.P' ekinden temizlendi: {symbol}")
            send_telegram_message_to_queue(f"ℹ️ Sembol '.P' eki temizlendi: <b>{symbol}</b>")
        
        symbol = symbol.upper()
        send_telegram_message_to_queue(f"ℹ️ Nihai işlem sembolü: <b>{symbol}</b>")
        

        # Fiyat verilerini float'a çevirme
        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (ValueError, TypeError) as ve:
            error_msg = f"❗ Fiyat verileri sayıya çevrilemedi: Entry={entry}, SL={sl}, TP={tp}. Hata: {ve}. Lütfen Pine Script alert formatını kontrol edin."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "Geçersiz fiyat formatı"}), 400

        # Bybit API oturumu
        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        # Bybit'ten enstrüman bilgilerini al
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
                elif 'minTradingQty' in lot_filter: 
                    lot_size = float(lot_filter['minTradingQty'])

                if 'minOrderQty' in lot_filter:
                    min_order_qty = float(lot_filter['minOrderQty'])
                
                if 'maxOrderQty' in lot_filter: 
                    max_order_qty = float(lot_filter['maxOrderQty'])

                if 'minOrderValue' in lot_filter: 
                    min_order_value = float(lot_filter['minOrderValue'])

                print(f"Bybit {symbol} için API'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}, Max Order Qty: {max_order_qty}, Min Order Value: {min_order_value}")
                send_telegram_message_to_queue(f"ℹ️ {symbol} için Bybit hassasiyetleri alındı:\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>\nMin Emir Miktarı: <code>{min_order_qty}</code>\nMax Emir Miktarı: <code>{max_order_qty}</code>\nMin Emir Değeri: <code>{min_order_value} USDT</code>")
            else:
                print(f"Uyarı: {symbol} için Bybit hassasiyet bilgisi bulunamadı. API yanıtı: {exchange_info_response}. Varsayılanlar kullanılıyor.")
                send_telegram_message_to_queue(f"⚠️ {symbol} için Bybit hassasiyet bilgisi alınamadı. Varsayılanlar kullanılıyor.")

        except Exception as api_e:
            error_msg_api = f"Bybit sembol/hassasiyet bilgisi alınırken hata: {api_e}. Varsayılan hassasiyetler kullanılıyor."
            print(error_msg_api)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg_api}")

        # Fiyatları Bybit'in hassasiyetine yuvarla (float olarak kalırlar)
        entry_rounded = round_to_precision(entry, tick_size)
        sl_rounded = round_to_precision(sl, tick_size)
        tp_rounded = round_to_precision(tp, tick_size)
        
        # === KRİTİK KONTROL: YUVARLAMA SONRASI SL VE ENTRY AYNI MI? ===
        if str(entry_rounded) == str(sl_rounded):
            error_msg = f"❗ GİRİŞ FİYATI ({entry_rounded}) ve STOP LOSS FİYATI ({sl_rounded}) YUVARLAMA SONRASI AYNI GELDİ. Risk anlamsız olduğu için emir gönderilmiyor. Lütfen Pine Script stratejinizi kontrol edin ve SL'nin Girişten belirgin bir mesafede olduğundan emin olun."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # === POZİSYON BÜYÜKLÜĞÜ AYARI (Kullanıcının mevcut çalışan tercihine göre 40$ ile işlem açacak) ===
        sabitMiktar_usd = 40.0 # Pozisyon değeri sabit olarak 40$ olarak ayarlandı

        if entry_rounded == 0:
            error_msg = "❗ Giriş fiyatı sıfır geldi. Pozisyon miktarı hesaplanamıyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Adet miktarını sabit dolar değerine göre hesapla
        calculated_quantity_float = sabitMiktar_usd / entry_rounded
        
        # Miktarı lot_size'ın katı olacak şekilde yuvarla ve string'e dönüştür
        quantity_str_for_bybit = round_quantity_to_exchange_precision(calculated_quantity_float, lot_size)
        
        # Debug mesajları
        send_telegram_message_to_queue(f"DEBUG: Hedef Pozisyon Değeri ({sabitMiktar_usd}$), Giriş Fiyatı ({entry_rounded}). Ham hesaplanan miktar: {calculated_quantity_float:.8f}. Bybit'e giden son miktar (string): {quantity_str_for_bybit}")

        # Limit kontrollerini yapmak için string'i tekrar float'a çeviriyoruz
        quantity_float_for_checks = float(quantity_str_for_bybit)

        # Yuvarlandıktan sonra limit kontrollerini tekrar yap (float haliyle)
        if quantity_float_for_checks < min_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) minimum emir miktarı ({min_order_qty}) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400
        
        if quantity_float_for_checks > max_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) maksimum emir miktarı ({max_order_qty}) üstündedir. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        if quantity_float_for_checks <= 0: 
            error_msg = f"❗ Nihai hesaplanan miktar sıfır veya negatif ({quantity_float_for_checks}). Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Gizli minimum işlem değerini kontrol etmek için 
        implied_min_order_value = max(10.0, min_order_value) 

        order_value = quantity_float_for_checks * entry_rounded
        if implied_min_order_value > 0 and order_value < implied_min_order_value:
            error_msg = f"❗ Nihai pozisyon değeri ({order_value:.2f} USDT) belirlenen minimum emir değeri ({implied_min_order_value} USDT) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        actual_risk_if_sl_hit = abs(quantity_float_for_checks * (entry_rounded - sl_rounded))
        
        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (Hedef Poz. Değeri: ${sabitMiktar_usd:.2f}):</b>\n" 
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_bybit.upper()}\n" 
            f"<b>Miktar (Adet):</b> {quantity_float_for_checks}\n" 
            f"<b>Giriş Fiyatı:</b> {entry_rounded}\n" 
            f"<b>Stop Loss (SL):</b> {sl_rounded}\n" 
            f"<b>Take Profit (TP):</b> {tp_rounded}\n" 
            f"<b>Hesaplanan Fiili Risk (SL vurulursa):</b> ${actual_risk_if_sl_hit:.2f}" 
        )
        send_telegram_message_to_queue(trade_summary)
        
        # Bybit'e emir gönder
        order = session.place_order(
            category="linear", 
            symbol=symbol,
            side=side_for_bybit, 
            orderType="Market", 
            qty=quantity_str_for_bybit,  # Bybit'e string hali gönderildi
            timeInForce="GoodTillCancel", 
            stopLoss=str(sl_rounded),   
            takeProfit=str(tp_rounded)  
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
            send_telegram_message_to_queue(success_message) 
            return jsonify({"status": "ok", "order": order})
        else:
            error_response_msg = order.get('retMsg', 'Bilinmeyen Bybit hatası.')
            full_error_details = json.dumps(order, indent=2) 
            error_message_telegram = f"<b>🚨 Bybit Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
            send_telegram_message_to_queue(error_message_telegram) 
            return jsonify({"status": "error", "message": error_response_msg}), 500

    except Exception as e:
        # Genel hata yakalama, traceback ile detaylı bilgi logla
        error_message_full = f"🔥 KRİTİK GENEL HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        send_telegram_message_to_queue(f"<b>🚨 KRİTİK BOT HATASI!</b>\n<pre>{error_message_full}</pre>") 
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

if __name__ == "__main__":
    app.run(debug=True, port=os.getenv("PORT", 5000))
