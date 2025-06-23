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
import ccxt # ccxt kütüphanesini import ediyoruz

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
# ccxt için testnet modu:
MEXC_TESTNET_MODE = os.getenv("MEXC_TESTNET_MODE", "False").lower() in ('true', '1', 't') 


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
    if precision_step <= 0: 
        return str(float(value))

    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(str(precision_step))

    num_decimals_from_step = abs(d_precision_step.as_tuple().exponent)
    
    rounded_d_value_by_step = (d_value / d_precision_step).quantize(decimal.Decimal('1'), rounding=decimal.ROUND_HALF_UP) * d_precision_step
    
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
            send_telegram_message_to_queue(f"� {exchange_name.upper()} Bot Hatası: {error_msg}")
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
            
            # ccxt ile MEXC Futures başlatma
            exchange_session = ccxt.mexc({
                'apiKey': MEXC_API_KEY,
                'secret': MEXC_API_SECRET,
                'options': {
                    'defaultType': 'future', # Vadeli işlemler için 'future' veya 'swap'
                },
                'enableRateLimit': True, # Oran limitlerini yönetmek için
            })
            if MEXC_TESTNET_MODE:
                # ccxt testnet URL'i için özel ayar gerekebilir, MEXC dokümantasyonunu kontrol edin.
                # Genellikle 'urls' içinde 'api', 'test' gibi alanlar bulunur.
                # Örneğin: exchange_session.set_sandbox_mode(True)
                pass 
            print(f"ℹ️ MEXC Futures Session (ccxt) başlatıldı.")

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
            
            elif exchange_name == "mexc":
                # ccxt ile enstrüman bilgisi alma (load_markets çağrısı)
                # Bu, sembollerin hassasiyetlerini, min/max miktarlarını vb. içerir.
                markets = exchange_session.load_markets()
                market_info = markets.get(symbol)
                
                if market_info:
                    tick_size = market_info['precision']['price']
                    # ccxt'de qtyStep genellikle 'amount' hassasiyetidir.
                    lot_size = market_info['precision']['amount']
                    min_order_qty = market_info['limits']['amount']['min']
                    max_order_qty = market_info['limits']['amount']['max']
                    min_order_value = market_info['limits']['cost']['min'] # Minimum notional value

                    print(f"MEXC {symbol} için CCXT'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}, Max Order Qty: {max_order_qty}, Min Order Value: {min_order_value}")
                    send_telegram_message_to_queue(f"ℹ️ {symbol} için MEXC hassasiyetleri alındı (CCXT):\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>\nMin Emir Miktarı: <code>{min_order_qty}</code>\nMax Emir Miktarı: <code>{max_order_qty}</code>\nMin Emir Değeri: <code>{min_order_value} USDT</code>")
                else:
                    print(f"Uyarı: {symbol} için MEXC hassasiyet bilgisi (CCXT) bulunamadı. Varsayılanlar kullanılıyor. Market info: {market_info}")
                    send_telegram_message_to_queue(f"⚠️ {symbol} için MEXC hassasiyet bilgisi (CCXT) alınamadı. Varsayılanlar kullanılıyor.")

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
            f"<b>📢 YENİ EMİR SİPARİŞİ ({exchange_name.upper()}, Hedef Poz. Değeri: ${sabitMiktar_usd:.2f}):</b>\n" 
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
            # ccxt ile emir gönderme
            # ccxt trade side'ları: 'buy' veya 'sell'
            ccxt_side = "buy" if side_for_exchange == "Buy" else "sell"
            
            # MEXC için varsayılan kaldıraç ve pozisyon modu (ihtiyaca göre ayarlayın)
            # ccxt'de leverage ve marginMode ayarları bazen exchange.set_leverage() veya exchange.set_margin_mode() ile yapılır.
            # Veya create_order params içinde gönderilir.
            # Şu anki mexc implementasyonunda bunları direkt create_order içinde gönderiyoruz.
            LEVERAGE = 1 # Varsayılan kaldıraç, MEXC'te manuel ayarlamanız gerekebilir.
            # open_type (pozisyon modu) ccxt'de genellikle 'marginMode' olarak geçer: 'isolated' veya 'cross'
            MARGIN_MODE = 'isolated' # Varsayılan izole.
            
            # Parametreler ccxt'nin MEXC özel parametrelerini içerir
            params = {
                'leverage': LEVERAGE,
                'marginMode': MARGIN_MODE, # 'isolated' veya 'cross'
            }

            try:
                # ccxt.create_order yöntemiyle piyasa emri gönder
                # Piyasa emrinde price alanı None olur.
                order = exchange_session.create_order(
                    symbol=symbol,
                    type='market', # Piyasa emri
                    side=ccxt_side,
                    amount=quantity_float_for_checks, # ccxt'de float miktar kullanılır
                    price=None, # Piyasa emri olduğu için fiyat belirtilmez
                    params=params
                )

                print(f"✅ MEXC Piyasa Emri gönderildi (CCXT): {order}")

                # ccxt order yapısından ID çekme
                order_id = order.get('id') if order else 'N/A'
                
                # TP/SL emirlerini gönderme (MEXC için ayrı ayrı)
                # ccxt'de stop-loss ve take-profit emirleri genellikle 'stop' tipi emirlerle veya 
                # Unified Margin/Futures için 'trigger' tipi emirlerle yapılır.
                # MEXC için bunlar genellikle 'stop_loss_limit', 'take_profit_limit' veya 'stop_loss_market', 'take_profit_market' olur.
                # Eğer MEXC futures API'si basit 'stop' tipi emirleri destekliyorsa (triggerPrice ile):
                
                # Take Profit Emri
                if tp_rounded:
                    try:
                        tp_params = {'triggerPrice': float(tp_rounded), 'reduceOnly': True}
                        tp_order = exchange_session.create_order(
                            symbol=symbol,
                            type='take_profit', # Veya 'limit'/'market' ve 'stopPrice' kullanarak
                            side= 'sell' if ccxt_side == 'buy' else 'buy', # Pozisyon kapatmak için ters yönde emir
                            amount=quantity_float_for_checks,
                            price=float(tp_rounded), # Limit fiyat olarak TP fiyatı
                            params=tp_params
                        )
                        print(f"✅ MEXC TP Emri gönderildi (CCXT): {tp_order}")
                        send_telegram_message_to_queue(f"✅ MEXC TP emri ({symbol}): ID: {tp_order.get('id', 'N/A')}, Durum: {tp_order.get('status', 'N/A')}")
                    except Exception as e:
                        print(f"🔥 MEXC TP emri gönderilirken hata (CCXT): {e}")
                        send_telegram_message_to_queue(f"🚨 MEXC TP emri gönderilirken hata ({symbol}, CCXT): {e}")

                # Stop Loss Emri
                if sl_rounded:
                    try:
                        sl_params = {'triggerPrice': float(sl_rounded), 'reduceOnly': True}
                        sl_order = exchange_session.create_order(
                            symbol=symbol,
                            type='stop_loss', # Veya 'limit'/'market' ve 'stopPrice' kullanarak
                            side= 'sell' if ccxt_side == 'buy' else 'buy', # Pozisyon kapatmak için ters yönde emir
                            amount=quantity_float_for_checks,
                            price=float(sl_rounded), # Limit fiyat olarak SL fiyatı
                            params=sl_params
                        )
                        print(f"✅ MEXC SL Emri gönderildi (CCXT): {sl_order}")
                        send_telegram_message_to_queue(f"✅ MEXC SL emri ({symbol}): ID: {sl_order.get('id', 'N/A')}, Durum: {sl_order.get('status', 'N/A')}")
                    except Exception as e:
                        print(f"🔥 MEXC SL emri gönderilirken hata (CCXT): {e}")
                        send_telegram_message_to_queue(f"🚨 MEXC SL emri gönderilirken hata ({symbol}, CCXT): {e}")
                    
                # Başarılı MEXC ana emir mesajı
                success_message = (
                    f"<b>✅ MEXC Emir Başarılı (CCXT)!</b>\n"
                    f"<b>Emir ID:</b> <code>{order_id}</code>\n"
                    f"<b>Sembol:</b> {symbol}\n"
                    f"<b>Yön:</b> {side_for_exchange}\n"
                    f"<b>Miktar:</b> {quantity_str_for_exchange}\n"
                    f"<b>Durum:</b> {order.get('status', 'Başarılı')}" 
                )
                send_telegram_message_to_queue(success_message)
                return {"status": "ok", "order": order}, 200
            except ccxt.NetworkError as e:
                error_response_msg = f"Ağ Hatası: {e}"
                full_error_details = json.dumps({"error": str(e), "traceback": traceback.format_exc()}, indent=2)
                error_message_telegram = f"<b>🚨 MEXC Emir Ağ Hatası (CCXT):</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
                send_telegram_message_to_queue(error_message_telegram)
                return {"status": "error", "message": error_response_msg}, 500
            except ccxt.ExchangeError as e:
                error_response_msg = f"Borsa Hatası: {e}"
                full_error_details = json.dumps({"error": str(e), "traceback": traceback.format_exc()}, indent=2)
                error_message_telegram = f"<b>🚨 MEXC Emir Borsa Hatası (CCXT):</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
                send_telegram_message_to_queue(error_message_telegram)
                return {"status": "error", "message": error_response_msg}, 500
            except Exception as mexc_order_e:
                error_msg_mexc_order = f"MEXC emir gönderilirken kritik hata (CCXT): {mexc_order_e}\nTraceback: {traceback.format_exc()}"
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
