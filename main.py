import json
import traceback
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP
import os
import decimal

app = Flask(__name__)

# === Ortam Değişkenlerinden Ayarları Yükle ===
# Bu değişkenleri Render.com üzerinde Environment Variables olarak tanımlamalısın.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

# Testnet modunu ortam değişkeninden al. Canlı (gerçek) hesap kullanıyorsan 'False' olmalı.
# Render'da 'BYBIT_TESTNET_MODE' diye bir değişken tanımlamazsan varsayılan olarak False olur.
# Gerçek hesap için bu değişkeni Render'da ya "False" olarak tanımla ya da hiç tanımlama.
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
    # TradingView'den gelen JSON verisini doğrudan alıyoruz
    data = request.get_json()
    print(f"📩 Webhook verisi alındı: {data}")

    try:
        # Gelen verinin ham halini Telegram'a gönder
        signal_message_for_telegram = f"<b>🔔 TradingView Ham Sinyali:</b>\n<pre>{json.dumps(data, indent=2)}</pre>"
        send_telegram_message(signal_message_for_telegram)
        
        # Gerekli sinyal verilerini al
        # NOT: Artık TradingView'den direkt olarak beklediğimiz JSON formatı gelmeli.
        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl") # Stop Loss
        tp = data.get("tp") # Take Profit

        # Bybit'in side parametresi için düzeltme: TradingView 'buy'/'sell' veya 'long'/'short' gönderirken Bybit 'Buy'/'Sell' bekler
        side_for_bybit = ""
        if side and side.lower() == "buy":
            side_for_bybit = "Buy"
        elif side and side.lower() == "sell":
            side_for_bybit = "Sell"
        elif side and side.lower() == "long": # Pine Script'teki 'long' için
            side_for_bybit = "Buy"
        elif side and side.lower() == "short": # Pine Script'teki 'short' için
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
        # Her işlemde risk edilecek dolar miktarı (birincil hedef).
        risk_dolar = 5.0 
        
        # İstediğimiz pozisyon büyüklüğü (dolar cinsinden). Bybit kaldıraç 10x ise, 200$ hedef pozisyon büyüklüğü.
        # Bu, kaldıraç uygulanmış pozisyonun toplam değeridir.
        target_position_value_usd = 200.0 

        # Risk per unit sıfıra çok yakınsa, sıfır kabul et ve hata ver
        if abs(entry - sl) < 0.00000001: 
            error_msg = f"❗ Giriş fiyatı ve SL arasındaki fark ({abs(entry - sl)}) çok küçük. Miktar hesaplanamaz veya çok büyük çıkabilir."
            print(error_msg)
            send_telegram_message(f"🚨 Bot Hatası: {error_msg}")
            return jsonify({"status": "error", "message": error_msg}), 400

        # Pozisyon büyüklüğünü (adet olarak) hedef pozisyon değeri ve giriş fiyatına göre hesapla.
        # Bu miktar, kaldıraç uygulanmış toplam pozisyonun notional değerini temsil eder.
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


        entry = round_to_precision(entry, tick_size)
        sl = round_to_precision(sl, tick_size)
        tp = round_to_precision(tp, tick_size)
        
        # calculated_quantity'nin Bybit limitleri içinde olduğundan emin ol
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
        
        if quantity <= 0: # Son yuvarlamadan sonra hala 0 veya negatif olabilir
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


        trade_summary = (
            f"<b>📢 YENİ EMİR SİPARİŞİ (Hedef Değer: ${target_position_value_usd:.2f}):</b>\n" # Başlık güncellendi
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Yön:</b> {side_for_bybit.upper()}\n" 
            f"<b>Miktar (Adet):</b> {quantity}\n"
            f"<b>Giriş Fiyatı:</b> {entry}\n"
            f"<b>Stop Loss (SL):</b> {sl}\n"
            f"<b>Take Profit (TP):</b> {tp}\n"
            f"<b>Hesaplanan Risk (SL vurulursa):</b> ${abs(quantity * (entry - sl)):.2f}" # Fiili risk
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
