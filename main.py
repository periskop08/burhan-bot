import json
import traceback
import requests
from flask import Flask, request, jsonify
import os
import decimal
import time
import threading
from queue import Queue
from pybit.unified_trading import HTTP  # Bybit API istemcisi
import hmac
import hashlib

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

MEXC_API_KEY = os.getenv("MEXC_API_KEY")
MEXC_API_SECRET = os.getenv("MEXC_API_SECRET")

BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')
MEXC_TESTNET_MODE = os.getenv("MEXC_TESTNET_MODE", "False").lower() in ('true', '1', 't')

telegram_message_queue = Queue()
LAST_TELEGRAM_MESSAGE_TIME = 0
TELEGRAM_RATE_LIMIT_DELAY = 1.0


def telegram_message_sender():
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
                    LAST_TELEGRAM_MESSAGE_TIME = time.time()
                except requests.exceptions.RequestException as e:
                    print(f"🔥 Telegram mesajı gönderilirken hata oluştu: {e}.")
                finally:
                    telegram_message_queue.task_done()
            else:
                sleep_duration = TELEGRAM_RATE_LIMIT_DELAY - time_since_last_message
                time.sleep(sleep_duration)
        else:
            time.sleep(0.1)


def send_telegram_message_to_queue(message_text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram BOT_TOKEN veya CHAT_ID tanımlı değil.")
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


def round_quantity_to_exchange_precision(value, precision_step):
    if value is None:
        return ""
    if precision_step <= 0:
        return str(float(value))
    d_value = decimal.Decimal(str(value))
    d_precision_step = decimal.Decimal(str(precision_step))
    num_decimals_from_step = abs(d_precision_step.as_tuple().exponent)
    rounded_d_value_by_step = (d_value / d_precision_step).quantize(decimal.Decimal('1'),
                                                                    rounding=decimal.ROUND_HALF_UP) * d_precision_step
    if abs(rounded_d_value_by_step) >= 1000:
        final_decimals = min(num_decimals_from_step, 0)
    elif abs(rounded_d_value_by_step) >= 100:
        final_decimals = min(num_decimals_from_step, 1)
    elif abs(rounded_d_value_by_step) >= 1:
        final_decimals = min(num_decimals_from_step, 2)
    else:
        final_decimals = min(num_decimals_from_step, 6)
    return f"{rounded_d_value_by_step:.{final_decimals}f}"


class MEXCSession:
    def __init__(self, api_key, api_secret, testnet=False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://contract.mexc.com"

    def _sign(self, params):
        sorted_params = sorted(params.items())
        query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
        return hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def place_order(self, category, symbol, side, orderType, qty, timeInForce, stopLoss=None, takeProfit=None):
        url = f"{self.base_url}/api/v1/private/order/submit"
        params = {
            "api_key": self.api_key,
            "req_time": int(time.time() * 1000),
            "symbol": symbol,
            "price": 0,
            "vol": qty,
            "side": 1 if side.lower() == "buy" else 2,
            "type": 1,
            "open_type": 1,
            "position_id": 0,
            "leverage": 10,
            "external_oid": f"bot-{int(time.time())}"
        }
        params["sign"] = self._sign(params)
        try:
            response = requests.post(url, data=params)
            return response.json()
        except Exception as e:
            return {
                "retCode": -1,
                "retMsg": f"API çağrısı hatası: {str(e)}",
                "result": {}
            }

    def get_instruments_info(self, category, symbol):
        return {
            'retCode': 0,
            'result': {'list': [{'priceFilter': {'tickSize': '0.1'},
                                 'lotFilter': {'qtyStep': '0.0001', 'minOrderQty': '0.001', 'maxOrderQty': '1000',
                                               'minOrderValue': '5'}}]}
        }


# Devamında handle_trade_signal ve Flask route'ların tümü aynı kalıyor.
# Yukarıdaki güncellemeyle artık MEXC tarafı gerçek emir gönderebilir hale geldi.

# === İşlem Sinyalini Belirli Bir Borsada Yürütme Fonksiyonu ===
def handle_trade_signal(exchange_name, data):
    exchange_session = None
    exchange_api_key = None
    exchange_api_secret = None
    exchange_testnet_mode = False

    # Exchange'e göre API kimlik bilgilerini ve oturumu ayarla
    if exchange_name == "bybit":
        exchange_api_key = BYBIT_API_KEY
        exchange_api_secret = BYBIT_API_SECRET
        exchange_testnet_mode = BYBIT_TESTNET_MODE
        exchange_session = HTTP(api_key=exchange_api_key, api_secret=exchange_api_secret, testnet=exchange_testnet_mode)
    elif exchange_name == "mexc":
        exchange_api_key = MEXC_API_KEY
        exchange_api_secret = MEXC_API_SECRET
        exchange_testnet_mode = MEXC_TESTNET_MODE
        # Gerçek MEXC SDK'sı entegre edildiğinde bu satırı değiştirmelisin:
        exchange_session = MEXCSession(api_key=exchange_api_key, api_secret=exchange_api_secret,
                                       testnet=exchange_testnet_mode)
    else:
        error_msg = f"❗ Tanımlanamayan borsa adı: {exchange_name}"
        print(error_msg)
        send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
        return {"status": "error", "message": error_msg}, 400

    order = None  # `order` değişkenini fonksiyon başında inisiyalize et

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

        # Sembol temizliği (varsa prefix'i kaldır)
        if ":" in symbol:
            original_symbol = symbol
            symbol = symbol.split(":")[-1]  # Prefix'i at
            print(f"Sembol prefix'ten temizlendi: {original_symbol} -> {symbol}")
            send_telegram_message_to_queue(f"ℹ️ {exchange_name.upper()} Sembol prefix temizlendi: <b>{symbol}</b>")

        if symbol.endswith(".P"):  # TradingView'den gelen bazı pariteler için '.P' eki olabilir
            symbol = symbol[:-2]
            print(f"Sembol '.P' ekinden temizlendi: {symbol}")
            send_telegram_message_to_queue(f"ℹ️ {exchange_name.upper()} Sembol '.P' eki temizlendi: <b>{symbol}</b>")

        symbol = symbol.upper()  # Tüm sembolleri büyük harfe çevir
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

        # Borsadan enstrüman bilgilerini al
        tick_size = 0.000001
        lot_size = 0.000001
        min_order_qty = 0.0
        max_order_qty = float('inf')
        min_order_value = 0.0

        try:
            exchange_info_response = exchange_session.get_instruments_info(category="linear", symbol=symbol)
            if exchange_info_response and exchange_info_response['retCode'] == 0 and exchange_info_response['result'][
                'list']:
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

                print(
                    f"{exchange_name.upper()} {symbol} için API'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}, Min Order Qty: {min_order_qty}, Max Order Qty: {max_order_qty}, Min Order Value: {min_order_value}")
                send_telegram_message_to_queue(
                    f"ℹ️ {exchange_name.upper()} {symbol} için hassasiyetler alındı:\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>\nMin Emir Miktarı: <code>{min_order_qty}</code>\nMax Emir Miktarı: <code>{max_order_qty}</code>\nMin Emir Değeri: <code>{min_order_value} USDT</code>")
            else:
                print(
                    f"Uyarı: {exchange_name.upper()} {symbol} için hassasiyet bilgisi bulunamadı. API yanıtı: {exchange_info_response}. Varsayılanlar kullanılıyor.")
                send_telegram_message_to_queue(
                    f"⚠️ {exchange_name.upper()} {symbol} için hassasiyet bilgisi alınamadı. Varsayılanlar kullanılıyor.")

        except Exception as api_e:
            error_msg_api = f"{exchange_name.upper()} sembol/hassasiyet bilgisi alınırken hata: {api_e}. Varsayılan hassasiyetler kullanılıyor."
            print(error_msg_api)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg_api}")

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
        sabitMiktar_usd = 400.0  # Pozisyon değeri sabit olarak 40$ olarak ayarlandı
        

        if entry_rounded == 0:
            error_msg = "❗ Giriş fiyatı sıfır geldi. Pozisyon miktarı hesaplanamıyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 {exchange_name.upper()} Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Adet miktarını sabit dolar değerine göre hesapla
        calculated_quantity_float = sabitMiktar_usd / entry_rounded

        # Miktarı lot_size'ın katı olacak şekilde yuvarla ve string'e dönüştür
        quantity_str_for_exchange = round_quantity_to_exchange_precision(calculated_quantity_float, lot_size)

        # Debug mesajları
        send_telegram_message_to_queue(
            f"DEBUG: {exchange_name.upper()} Hedef Pozisyon Değeri ({sabitMiktar_usd}$), Giriş Fiyatı ({entry_rounded}). Ham hesaplanan miktar: {calculated_quantity_float:.8f}. Bybit'e giden son miktar (string): {quantity_str_for_exchange}")

        # Limit kontrollerini yapmak için string'i tekrar float'a çeviriyoruz
        quantity_float_for_checks = float(quantity_str_for_exchange)

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
        implied_min_order_value = max(10.0, min_order_value)

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

        if side_for_exchange == "Buy":
            ondalik_sayisi = len(str(entry).split('.')[-1])
            sl_rounded = round(entry * 0.99, ondalik_sayisi)
            ondalik_sayisi2 = len(str(entry).split('.')[-1])
            tp_rounded = round(entry * 1.02, ondalik_sayisi2)
        else:
            ondalik_sayisi = len(str(entry).split('.')[-1])
            sl_rounded = round(entry * 1.01, ondalik_sayisi)
            ondalik_sayisi2 = len(str(entry).split('.')[-1])
            tp_rounded = round(entry * 0.98, ondalik_sayisi2)
            
        # Borsaya emir gönder
        order = exchange_session.place_order(
            category="linear",  # Vadeli işlemler için 'linear', spot için 'spot' - MEXC için kontrol edin
            symbol=symbol,
            side=side_for_exchange,
            orderType="Market",
            qty=quantity_str_for_exchange,  # Borsaya string hali gönderildi
            timeInForce="GoodTillCancel",
            stopLoss=str(sl_rounded),
            takeProfit=str(tp_rounded)
        )

        print(f"✅ {exchange_name.upper()} Emir gönderildi: {order}")

        if order and order.get('retCode') == 0:
            order_info = order.get('result', {})
            success_message = (
                f"<b>✅ {exchange_name.upper()} Emir Başarılı!</b>\n"
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
            error_response_msg = order.get('retMsg', 'Bilinmeyen borsa hatası.')
            full_error_details = json.dumps(order, indent=2)
            error_message_telegram = f"<b>� {exchange_name.upper()} Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity_float_for_checks}\n<pre>{full_error_details}</pre>"
            send_telegram_message_to_queue(error_message_telegram)
            return {"status": "error", "message": error_response_msg}, 500

    except Exception as e:
        error_message_full = f"🔥 {exchange_name.upper()} GENEL HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        # Eğer order değişkeni burada tanımlı değilse, sadece hata mesajını gönder.
        if 'order' not in locals() or order is None:
            send_telegram_message_to_queue(
                f"<b>🚨 KRİTİK {exchange_name.upper()} BOT HATASI! (order tanımsız)</b>\n<pre>{error_message_full}</pre>")
        else:
            # Eğer order tanımlı ama bir hata varsa, borsa hata detaylarını da ekleyelim.
            error_response_msg = order.get('retMsg', 'Bilinmeyen borsa hatası.') if isinstance(order, dict) else str(
                order)
            send_telegram_message_to_queue(
                f"<b>🚨 KRİTİK {exchange_name.upper()} BOT HATASI!</b>\n{error_response_msg}\n<pre>{error_message_full}</pre>")

        return {"status": "error", "message": str(e)}, 500


# === Ana Webhook Endpoint'i (TradingView Sinyallerini Alır ve Yönlendirir) ===
@app.route("/webhook", methods=["POST"])
def webhook():
    data = None
    raw_data_text = None
    headers = dict(request.headers)

    try:
        # 🔍 Gelen ham veriyi oku
        raw_data_text = request.get_data(as_text=True)
        print("📨 Webhook tetiklendi!")
        print("📦 Ham veri:", raw_data_text)

        # ❗ JSON ayrıştırması
        data = json.loads(raw_data_text)
        print("✅ JSON ayrıştırıldı:", data)

        # 🔄 Burada normal işlem devam eder (örneğin işlem açma, loglama vb.)
        return jsonify({"status": "success", "message": "Webhook alındı"}), 200

    except json.JSONDecodeError as e:
        error_msg = f"❗ Webhook verisi JSON olarak ayrıştırılamadı.\nHata: {e}\n" \
                    f"📋 Headers:\n<pre>{json.dumps(headers, indent=2)}</pre>\n" \
                    f"📦 Ham Veri (ilk 500 karakter):\n<pre>{raw_data_text[:500] if raw_data_text else 'N/A'}</pre>"
        print(error_msg)
        send_telegram_message_to_queue(f"🚨 Bot Hatası:\n{error_msg}")
        return jsonify({"status": "error", "message": "JSON ayrıştırma hatası"}), 400

    except Exception as e:
        error_msg = f"❗ Webhook işlenirken beklenmedik bir hata oluştu.\nHata: {e}\n" \
                    f"📋 Headers:\n<pre>{json.dumps(headers, indent=2)}</pre>\n" \
                    f"📦 Ham Veri (ilk 500 karakter):\n<pre>{raw_data_text[:500] if raw_data_text else 'N/A'}</pre>"
        print(error_msg)
        send_telegram_message_to_queue(f"🚨 Bot Hatası:\n{error_msg}")
        return jsonify({"status": "error", "message": "Webhook işleme hatası"}), 500

    # Ham sinyali Telegram'a gönder
    signal_message_for_telegram = f"<b>🔔 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>"
    send_telegram_message_to_queue(signal_message_for_telegram)

    symbol_from_tv = data.get("symbol", "").upper()
    exchange_to_use = "bybit"  # Varsayılan borsa

    # Sembol prefix'ine göre borsayı belirle
    if symbol_from_tv.startswith("MEXC:"):
        exchange_to_use = "mexc"
        data["symbol"] = symbol_from_tv[len("MEXC:"):].strip()  # Sembolü temizle
    elif symbol_from_tv.startswith("BYBIT:"):
        exchange_to_use = "bybit"
        data["symbol"] = symbol_from_tv[len("BYBIT:"):].strip()  # Sembolü temizle
    # Eğer başka bir prefix varsa (örn. BINANCE:), şimdilik Bybit'e yönlendirir,
    # ancak loglarda "Sembol prefix temizlendi" mesajını görürsünüz.
    # Gelecekte buraya başka borsalar da eklenebilir.

    print(f"Sinyal {exchange_to_use.upper()} borsası için yönlendiriliyor.")
    send_telegram_message_to_queue(
        f"➡️ Sinyal <b>{exchange_to_use.upper()}</b> borsası için yönlendirildi: <b>{data.get('symbol')}</b>")

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