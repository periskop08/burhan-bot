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
from hashlib import sha256

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

BYBIT_TESTNET_MODE = os.getenv("BYBIT_TESTNET_MODE", "False").lower() in ('true', '1', 't')

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


class BingXSession:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://open-api.bingx.com"

    def get_sign(self, payload):
        signature = hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), digestmod=sha256).hexdigest()
        return signature

    def parse_param(self, params_map):
        sorted_keys = sorted(params_map)
        params_str = "&".join(["%s=%s" % (x, params_map[x]) for x in sorted_keys])
        if params_str != "":
            return params_str + "&timestamp=" + str(int(time.time() * 1000))
        else:
            return params_str + "timestamp=" + str(int(time.time() * 1000))

    def send_request(self, method, path, url_params, payload):
        try:
            url = "%s%s?%s&signature=%s" % (self.base_url, path, url_params, self.get_sign(url_params))
            headers = {
                'X-BX-APIKEY': self.api_key,
            }
            response = requests.request(method, url, headers=headers, data=payload)
            return response.json()
        except Exception as e:
            return {"code": -1, "msg": f"API çağrısı hatası: {str(e)}"}

    def place_order(self, symbol, side, quantity, stop_loss=None, take_profit=None):
        payload = {}
        path = '/openApi/swap/v2/trade/order'
        method = "POST"
        
        # BingX için side dönüşümü
        bingx_side = "BUY" if side.lower() in ["buy", "long"] else "SELL"
        position_side = "LONG" if side.lower() in ["buy", "long"] else "SHORT"
        
        # Take profit parametresi
        take_profit_param = ""
        if take_profit:
            take_profit_param = json.dumps({
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": float(take_profit),
                "price": float(take_profit),
                "workingType": "MARK_PRICE"
            })
        
        params_map = {
            "symbol": symbol,
            "side": bingx_side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": quantity
        }
        
        if take_profit_param:
            params_map["takeProfit"] = take_profit_param
            
        params_str = self.parse_param(params_map)
        return self.send_request(method, path, params_str, payload)

    def get_instruments_info(self, symbol):
        # BingX için basit enstrüman bilgisi (varsayılan değerler)
        return {
            'retCode': 0,
            'result': {'list': [{'priceFilter': {'tickSize': '0.1'},
                                 'lotFilter': {'qtyStep': '0.001', 'minOrderQty': '0.001', 'maxOrderQty': '1000',
                                               'minOrderValue': '5'}}]}
        }


# === İşlem Sinyalini Bybit ve BingX Borsalarında Yürütme Fonksiyonu ===
def handle_trade_signal(data):
    # Bybit oturumu
    bybit_session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)
    
    # BingX oturumu
    bingx_session = BingXSession(api_key=BINGX_API_KEY, api_secret=BINGX_SECRET_KEY)

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
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
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
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Sembol temizliği (varsa prefix'i kaldır)
        if ":" in symbol:
            original_symbol = symbol
            symbol = symbol.split(":")[-1]  # Prefix'i at
            print(f"Sembol prefix'ten temizlendi: {original_symbol} -> {symbol}")
            send_telegram_message_to_queue(f"ℹ️ Sembol prefix temizlendi: <b>{symbol}</b>")

        if symbol.endswith(".P"):  # TradingView'den gelen bazı pariteler için '.P' eki olabilir
            symbol = symbol[:-2]
            print(f"Sembol '.P' ekinden temizlendi: {symbol}")
            send_telegram_message_to_queue(f"ℹ️ Sembol '.P' eki temizlendi: <b>{symbol}</b>")

        symbol = symbol.upper()  # Tüm sembolleri büyük harfe çevir
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
            return {"status": "error", "message": "Geçersiz fiyat formatı"}, 400

        # === POZİSYON BÜYÜKLÜĞÜ AYARI ===
        sabitMiktar_usd = 400.0  # Pozisyon değeri sabit olarak 400$ olarak ayarlandı

        if entry == 0:
            error_msg = "❗ Giriş fiyatı sıfır geldi. Pozisyon miktarı hesaplanamıyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Adet miktarını sabit dolar değerine göre hesapla
        calculated_quantity_float = sabitMiktar_usd / entry

        # === KRİTİK KONTROL: SL VE ENTRY AYNI MI? ===
        if str(entry) == str(sl):
            error_msg = f"❗ GİRİŞ FİYATI ({entry}) ve STOP LOSS FİYATI ({sl}) AYNI GELDİ. Risk anlamsız olduğu için emir gönderilmiyor."
            print(error_msg)
            send_telegram_message_to_queue(f"🚨 Bot Hatası: {error_msg}")
            return {"status": "error", "message": error_msg}, 400

        # Stop Loss ve Take Profit hesaplama
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

        actual_risk_if_sl_hit = abs(calculated_quantity_float * (entry - sl_rounded))

        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (BYBIT + BINGX, Hedef Poz. Değeri: ${sabitMiktar_usd:.2f}):</b>\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_exchange.upper()}\n"
            f"<b>Miktar (Adet):</b> {calculated_quantity_float:.6f}\n"
            f"<b>Giriş Fiyatı:</b> {entry}\n"
            f"<b>Stop Loss (SL):</b> {sl_rounded}\n"
            f"<b>Take Profit (TP):</b> {tp_rounded}\n"
            f"<b>Hesaplanan Fiili Risk (SL vurulursa):</b> ${actual_risk_if_sl_hit:.2f}"
        )
        send_telegram_message_to_queue(trade_summary)

        # === BYBIT EMİRİ GÖNDER ===
        bybit_order = None
        try:
            # Bybit için miktar hassasiyeti
            quantity_str_for_bybit = round_quantity_to_exchange_precision(calculated_quantity_float, 0.001)
            
            bybit_order = bybit_session.place_order(
                category="linear",
                symbol=symbol,
                side=side_for_exchange,
                orderType="Market",
                qty=quantity_str_for_bybit,
                timeInForce="GoodTillCancel",
                stopLoss=str(sl_rounded),
                takeProfit=str(tp_rounded)
            )
            
            if bybit_order and bybit_order.get('retCode') == 0:
                order_info = bybit_order.get('result', {})
                success_message = (
                    f"<b>✅ BYBIT Emir Başarılı!</b>\n"
                    f"<b>Emir ID:</b> <code>{order_info.get('orderId', 'N/A')}</code>\n"
                    f"<b>Sembol:</b> {order_info.get('symbol', 'N/A')}\n"
                    f"<b>Yön:</b> {order_info.get('side', 'N/A')}\n"
                    f"<b>Miktar:</b> {order_info.get('qty', 'N/A')}\n"
                    f"<b>Durum:</b> {bybit_order.get('retMsg', 'Başarılı')}"
                )
                send_telegram_message_to_queue(success_message)
            else:
                error_response_msg = bybit_order.get('retMsg', 'Bilinmeyen Bybit hatası.')
                send_telegram_message_to_queue(f"<b>❌ BYBIT Emir Hatası:</b>\n{error_response_msg}")
                
        except Exception as e:
            send_telegram_message_to_queue(f"<b>🚨 BYBIT Emir Hatası:</b>\n{str(e)}")

        # === BINGX EMİRİ GÖNDER ===
        bingx_order = None
        try:
            # BingX için miktar hassasiyeti
            quantity_str_for_bingx = round_quantity_to_exchange_precision(calculated_quantity_float, 0.001)
            
            bingx_order = bingx_session.place_order(
                symbol=symbol,
                side=side_for_exchange,
                quantity=quantity_str_for_bingx,
                stop_loss=str(sl_rounded),
                take_profit=str(tp_rounded)
            )
            
            if bingx_order and bingx_order.get('code') == 0:
                success_message = (
                    f"<b>✅ BINGX Emir Başarılı!</b>\n"
                    f"<b>Sembol:</b> {symbol}\n"
                    f"<b>Yön:</b> {side_for_exchange.upper()}\n"
                    f"<b>Miktar:</b> {quantity_str_for_bingx}\n"
                    f"<b>Durum:</b> Başarılı"
                )
                send_telegram_message_to_queue(success_message)
            else:
                error_response_msg = bingx_order.get('msg', 'Bilinmeyen BingX hatası.')
                send_telegram_message_to_queue(f"<b>❌ BINGX Emir Hatası:</b>\n{error_response_msg}")
                
        except Exception as e:
            send_telegram_message_to_queue(f"<b>🚨 BINGX Emir Hatası:</b>\n{str(e)}")

        # Genel sonuç
        if (bybit_order and bybit_order.get('retCode') == 0) or (bingx_order and bingx_order.get('code') == 0):
            return {"status": "ok", "bybit": bybit_order, "bingx": bingx_order}, 200
        else:
            return {"status": "partial_error", "bybit": bybit_order, "bingx": bingx_order}, 500

    except Exception as e:
        error_message_full = f"🔥 GENEL HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        send_telegram_message_to_queue(f"<b>🚨 KRİTİK BOT HATASI!</b>\n<pre>{error_message_full}</pre>")
        return {"status": "error", "message": str(e)}, 500


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

    # Sembol prefix'ini temizle (varsa)
    if ":" in symbol_from_tv:
        data["symbol"] = symbol_from_tv.split(":")[-1].strip()

    print(f"Sinyal BYBIT ve BINGX borsaları için işleniyor.")
    send_telegram_message_to_queue(
        f"➡️ Sinyal <b>BYBIT ve BINGX</b> borsaları için işleniyor: <b>{data.get('symbol')}</b>")

    # Sinyali her iki borsanın işlem yöneticisine gönder
    response_data, status_code = handle_trade_signal(data)
    return jsonify(response_data), status_code


# === Ana Sayfa (Botun Aktif Olduğunu Kontrol Etmek İçin) ===
@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪 (Bybit + BingX)"


# === Uygulamayı Başlat ===
if __name__ == "__main__":
    # Telegram mesaj sender thread'ini başlat
    telegram_thread = threading.Thread(target=telegram_message_sender, daemon=True)
    telegram_thread.start()
    
    app.run(debug=True, port=os.getenv("PORT", 5000))