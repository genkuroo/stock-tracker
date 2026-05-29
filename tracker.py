import json
import sqlite3
import sys
from datetime import datetime, timezone

import yfinance as yf


def format_market_cap(value):
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,}"


def format_time_ago(iso_string):
    pub_time = datetime.fromisoformat(iso_string)
    seconds = max(0, (datetime.now(timezone.utc) - pub_time).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def init_db():
    conn = sqlite3.connect("stocks.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()
    return conn


def get_trend_pct(conn, symbol, days):
    current = conn.execute(
        "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    past = conn.execute(
        "SELECT close FROM prices WHERE symbol = ? AND date <= date('now', ?) "
        "ORDER BY date DESC LIMIT 1",
        (symbol, f"-{days} days"),
    ).fetchone()
    if current is None or past is None:
        return None
    return ((current[0] - past[0]) / past[0]) * 100


def store_history(conn, symbol, hist):
    rows = [
        (
            symbol,
            date.strftime("%Y-%m-%d"),
            row["Open"], row["High"], row["Low"], row["Close"],
            int(row["Volume"]),
        )
        for date, row in hist.iterrows()
    ]
    before = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE symbol = ?", (symbol,)
    ).fetchone()[0]
    conn.executemany(
        """INSERT OR IGNORE INTO prices
           (symbol, date, open, high, low, close, volume)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE symbol = ?", (symbol,)
    ).fetchone()[0]
    return after - before


conn = init_db()

try:
    with open("tickers.json") as f:
        config = json.load(f)
except FileNotFoundError:
    print("Error: tickers.json not found. Create it with a list of ticker symbols. See CLAUDE.md.")
    sys.exit(1)

total_new_rows = 0

for symbol in config["tickers"]:
    ticker = yf.Ticker(symbol)
    info = ticker.info
    news = ticker.news
    hist = ticker.history(period="1y")

    total_new_rows += store_history(conn, symbol, hist)

    price = info["currentPrice"]
    prev_close = info["regularMarketPreviousClose"]
    change_pct = ((price - prev_close) / prev_close) * 100
    high_52w = info["fiftyTwoWeekHigh"]
    low_52w = info["fiftyTwoWeekLow"]
    market_cap = format_market_cap(info["marketCap"])
    trend_30d = get_trend_pct(conn, symbol, 30)
    trend_30d_str = f"{trend_30d:+.2f}%" if trend_30d is not None else "n/a"

    print(
        f"{symbol}: ${price:.2f}  "
        f"Day: {change_pct:+.2f}%  "
        f"30d: {trend_30d_str}  "
        f"52W: ${low_52w:.2f} – ${high_52w:.2f}  "
        f"MktCap: {market_cap}"
    )

    for item in news[:5]:
        content = item["content"]
        title = content["title"]
        source = content["provider"]["displayName"]
        pub_date = content["pubDate"]
        print(f"  • {title}")
        print(f"    {source} · {format_time_ago(pub_date)}")

    print()

print(f"[DB] {total_new_rows} new price row(s) added across {len(config['tickers'])} ticker(s)")
