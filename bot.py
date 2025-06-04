from pybit.unified_trading import HTTP

import config

session = HTTP(
    testnet=True,
    api_key=config.API_KEY,
    api_secret=config.API_SECRET
)

def place_order(symbol, side, entry, sl, tp, qty):
    try:
        order = session.place_order(
            category="linear",
            symbol=symbol,
            side=side.capitalize(),  # 'Buy' veya 'Sell'
            order_type="Market",
            qty=qty,
            take_profit=tp,
            stop_loss=sl,
            time_in_force="GoodTillCancel"
        )
        print("Emir gönderildi:", order)
        return order
    except Exception as e:
        print("Emir gönderilirken hata oluştu:", e)
        return None