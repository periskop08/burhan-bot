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
        return value

    # Decimal kütüphanesi ile hassas yuvarlama
    # Adım formatı için 'quantize' fonksiyonuna uygun bir Decimal nesnesi oluştur
    precision_decimal = decimal.Decimal(str(precision_step))
    # Değeri Decimal nesnesine çevir ve yuvarla (ROUND_FLOOR: aşağı yuvarla)
    rounded_value = decimal.Decimal(str(value)).quantize(precision_decimal, rounding=decimal.ROUND_FLOOR)
    return float(rounded_value)


# === Ana Webhook Endpoint'i (TradingView Sinyallerini İşler) ===
@app.route("/webhook", methods=["POST"])
def webhook():
    raw_data = request.get_json()
    print(f"📩 Webhook verisi alındı: {raw_data}")

    try:
        # TradingView'den gelen ham sinyali Telegram'a gönder
        signal_message_for_telegram = f"<b>🔔 TradingView Sinyali Alındı:</b>\n<pre>{json.dumps(raw_data, indent=2)}</pre>"
        send_telegram_message(signal_message_for_telegram)

        data = raw_data
        # Bazı durumlarda TradingView JSON'ı 'text' alanı içinde string olarak gönderebilir.
        if isinstance(data.get("text"), str):
            data = json.loads(data["text"])

         # Gerekli sinyal verilerini al
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") # Stop Loss
        tp = data.get("tp") # Take Profit

         # TradingView'den gelen sembolde Bybit'in beklemediği prefix veya suffix varsa temizle
        if symbol: # symbol'ün boş olup olmadığını kontrol et
            # Örnek: "BINANCE:BTCUSDT" -> "BTCUSDT"
            if ":" in symbol:
                symbol = symbol.split(":")[-1]
                print(f"Sembol TradingView prefix'inden temizlendi: {symbol}")
                send_telegram_message(f"ℹ️ Sembol prefix temizlendi: <b>{symbol}</b>")
            
            # Örnek: "BTCUSDT.P" -> "BTCUSDT" (Bybit için .P ekini kaldır)
            if symbol.endswith(".P"): # Eğer sembol .P ile bitiyorsa
                symbol = symbol[:-2] # Son 2 karakteri (.P) kaldır
                print(f"Sembol '.P' ekinden temizlendi: {symbol}")
                send_telegram_message(f"ℹ️ Sembol '.P' eki temizlendi: <b>{symbol}</b>")
            
            # Ek bir güvenlik adımı: Sembolü büyük harflere çevir (Bybit sembolleri genelde büyük harftir)
            symbol = symbol.upper()
            send_telegram_message(f"ℹ️ Nihai işlem sembolü: <b>{symbol}</b>")

        # Verilerin eksik olup olmadığını kontrol et
        if not all([symbol, side, entry, sl, tp]):
            error_msg = f"❗ Eksik sinyal verisi! Symbol: {symbol}, Side: {side}, Entry: {entry}, SL: {sl}, TP: {tp}"
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "Eksik sinyal verisi"}), 400

        # Sayısal değerleri float'a çevir (TradingView'den string olarak gelebilir)
        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (ValueError, TypeError) as ve:
            error_msg = f"❗ Fiyat verileri sayıya çevrilemedi: Entry={entry}, SL={sl}, TP={tp}. Hata: {ve}. Lütfen TradingView sinyal formatını kontrol edin."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": "Geçersiz fiyat formatı"}), 400

        # Risk yönetimi ile pozisyon büyüklüğü hesapla
        risk_dolar = 16.0
        risk_per_unit = abs(entry - sl)

        # Risk per unit sıfırsa (SL = Entry), hata ver veya varsayılan bir miktar kullan
        if risk_per_unit == 0:
            error_msg = "❗ Risk per unit sıfır olamaz (Giriş fiyatı SL'ye eşit). Bu durumda miktar hesaplanamaz."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        calculated_quantity = risk_dolar / risk_per_unit

        # Bybit API ile oturum başlat (Sembol bilgisi için burada başlatmak en doğrusu)
        session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=BYBIT_TESTNET_MODE)

        # Sembol bilgilerini Bybit'ten al (Fiyat ve Miktar hassasiyeti için)
        tick_size = 0.000001 # Varsayılan: çok küçük bir değer, çoğu parite için yeterli
        lot_size = 0.000001  # Varsayılan: çok küçük bir değer
        
        try:
            exchange_info_response = session.get_instruments_info(category="linear", symbol=symbol)
            if exchange_info_response and exchange_info_response['retCode'] == 0 and exchange_info_response['result']['list']:
                price_filter = exchange_info_response['result']['list'][0].get('priceFilter', {})
                lot_filter = exchange_info_response['result']['list'][0].get('lotFilter', {})

                # Fiyat adımı (tickSize)
                if 'tickSize' in price_filter:
                    tick_size = float(price_filter['tickSize'])
                # Miktar adımı (qtyStep)
                if 'qtyStep' in lot_filter: # Unified API'de lotFilter altında qtyStep bulunabilir
                    lot_size = float(lot_filter['qtyStep'])
                elif 'minTradingQty' in lot_filter: # Alternatif olarak minTradingQty'yi kullanabiliriz
                    lot_size = float(lot_filter['minTradingQty'])

                print(f"Bybit {symbol} için API'den alınan Tick Size: {tick_size}, Lot Size: {lot_size}")
                send_telegram_message(f"ℹ️ {symbol} için Bybit hassasiyetleri alındı:\nFiyat Adımı: <code>{tick_size}</code>\nMiktar Adımı: <code>{lot_size}</code>")
            else:
                print(f"Uyarı: {symbol} için Bybit hassasiyet bilgisi bulunamadı. API yanıtı: {exchange_info_response}. Varsayılanlar kullanılıyor.")
                send_telegram_message(f"⚠️ {symbol} için Bybit hassasiyet bilgisi alınamadı. Varsayılanlar kullanılıyor.")

        except Exception as api_e:
            error_msg_api = f"Bybit sembol/hassasiyet bilgisi alınırken hata: {api_e}. Varsayılan hassasiyetler kullanılıyor."
            print(error_msg_api)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg_api}")
            # Hata durumunda varsayılan hassasiyetler zaten yukarıda tanımlı


        # Fiyatları ve miktarı Bybit'in hassasiyetine yuvarla
        entry = round_to_precision(entry, tick_size)
        sl = round_to_precision(sl, tick_size)
        tp = round_to_precision(tp, tick_size)
        quantity = round_to_precision(calculated_quantity, lot_size)
        
        # Miktar sıfır veya negatifse emir gönderme
        if quantity <= 0:
            error_msg = f"❗ Hesaplanan miktar sıfır veya negatif ({quantity}). Emir gönderilmiyor."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400


        # Emir özetini Telegram'a gönder (yuvarlanmış değerlerle)
        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (Yuvarlanmış Değerler):</b>\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side.upper()}\n"
            f"<b>Miktar (Adet):</b> {quantity}\n"
            f"<b>Giriş Fiyatı:</b> {entry}\n"
            f"<b>Stop Loss (SL):</b> {sl}\n"
            f"<b>Take Profit (TP):</b> {tp}\n"
            f"<b>Risk Miktarı:</b> ${risk_dolar}"
        )
        send_telegram_message(trade_summary)

        # Bybit'e emir gönder
        order = session.place_order(
            category="linear", # Vadeli işlemler için 'linear', spot için 'spot'
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell", # Sinyal yönüne göre 'Buy' veya 'Sell'
            orderType="Market", # Piyasa emri
            qty=str(quantity),  # Bybit API'si qty'yi string olarak bekler
            timeInForce="GoodTillCancel", # Emir iptal edilene kadar geçerli
            stopLoss=str(sl),   # SL fiyatını string olarak gönder
            takeProfit=str(tp)  # TP fiyatını string olarak gönder
        )

        print(f"✅ Emir gönderildi: {order}")

        # Emir gönderimi sonucunu Telegram'a bildir
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
            # Bybit'ten gelen detaylı hatayı logda ve Telegram'da göster
            full_error_details = json.dumps(order, indent=2) 
            error_message_telegram = f"<b>🚨 Bybit Emir Hatası:</b>\n{error_response_msg}\nSinyal: {symbol}, {side}, Miktar: {quantity}\n<pre>{full_error_details}</pre>"
            send_telegram_message(error_message_telegram)
            return jsonify({"status": "error", "message": error_response_msg}), 500

    except Exception as e:
        error_message_full = f"🔥 Genel HATA webhook işlenirken: {str(e)}\n{traceback.format_exc()}"
        print(error_message_full)
        send_telegram_message(f"<b>🚨 KRİTİK BOT HATASI!</b>\n<pre>{error_message_full}</pre>")
        return jsonify({"status": "error", "message": str(e)}), 500

# === Ana Sayfa (Botun Aktif Olduğunu Kontrol Etmek İçin) ===
@app.route("/", methods=["GET"])
def home():
    return "Burhan-Bot aktif 💪"

# === Uygulamayı Başlat ===
if __name__ == "__main__":
    # Render'da gunicorn kullanılır, bu kısım sadece yerel test için
    app.run(debug=True, port=os.getenv("PORT", 5000))

