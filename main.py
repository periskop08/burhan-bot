import json
import traceback
import requests
from flask import Flask, request, jsonify
import os
import decimal
import time
import threading
from queue import Queue 
from pybit.unified_trading import HTTP # Bybit API istemcisi
from mexc_api.mexc_futures import MEXCFutures # MEXC Futures API SDK'sını buradan import ediyoruz

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# MEXC için API anahtarları
MEXC_API_KEY = os.getenv("MEXC_API_KEY")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET")

BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')
# MEXC SDK'sı genellikle testnet parametresini içermez, ancak eğer kullanılıyorsa buradan kontrol edilebilir.
# Şu anki mexc_api kütüphanesinde testnet desteği direkt Client başlatılırken yok gibi görünüyor.
# Canlı API URL'ini varsayılan olarak kullanacaktır.
# MEXC_TESTNET_MODE = os.getenv("MEXC_TESTNET_MODE", "False").lower() in ('true', '1', 't') 


# === Telegram Mesaj Kuyruğu ve İşleyici ===
telegram_message_queue = Queue()
LAST_TELEGRAM_MESSAGE_TIME = 0
TELEGRAM_RATE_LIMIT_DELAY = 1.0 # Telegram'a en az 1 saniyede bir mesaj gönder

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
    # Exponent negatif olduğu için abs() kullanıyoruz: Decimal('0.000001').as_tuple().exponent == -6
    # Bu, lot_size'ın sağladığı kesin ondalık basamak sayısını bulur.
    num_decimals_from_step = abs(d_precision_step.as_tuple().exponent)
    
    # Değeri tam olarak precision_step'in katı olacak şekilde yuvarla
    rounded_d_value_by_step = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    
    # Son olarak, yuvarlanmış değeri belirlenen ondalık basamak sayısıyla stringe dönüştür.
    # Bu, borsanın tam hassasiyet beklentisini karşılamalıdır.
    return f"{rounded_d_value_by_step:.{num_decimals_from_step}f}"


# === İşlem Sinyalini Belirli Bir Borsada Yürütme Fonksiyonu ===
def handle_trade_signal(exchange_name, data):
    exchange_session = None
    order = None # `order` değişkenini fonksiyon başında inisiyalize et

    try:
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") 
        tp = data.get("tp") 

        # Giriş verilerini kontrol et
        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Eksik sinyal verisi! Symbol: {symbol}, Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}"
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Side (işlem yönü) kontrolü
        side_for_exchange = ""
        if side and side.lower() in ["buy", "long"]:
            side_for_exchange = "Buy"
        elif side and side.lower() in ["sell", "short"]:
            side_for_exchange = "Sell"
        else:
            error_msg = f"❗ Geçersiz işlem yönü (side): {side}. 'Buy', 'Sell', 'Long' veya 'Short' bekleniyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400
        
        # Sembol temizliği (varsa prefix'i kaldır) - Zaten webhook içinde yapılıyor, burada ek kontrole gerek yok.
        # Bu fonksiyon sadece temizlenmiş sembolle çalışmalı.
        symbol = symbol.upper() # Her ihtimale karşı büyük harfe çevir
        send_telegram_message_to_queue(f"ℹ️ {exchange_name.upper()} Nihai işlem sembolü: <b>{symbol}</b>")

        # Fiyat verilerini float'a çevirme
        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (ValueError, TypeError) as ve:
            error_msg = f"❗ Fiyat verileri sayıya çevrilemedi: Entry={entry}, SL={sl}, TP={tp}. Hata: {ve}. Lütfen Pine Script alert formatını kontrol edin."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": "Geçersiz fiyat formatı"}, 400

        # Exchange'e göre API kimlik bilgilerini ve oturumu ayarla
        if exchange_name == "bybit":
            if not BYBIT_API_KEY or not BYBIT_API_SECRET:
                error_msg = "🚨 Bybit Bot Hatası: Bybit API Anahtarları tanımlı değil. Lütfen ortam değişkenlerini kontrol edin."
                send_telegram_message_to_queue(error_msg)
                return {"status": "error", "message": error_msg}, 400
            exchange_session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)
            print(f"ℹ️ Bybit Session başlatıldı (Testnet: {BYBIT_TESTNET_MODE})")

        elif exchange_name == "mexc":
            if not MEXC_API_KEY or not MEXC_API_SECRET:
                error_msg = "🚨 MEXC Bot Hatası: MEXC API Anahtarları tanımlı değil. Lütfen ortam değişkenlerini kontrol edin."
                send_telegram_message_to_queue(error_msg)
                return {"status": "error", "message": error_msg}, 400
            
            # Gerçek MEXC Futures Client başlatma
            # mexc_api.mexc_futures kütüphanesinin testnet desteği dokümantasyonunda belirtilmediği için
            # doğrudan MEXCFutures() çağırıyoruz. Canlı API'ye bağlanacaktır.
            exchange_session = MEXCFutures(api_key=MEXC_API_KEY, api_secret=MEXC_API_SECRET) 
            print(f"ℹ️ MEXC Futures Session başlatıldı.")

        else:
            error_msg = f"❗ Tanımlanamayan borsa adı: {exchange_name}"
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Bybit/MEXC'ten enstrüman bilgilerini al
        tick_size = 0.000001 
        lot_size = 0.000001  
        min_order_qty = 0.0  
        max_order_qty = float('inf') 
        min_order_value = 0.0 
        
        try:
            if exchange_name == "bybit":
                exchange_info_response = exchange_session.get_instruments_info(category="linear", symbol=symbol)
                if exchange_info_response and exchange_info_response.get('retCode') == 0 and exchange_info_response.get('result', {}).get('list'):
                    instrument_info = exchange_info_response['result']['list'][0]
                    price_filter = instrument_info.get('priceFilter', {})
                    lot_filter = instrument_info.get('lotFilter', {})

                    if 'tickSize' in price_filter:
                        tick_size = float(price_filter['tickSize'])
                    if 'qtyStep' in lot_filter:
                        lot_size = float(lot_filter['qtyStep'])
                    elif 'minTradingQty' in lot_filter: # Bazı borsalar minTradingQty olarak dönebilir
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
            
            elif exchange_name == "mexc":
                # MEXC için enstrüman bilgisi alma (get_contract_detail)
                # Bu çağrı tüm sözleşmeleri döndürebilir, bu yüzden sembole göre filtrelememiz gerekecek.
                contract_details_response = exchange_session.get_contract_detail() 
                
                if contract_details_response and contract_details_response.get('code') == 200:
                    instrument_info_list = contract_details_response.get('data', [])
                    instrument_info = next((item for item in instrument_info_list if item.get("symbol") == symbol), None) # 'symbol' anahtarını güvenli erişimle kontrol et
                    
                    if instrument_info:
                        # MEXC'in priceScale ve volumeScale değerlerini float'a çevirme
                        # priceScale: 4 -> 0.0001, volumeScale: 0 -> 1 (1e-0)
                        tick_size = float("1e-" + str(instrument_info.get('priceScale', '6'))) 
                        lot_size = float("1e-" + str(instrument_info.get('volumeScale', '0'))) 
                        min_order_qty = float(instrument_info.get('minTradeNum', '0.0'))
                        max_order_qty = float(instrument_info.get('maxTradeNum', 'inf'))
                        min_order_value = float(instrument_info.get('minTradeAmount', '0.0')) # Minimum notional value
                        
                        print(f"MEXC {symbol} için API'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}, Max Order Qty: {max_order_qty}, Min Order Value: {min_order_value}")
                        send_telegram_message_to_queue(f"ℹ️ {symbol} için MEXC hassasiyetleri alındı:\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>\nMin Emir Miktarı: <code>{min_order_qty}</code>\nMax Emir Miktarı: <code>{max_order_qty}</code>\nMin Emir Değeri: <code>{min_order_value} USDT</code>")
                    else:
                        print(f"Uyarı: {symbol} için MEXC hassasiyet bilgisi bulunamadı. Sözleşme listesi: {instrument_info_list}. Varsayılanlar kullanılıyor.")
                        send_telegram_message_to_queue(f"⚠️ {symbol} için MEXC hassasiyet bilgisi alınamadı. Varsayılanlar kullanılıyor.")
                else:
                    print(f"Uyarı: MEXC tüm enstrüman bilgileri alınırken hata oluştu. API yanıtı: {contract_details_response}. Varsayılanlar kullanılıyor.")
                    send_telegram_message_to_queue(f"⚠️ MEXC tüm enstrüman bilgileri alınamadı. Varsayılanlar kullanılıyor.")

        except Exception as api_e:
            error_msg_api = f"{exchange_name.upper()} sembol/hassasiyet bilgisi alınırken hata: {api_e}. Varsayılan hassasiyetler kullanılıyor.\nTraceback: {traceback.format_exc()}"
            print(error_msg_api)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg_api}")
            return {"status": "error", "message": "Hassasiyet bilgisi alınırken hata"}, 500


        # Fiyatları borsanın hassasiyetine yuvarla (float olarak kalırlar)
        entry_rounded = round_to_precision(entry, tick_size)
        sl_rounded = round_to_precision(sl, tick_size)
        tp_rounded = round_to_precision(tp, tick_size)
        
        # === KRİTİK KONTROL: YUVARLAMA SONRASI SL VE ENTRY AYNI MI? ===
        if str(entry_rounded) == str(sl_rounded):
            error_msg = f"❗ GİRİŞ FİYATI ({entry_rounded}) ve STOP LOSS FİYATI ({sl_rounded}) YUVARLAMA SONRASI AYNI GELDİ. Risk anlamsız olduğu için emir gönderilmiyor. Lütfen Pine Script stratejinizi kontrol edin ve SL'nin Girişten belirgin bir mesafede olduğundan emin olun."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # === POZİSYON BÜYÜKLÜĞÜ AYARI (Kullanıcının tercihine göre 40$ ile işlem açacak) ===
        sabitMiktar_usd = 40.0 # Pozisyon değeri sabit olarak 40$ olarak ayarlandı

        if entry_rounded == 0:
            error_msg = "❗ Giriş fiyatı sıfır geldi. Pozisyon miktarı hesaplanamıyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Adet miktarını sabit dolar değerine göre hesapla
        calculated_quantity_float = sabitMiktar_usd / entry_rounded
        
        # Miktarı lot_size'ın katı olacak şekilde yuvarla ve string'e dönüştür
        quantity_str_for_exchange = round_quantity_to_exchange_precision(calculated_quantity_float, lot_size)
        
        # Limit kontrollerini yapmak için string'i tekrar float'a çeviriyoruz
        quantity_float_for_checks = float(quantity_str_for_exchange)

        # Debug mesajları (miktar yuvarlama sonrası)
        send_telegram_message_to_queue(f"DEBUG: {exchange_name.upper()} Hedef Poz. Değeri ({sabitMiktar_usd}$), Giriş Fiyatı ({entry_rounded}). Ham hesaplanan miktar: {calculated_quantity_float:.8f}. Gönderilecek miktar (string): {quantity_str_for_exchange} (Float: {quantity_float_for_checks})")


        # Yuvarlandıktan sonra limit kontrollerini tekrar yap (float haliyle)
        if quantity_float_for_checks < min_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) minimum emir miktarı ({min_order_qty}) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400
        
        if quantity_float_for_checks > max_order_qty:
            error_msg = f"❗ Nihai miktar ({quantity_float_for_checks}) maksimum emir miktarı ({max_order_qty}) üstündedir. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        if quantity_float_for_checks <= 0: 
            error_msg = f"❗ Nihai hesaplanan miktar sıfır veya negatif ({quantity_float_for_checks}). Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Gizli minimum işlem değerini kontrol etmek için 
        implied_min_order_value = max(10.0, min_order_value) # Bybit'in 10 USDT minimum notional değeri var, MEXC'in de olabilir.

        order_value = quantity_float_for_checks * entry_rounded
        if implied_min_order_value > 0 and order_value < implied_min_order_value:
            error_msg = f"❗ Nihai pozisyon değeri ({order_value:.2f} USDT) belirlenen minimum emir değeri ({implied_min_order_value} USDT) altındadır. Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        actual_risk_if_sl_hit = abs(quantity_float_for_checks * (entry_rounded - sl_rounded))
        
        trade_summary = (
            f"<b>� YENİ EMİR SİPARİŞİ ({exchange_name.upper()}, Hedef Poz. Değeri: ${sabitMiktar_usd:.2f}):</b>\n" 
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_exchange.upper()}\n" 
            f"<b>Miktar (Adet):</b> {quantity_float_for_checks}\n" 
            f"<b>Giriş Fiyatı:</b> {entry_rounded}\n" 
            f"<b>Stop Loss (SL):</b> {sl_rounded}\n" 
            f"<b>Take Profit (TP):</b> {tp_rounded}\n" 
            f"<b>Hesaplanan Fiili Risk (SL vurulursa):</b> ${actual_risk_if_sl_hit:.2f}" 
        )
        send_telegram_message_to_queue(trade_summary)
        
        # --- Borsaya emir gönder ---
        if exchange_name == "bybit":
            order = exchange_session.place_order(
                category="linear", 
                symbol=symbol,
                side=side_for_exchange, 
                orderType="Market", 
                qty=quantity_str_for_exchange,  
                timeInForce="GoodTillCancel", 
                stopLoss=str(sl_rounded),   
                takeProfit=str(tp_rounded)  
            )

            print(f"✅ Bybit Emir gönderildi: {order}")

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
                return {"status": "ok", "order": order}, 200
            else:
                error_response_msg = order.get('retMsg', 'Bilinmeyen Bybit hatası.')
                full_error_details = json.dumps(order, indent=2) 
                error_message_telegram = f"<b>🚨 Bybit Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
                send_telegram_message_to_queue(error_message_telegram) 
                return {"status": "error", "message": error_response_msg}, 500

        elif exchange_name == "mexc":
            # MEXC için emir gönderme
            # Side (Buy/Sell) to MEXC trade_type (1=open long, 3=open short)
            mexc_trade_type = 1 if side_for_exchange == "Buy" else 3
            
            # MEXC SDK'sının place_order metoduna göre parametreleri kontrol edin
            # Piyasa emri için price değeri 0 veya boş bırakılabilir.
            # Leveraj ve open_type değerlerini ihtiyaca göre ayarlayın!
            LEVERAGE = 1 # Varsayılan kaldıraç, MEXC'te manuel ayarlamanız gerekebilir.
            OPEN_TYPE = 1 # 1=ISOLATED (İzole), 2=CROSSED (Çapraz). Varsayılan izole.
            
            try:
                order = exchange_session.place_order(
                    symbol=symbol,
                    vol=float(quantity_str_for_exchange), # MEXC API genelde float bekler
                    trade_type=mexc_trade_type,
                    order_type=1, # 1 for MARKET order
                    leverage=LEVERAGE, 
                    open_type=OPEN_TYPE
                )

                print(f"✅ MEXC Piyasa Emri gönderildi: {order}")

                if order and order.get('code') == 200: # MEXC success code
                    # Piyasa emri başarılıysa TP/SL plan emirlerini gönder
                    order_id = order.get('data', {}).get('orderId')
                    # TP/SL için trade_type'ı tersine çeviriyoruz (pozisyon kapatma)
                    # Long pozisyon için TP/SL -> trade_type = 4 (close long)
                    # Short pozisyon için TP/SL -> trade_type = 2 (close short)
                    mexc_sl_tp_trade_type = 4 if side_for_exchange == "Buy" else 2 

                    # Take Profit Emri
                    if tp_rounded:
                        try:
                            tp_order = exchange_session.place_planorder(
                                symbol=symbol,
                                order_type=1, # 1 for Take Profit
                                trigger_price=float(tp_rounded),
                                vol=float(quantity_str_for_exchange),
                                side=mexc_sl_tp_trade_type,
                                trigger_type=1 # 1 for last price
                            )
                            print(f"✅ MEXC TP Emri gönderildi: {tp_order}")
                            send_telegram_message_to_queue(f"✅ MEXC TP emri ({symbol}): {tp_order.get('code')}: {tp_order.get('msg')}")
                        except Exception as e:
                            print(f"🔥 MEXC TP emri gönderilirken hata: {e}")
                            send_telegram_message_to_queue(f"🚨 MEXC TP emri gönderilirken hata ({symbol}): {e}")

                    # Stop Loss Emri
                    if sl_rounded:
                        try:
                            sl_order = exchange_session.place_planorder(
                                symbol=symbol,
                                order_type=2, # 2 for Stop Loss
                                trigger_price=float(sl_rounded),
                                vol=float(quantity_str_for_exchange),
                                side=mexc_sl_tp_trade_type,
                                trigger_type=1 # 1 for last price
                            )
                            print(f"✅ MEXC SL Emri gönderildi: {sl_order}")
                            send_telegram_message_to_queue(f"✅ MEXC SL emri ({symbol}): {sl_order.get('code')}: {sl_order.get('msg')}")
                        except Exception as e:
                            print(f"🔥 MEXC SL emri gönderilirken hata: {e}")
                            send_telegram_message_to_queue(f"🚨 MEXC SL emri gönderilirken hata ({symbol}): {e}")
                    
                    # Başarılı MEXC ana emir mesajı
                    success_message = (
                        f"<b>✅ MEXC Emir Başarılı!</b>\n"
                        f"<b>Emir ID:</b> <code>{order_id}</code>\n"
                        f"<b>Sembol:</b> {symbol}\n"
                        f"<b>Yön:</b> {side_for_exchange}\n"
                        f"<b>Miktar:</b> {quantity_str_for_exchange}\n"
                        f"<b>Durum:</b> Başarılı" # MEXC'in 'retMsg' yerine genel bir durum
                    )
                    send_telegram_message_to_queue(success_message)
                    return {"status": "ok", "order": order}, 200
                else:
                    error_response_msg = order.get('msg', 'Bilinmeyen MEXC hatası.')
                    full_error_details = json.dumps(order, indent=2) 
                    error_message_telegram = f"<b>🚨 MEXC Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
                    send_telegram_message_to_queue(error_message_telegram) 
                    return {"status": "error", "message": error_response_msg}, 500

            except Exception as mexc_order_e:
                error_msg_mexc_order = f"MEXC emir gönderilirken hata: {mexc_order_e}\nTraceback: {traceback.format_exc()}"
                print(error_msg_mexc_order)
                send_telegram_message_to_queue(f"🚨 MEXC Emir Gönderme KRİTİK HATA! ({symbol}): {error_msg_mexc_order}")
                return {"status": "error", "message": str(mexc_order_e)}, 500

    except Exception as e:
        # Genel hata yakalama, traceback ile detaylı bilgi logla
        error_message_full = f"🔥 KRİTİK GENEL HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        
        # Eğer order değişkeni burada tanımlı değilse, sadece hata mesajını gönder.
        if 'order' not in locals() or order is None:
            send_telegram_message_to_queue(f"<b>🚨 KRİTİK BOT HATASI! (order tanımsız)</b>\n<pre>{error_message_full}</pre>")
        else:
            # Eğer order tanımlı ama bir hata varsa, borsa hata detaylarını da ekleyelim.
            # Bu durum normalde yukarıdaki 'else' bloğunda yakalanır, ama yine de bir güvenlik önlemi.
            error_response_msg = order.get('retMsg', 'Bilinmeyen borsa hatası.') if isinstance(order, dict) else str(order)
            send_telegram_message_to_queue(f"<b>🚨 KRİTİK BOT HATASI!</b>\n{error_response_msg}\n<pre>{error_message_full}</pre>")
        
        return jsonify({"status": "error", "message": str(e)}), 500


# === Ana Webhook Endpoint'i (TradingView Sinyallerini Alır ve Yönlendirir) ===
@app.route("/webhook", methods=["POST"])
def webhook():
    data = None
    raw_data_text = None
    headers = dict(request.headers)

    try:
        raw_data_text = request.get_data(as_text=True)
        data = json.loads(raw_data_text)
        
    except json.JSONDecodeError as e:
        error_msg = f"❗ Webhook verisi JSON olarak ayrıştırılamadı. JSONDecodeError: {e}\n" \
                    f"Headers: <pre>{json.dumps(headers, indent=2)}</pre>\n" \
                    f"Raw Data (ilk 500 karakter): <pre>{raw_data_text[:500]}</pre>"
        print(error_msg)
        send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
        return jsonify({"status": "error", "message": "JSON ayrıştırma hatası veya geçersiz veri"}), 400
    except Exception as e:
        error_msg = f"❗ Webhook verisi alınırken/işlenirken beklenmedik hata: {e}\n" \
                    f"Headers: <pre>{json.dumps(headers, indent=2)}</pre>\n" \
                    f"Raw Data (ilk 500 karakter): <pre>{raw_data_text[:500] if raw_data_text else 'N/A'}</pre>"
        print(error_msg)
        send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
        return jsonify({"status": "error", "message": "Webhook işleme hatası"}), 500

    # Ham sinyali Telegram'a gönder
    signal_message_for_telegram = f"<b>🔔 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>"
    send_telegram_message_to_queue(signal_message_for_telegram)

    symbol_from_tv = data.get("symbol", "").upper()
    exchange_to_use = "bybit" # Varsayılan borsa

    # Sembol prefix'ine göre borsayı belirle
    if symbol_from_tv.startswith("MEXC:"):
        exchange_to_use = "mexc"
        data["symbol"] = symbol_from_tv[len("MEXC:"):].strip() # Sembolü temizle
    elif symbol_from_tv.startswith("BYBIT:"):
        exchange_to_use = "bybit"
        data["symbol"] = symbol_from_tv[len("BYBIT:"):].strip() # Sembolü temizle
    # Eğer başka bir prefix varsa, varsayılan olarak Bybit'e yönlendirilir.

    print(f"Sinyal {exchange_to_use.upper()} borsası için yönlendiriliyor.")
    send_telegram_message_to_queue(f"➡️ Sinyal <b>{exchange_to_use.upper()}</b> borsası için yönlendirildi: <b>{data.get('symbol')}</b>")

    # Yönlendirilen sinyali ilgili borsanın işlem yöneticisine gönder
    response_data, status_code = handle_trade_signal(exchange_to_use, data)
    return jsonify(response_data), status_code

# === Ana Sayfa (Botun Aktif Olduğunu Kontrol Etmek İçin) ===
@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

# === Uygulamayı Başlat ===
if __name__ == "__main__":
    app.run(debug=True, port=os.getenv("PORT", 5000))
