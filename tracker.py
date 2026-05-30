import json
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone

import anthropic
import yfinance as yf


def format_market_cap(value):
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,}"


def fmt_or_na(value, formatter):
    return formatter(value) if value is not None else "n/a"


def get_next_earnings(ticker):
    calendar = ticker.calendar
    if calendar and calendar.get("Earnings Date"):
        return calendar["Earnings Date"][0].strftime("%Y-%m-%d")
    return None


TLDR_SYSTEM_PROMPT = (
    "You are a market analyst tracking a single stock for an investor reviewing "
    "their watchlist. You produce a structured JSON response with three fields:\n"
    "\n"
    "1. `daily`: A 2-3 sentence TLDR of the stock's current state, connecting "
    "financial data to news themes. Be direct, avoid hedging, no disclaimers.\n"
    "\n"
    "2. `material_change`: Boolean. True if today's data represents a material "
    "shift from the prior arc — a new risk surfacing, earnings beat/miss, "
    "sustained trend reversal, or major regulatory/product news. False if "
    "today is a continuation of recent priors with normal day-to-day movement.\n"
    "\n"
    "3. `chronicle_entry`: A 1-2 sentence dated note suitable for the long-term "
    "chronicle, describing the stock's current standing relative to its "
    "multi-month arc. Example: \"As of 2026-05: Apple holding near 52w highs "
    "on sustained AI demand; no material risks surfaced this month.\" Always "
    "provide one — the script decides whether to save it.\n"
    "\n"
    "When prior TLDRs and a chronicle are provided, maintain narrative "
    "continuity. Don't flip your stance on small fluctuations — only revise "
    "when the data materially contradicts the prior view. Use only figures "
    "explicitly provided in the input; do not calculate, estimate, or invent "
    "percentages."
)


TLDR_SCHEMA = {
    "type": "object",
    "properties": {
        "daily": {
            "type": "string",
            "description": "2-3 sentence current-state TLDR.",
        },
        "material_change": {
            "type": "boolean",
            "description": "True if today's data represents a material shift from the prior arc.",
        },
        "chronicle_entry": {
            "type": "string",
            "description": "1-2 sentence dated note for the long-term chronicle.",
        },
    },
    "required": ["daily", "material_change", "chronicle_entry"],
    "additionalProperties": False,
}


def generate_tldr(client, conn, symbol, today, financials_text, headlines_text):
    priors = get_recent_tldrs(conn, symbol, today, n=3)
    chronicle = get_chronicle(conn, symbol)

    priors_text = "\n".join(f"- {d}: {t}" for d, t in priors) or "(none yet)"
    chronicle_text = "\n".join(f"- {d}: {e}" for d, e in chronicle) or "(none yet)"

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        system=TLDR_SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": TLDR_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                f"Today's date: {today}\n\n"
                f"Ticker: {symbol}\n\n"
                f"Financials:\n{financials_text}\n\n"
                f"Recent headlines:\n{headlines_text}\n\n"
                f"Prior daily TLDRs (oldest first):\n{priors_text}\n\n"
                f"Chronicle (long-term arc):\n{chronicle_text}"
            ),
        }],
    )

    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)

    save_tldr(conn, symbol, today, result["daily"])

    is_new_month = (not chronicle) or (chronicle[-1][0][:7] != today[:7])
    if is_new_month or result["material_change"]:
        save_chronicle_entry(conn, symbol, today, result["chronicle_entry"])

    return result["daily"]


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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tldrs (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            tldr TEXT NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chronicle (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            entry TEXT NOT NULL,
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


def get_recent_tldrs(conn, symbol, today, n=3):
    rows = conn.execute(
        "SELECT date, tldr FROM tldrs WHERE symbol = ? AND date < ? "
        "ORDER BY date DESC LIMIT ?",
        (symbol, today, n),
    ).fetchall()
    return list(reversed(rows))


def get_chronicle(conn, symbol):
    return conn.execute(
        "SELECT date, entry FROM chronicle WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    ).fetchall()


def save_tldr(conn, symbol, date, tldr):
    conn.execute(
        "INSERT OR REPLACE INTO tldrs (symbol, date, tldr) VALUES (?, ?, ?)",
        (symbol, date, tldr),
    )
    conn.commit()


def save_chronicle_entry(conn, symbol, date, entry):
    conn.execute(
        "INSERT OR REPLACE INTO chronicle (symbol, date, entry) VALUES (?, ?, ?)",
        (symbol, date, entry),
    )
    conn.commit()


conn = init_db()
ai = anthropic.Anthropic()
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    pe = info.get("trailingPE")
    eps = info.get("trailingEps")
    revenue = info.get("totalRevenue")
    target_price = info.get("targetMeanPrice")
    recommendation = info.get("recommendationKey")
    analyst_count = info.get("numberOfAnalystOpinions")
    next_earnings = get_next_earnings(ticker)

    print("  Financials:")
    print(
        f"    P/E: {fmt_or_na(pe, lambda v: f'{v:.2f}')}   "
        f"EPS: {fmt_or_na(eps, lambda v: f'${v:.2f}')}   "
        f"Revenue (TTM): {fmt_or_na(revenue, format_market_cap)}"
    )
    print(f"    Next earnings: {fmt_or_na(next_earnings, str)}")
    print(
        f"    Analysts: {fmt_or_na(recommendation, lambda v: v.replace('_', ' ').title())} "
        f"(target {fmt_or_na(target_price, lambda v: f'${v:.2f}')}, "
        f"{fmt_or_na(analyst_count, str)} analysts)"
    )
    print()

    for item in news[:5]:
        content = item["content"]
        title = content["title"]
        source = content["provider"]["displayName"]
        pub_date = content["pubDate"]
        print(f"  • {title}")
        print(f"    {source} · {format_time_ago(pub_date)}")

    upside_pct = ((target_price - price) / price * 100) if target_price is not None else None
    financials_text = (
        f"- P/E: {fmt_or_na(pe, lambda v: f'{v:.2f}')}\n"
        f"- EPS: {fmt_or_na(eps, lambda v: f'${v:.2f}')}\n"
        f"- Revenue (TTM): {fmt_or_na(revenue, format_market_cap)}\n"
        f"- Next earnings: {fmt_or_na(next_earnings, str)}\n"
        f"- Analyst rating: {fmt_or_na(recommendation, lambda v: v.replace('_', ' ').title())} "
        f"(target {fmt_or_na(target_price, lambda v: f'${v:.2f}')}, "
        f"{fmt_or_na(analyst_count, str)} analysts)\n"
        f"- Upside to analyst target: {fmt_or_na(upside_pct, lambda v: f'{v:+.2f}%')}"
    )
    headlines_text = "\n".join(
        f"{i}. {item['content']['title']} ({item['content']['provider']['displayName']})"
        for i, item in enumerate(news[:5], 1)
    ) or "(no recent headlines)"

    tldr = generate_tldr(ai, conn, symbol, today, financials_text, headlines_text)
    print("\n  TLDR:")
    print(textwrap.fill(tldr, width=78, initial_indent="    ", subsequent_indent="    "))

    print()

print(f"[DB] {total_new_rows} new price row(s) added across {len(config['tickers'])} ticker(s)")
