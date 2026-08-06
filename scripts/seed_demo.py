"""Populate a stock-tracker database with synthetic-but-plausible demo data.

Why this exists:
  The dashboard is worth showing to other people, but the real one displays an
  actual watchlist. This generates a self-contained database so a public
  instance can render a full dashboard — price charts, TLDR history, chronicle
  entries, outlook pills — without exposing anything real. The app renders a
  "Demo mode" banner whenever READ_ONLY=1, so nobody mistakes these numbers for
  live quotes.

  It's deterministic (fixed seed), so screenshots are reproducible and a rebuilt
  container shows the same dashboard as the one before it.

Design notes:
  * Prices are a random walk with drift, generated on weekdays only — markets
    are closed weekends, and the dashboard's "last 30 rows" trend calculation
    would otherwise silently span six calendar weeks instead of six trading ones.
  * Each ticker gets a drift and volatility that *matches* its outlook. If the
    generated prices trended down while the TLDR said PROMISING, the demo would
    read as broken rather than as a product.
  * The schema is duplicated here rather than imported from tracker.py, because
    importing that module runs argparse and constructs an Anthropic client at
    import time. Keep this DDL in sync with tracker.py's init_db().

Run from the repo root:  python scripts/seed_demo.py
"""

import json
import os
import random
import sqlite3
from datetime import date, timedelta

DB_PATH = os.environ.get("STOCK_DB", "stocks.db")
TICKERS_PATH = os.environ.get("STOCK_TICKERS", "tickers.json")

random.seed(1729)

TRADING_DAYS = 120  # ~6 months, comfortably more than the 90-point chart window

# start price, annualized drift, daily volatility, outlook
#
# Drift has to be large relative to volatility or the random walk buries it:
# at a realistic 3% daily vol, a -12%/yr drift is ~65x smaller than the daily
# noise, and the "declining" stock finishes up. These are tuned so the 30-day
# trend on each dashboard card actually agrees with that ticker's outlook pill
# (verified against the fixed seed below — re-check if you change either).
DEMO_TICKERS = {
    "AAPL": (228.0, 0.42, 0.007, "green"),
    "MSFT": (441.0, 0.28, 0.008, "green"),
    "NVDA": (132.0, 0.45, 0.022, "yellow"),
    "AMZN": (197.0, 0.16, 0.012, "yellow"),
    "TSLA": (346.0, -1.05, 0.017, "red"),
}

TLDRS = {
    "AAPL": [
        ("Services revenue continues to carry the quarter while hardware demand "
         "stays flat. Margin expansion is the story analysts keep returning to.",
         "green",
         "Steady margin growth and a durable services mix outweigh soft iPhone units."),
        ("Supply chain commentary turned more optimistic; component costs easing "
         "into the back half of the year.",
         "green",
         "Cost relief plus stable demand supports the current trajectory."),
    ],
    "MSFT": [
        ("Cloud segment growth held above expectations. Capital expenditure on AI "
         "infrastructure remains the main drag on free cash flow.",
         "green",
         "Azure momentum is strong enough to absorb elevated capex."),
        ("Enterprise seat expansion offset slower consumer licensing.",
         "green",
         "Recurring enterprise revenue continues to compound."),
    ],
    "NVDA": [
        ("Data center demand remains extraordinary, but the stock is priced for "
         "near-flawless execution. Any supply hiccup moves it hard.",
         "yellow",
         "Fundamentals are excellent; valuation leaves no room for error."),
        ("Export restriction headlines introduced fresh uncertainty around the "
         "China revenue line.",
         "yellow",
         "Strong core business, genuine regulatory overhang."),
    ],
    "AMZN": [
        ("Retail margins improved on fulfillment efficiency; AWS growth "
         "reaccelerated modestly off a soft comparison.",
         "yellow",
         "Improving, but the reacceleration is partly a base effect."),
        ("Advertising continues to outgrow every other segment.",
         "yellow",
         "One strong segment is doing a lot of the work."),
    ],
    "TSLA": [
        ("Deliveries missed consensus again and average selling prices fell "
         "further. Price cuts are defending volume at the cost of margin.",
         "red",
         "Margin compression with no clear floor on pricing."),
        ("Competitive pressure in Europe and China intensified through the quarter.",
         "red",
         "Share loss in two major markets alongside falling ASPs."),
    ],
}

CHRONICLE = {
    "AAPL": ["Added to watchlist after the services margin story held up for a third straight quarter."],
    "MSFT": ["Watching whether AI capex starts to compress free cash flow."],
    "NVDA": ["Position sized deliberately small — conviction is high, but so is the volatility."],
    "AMZN": ["Interested mainly in the advertising segment, not the retail business."],
    "TSLA": ["Kept on the list as a bear-case study rather than a buy candidate."],
}


def trading_days(n):
    """The last n weekdays, oldest first."""
    days, cursor = [], date.today()
    while len(days) < n:
        if cursor.weekday() < 5:  # Mon-Fri
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def init_db(conn):
    """Mirror of tracker.py's init_db() — keep in sync."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tldrs (
            symbol TEXT NOT NULL, date TEXT NOT NULL, tldr TEXT NOT NULL,
            outlook TEXT, outlook_rationale TEXT,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chronicle (
            symbol TEXT NOT NULL, date TEXT NOT NULL, entry TEXT NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()


def generate_prices(conn, symbol, start, drift, vol, days):
    """Random walk with drift. Daily drift is the annual figure spread over
    ~252 trading days, so a 0.18 'annualized' input actually looks like 18%."""
    daily_drift = drift / 252
    price = start
    rows = []
    for day in days:
        change = random.gauss(daily_drift, vol)
        open_ = price
        close = max(price * (1 + change), 1.0)
        # Intraday range straddles the open/close, as a real candle would.
        high = max(open_, close) * (1 + abs(random.gauss(0, vol / 3)))
        low = min(open_, close) * (1 - abs(random.gauss(0, vol / 3)))
        volume = int(random.gauss(45_000_000, 12_000_000))
        rows.append((
            symbol, day.isoformat(),
            round(open_, 2), round(high, 2), round(low, 2), round(close, 2),
            max(volume, 1_000_000),
        ))
        price = close
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    days = trading_days(TRADING_DAYS)
    total = 0
    for symbol, (start, drift, vol, _outlook) in DEMO_TICKERS.items():
        total += generate_prices(conn, symbol, start, drift, vol, days)

        # TLDRs land on recent weekly-ish dates so the detail page shows history.
        for i, (text, outlook, rationale) in enumerate(TLDRS[symbol]):
            when = days[-1 - (i * 7)]
            conn.execute(
                "INSERT OR REPLACE INTO tldrs "
                "(symbol, date, tldr, outlook, outlook_rationale) VALUES (?, ?, ?, ?, ?)",
                (symbol, when.isoformat(), text, outlook, rationale),
            )

        for i, entry in enumerate(CHRONICLE[symbol]):
            conn.execute(
                "INSERT OR REPLACE INTO chronicle (symbol, date, entry) VALUES (?, ?, ?)",
                (symbol, days[-1 - (i * 14)].isoformat(), entry),
            )

    conn.commit()
    conn.close()

    # The dashboard reads its watchlist from this file, not from the database.
    with open(TICKERS_PATH, "w") as f:
        json.dump({"tickers": list(DEMO_TICKERS)}, f, indent=2)

    print(f"Seeded {DB_PATH}: {len(DEMO_TICKERS)} tickers, {total} price rows")
    print(f"Wrote watchlist to {TICKERS_PATH}")


if __name__ == "__main__":
    main()
