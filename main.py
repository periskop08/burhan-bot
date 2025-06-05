@app.route("/webhook", methods=["POST"])
def webhook():
    raw_data = request.get_json()
    print("📩 Webhook verisi alındı:", raw_data)

    try:
        data = raw_data
        if isinstance(raw_data.get("text"), str):
            # TradingView'den gelen gömülü json string'i varsa onu ayıkla
            data = json.loads(raw_data["text"])

        symbol = data.get("symbol")
        side = data.get("side")
        entry = data.get("entry")
        sl = data.get("sl")
        tp = data.get("tp")

        if not all([symbol, side, entry, sl, tp]):
            print("❗ Eksik veri:", symbol, side, entry, sl, tp)
            return jsonify({"status": "error", "message": "Eksik veri: entry, sl veya tp eksik."}), 400

        entry = float(entry)
        sl = float(sl)
        tp = float(tp)

        risk_dolar = 16.0
        risk_per_unit = abs(entry - sl)
        if risk_per_unit == 0:
            return jsonify({"status": "error", "message": "Entry ve SL aynı, pozisyon büyüklüğü hesaplanamaz."}), 400

        quantity = round(risk_dolar / risk_per_unit, 3)

        print(f"📢 EMİR: {side.upper()} | Symbol: {symbol} | Entry: {entry} | SL: {sl} | TP: {tp} | Miktar: {quantity}")

        session = HTTP(api_key=api_key, api_secret=api_secret, testnet=False)

        order = session.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side.lower() == "long" else "Sell",
            order_type="Market",
            qty=quantity,
            time_in_force="GoodTillCancel",
            position_idx=1
        )
        print("✅ Emir gönderildi:", order)
        return jsonify({"status": "ok", "order": order})

    except Exception as e:
        print("🔥 Emir gönderilirken hata:", e)
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500