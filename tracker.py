import json
import yfinance as yf


def format_market_cap(value):
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,}"


with open("tickers.json") as f:
    config = json.load(f)

for symbol in config["tickers"]:
    info = yf.Ticker(symbol).info

    price = info["currentPrice"]
    prev_close = info["regularMarketPreviousClose"]
    change_pct = ((price - prev_close) / prev_close) * 100
    high_52w = info["fiftyTwoWeekHigh"]
    low_52w = info["fiftyTwoWeekLow"]
    market_cap = format_market_cap(info["marketCap"])

    print(
        f"{symbol}: ${price:.2f}  "
        f"Day: {change_pct:+.2f}%  "
        f"52W: ${low_52w:.2f} – ${high_52w:.2f}  "
        f"MktCap: {market_cap}"
    )
