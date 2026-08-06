import argparse
import json
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone

import anthropic
import yfinance as yf

# Kept in sync with app.py — the scheduled job and the dashboard must agree on
# which database file they're using, or the timer writes prices the web UI
# never displays. Defaults preserve the original local-directory behavior.
DB_PATH = os.environ.get("STOCK_DB", "stocks.db")
TICKERS_PATH = os.environ.get("STOCK_TICKERS", "tickers.json")

parser = argparse.ArgumentParser(description="Pull stock data and (optionally) generate AI TLDRs.")
parser.add_argument(
    "--no-ai",
    action="store_true",
    help="Skip Claude API calls (refresh prices and news only — no TLDR/chronicle/outlook).",
)
args = parser.parse_args()


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
    "their watchlist.\n"
    "\n"
    "**RULE 1: Numbers come from the `Financials` section ONLY.** The "
    "Financials section lists every quantitative fact you may reference: "
    "price, day change, 52-week range, market cap, P/E, EPS, revenue, "
    "earnings dates, analyst targets, upside percentage. Do NOT cite, "
    "paraphrase, or reference any number from any other source — including "
    "headlines, article bodies, prior TLDRs, or the chronicle. If a number "
    "appears in a headline (e.g., \"$37 Billion AI Run Rate\"), you may "
    "reference the topic qualitatively (\"a major AI revenue milestone\") "
    "but not the specific figure. Headline and article numbers may be "
    "stale, wrong, or misattributed. Do NOT calculate, estimate, or invent "
    "percentages of your own.\n"
    "\n"
    "**RULE 2: Articles are for themes only.** When you use `web_fetch` to "
    "read an article, use it to understand qualitative context — what is "
    "the story about, what concerns or opportunities are being discussed, "
    "what is the narrative theme. Do not extract specific figures, growth "
    "rates, dates, revenue numbers, prices, product specifications, or any "
    "other company-specific quantitative claims from article text. Themes "
    "only.\n"
    "\n"
    "**Output**: structured JSON with five fields:\n"
    "\n"
    "1. `daily`: 2-3 sentence TLDR of the stock's current state, connecting "
    "data (from Financials) to themes (from headlines and articles). "
    "Direct, no hedging, no disclaimers.\n"
    "\n"
    "2. `material_change`: Boolean. True if today's data represents a "
    "material shift from the prior arc — new risk, earnings beat/miss, "
    "sustained trend reversal, major regulatory/product news. False if "
    "today is a continuation of recent priors with normal day-to-day movement.\n"
    "\n"
    "3. `chronicle_entry`: 1-2 sentence dated note for the long-term "
    "chronicle, describing the stock's current standing relative to its "
    "multi-month arc. Example: \"As of 2026-05: Apple holding near 52w "
    "highs on sustained AI demand; no material risks surfaced this month.\" "
    "Always provide one — the script decides whether to save it.\n"
    "\n"
    "4. `outlook`: One of \"green\", \"yellow\", or \"red\" — a directional "
    "read on the stock right now:\n"
    "   - green = bullish (fundamentals, narrative, and trend align "
    "positively; limited material risks)\n"
    "   - yellow = mixed/hold (fundamentals and narrative diverge, or "
    "conditions genuinely uncertain)\n"
    "   - red = bearish (material risks dominate, trend breaking down, or "
    "negative catalysts likely)\n"
    "   Be conservative — yellow is the default when uncertain. Do not flip "
    "the outlook from a prior call's value on noisy data; only revise on "
    "material change.\n"
    "\n"
    "5. `outlook_rationale`: One sentence (≤ 25 words) explaining the outlook.\n"
    "\n"
    "**Web fetch**: fetch URLs for headlines likely to contain substantive "
    "analysis or company-specific reporting. Skip aggregator/pump pieces, "
    "obvious paywalls, or headlines that already convey their full point. "
    "Remember Rule 2: themes only.\n"
    "\n"
    "**Continuity**: when prior TLDRs and a chronicle are provided, "
    "maintain narrative continuity. Don't flip your stance on small "
    "fluctuations — only revise when the data materially contradicts the "
    "prior view."
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
        "outlook": {
            "type": "string",
            "enum": ["green", "yellow", "red"],
            "description": "Directional read: green=bullish, yellow=mixed/hold, red=bearish.",
        },
        "outlook_rationale": {
            "type": "string",
            "description": "One sentence explaining the outlook.",
        },
    },
    "required": ["daily", "material_change", "chronicle_entry", "outlook", "outlook_rationale"],
    "additionalProperties": False,
}


def generate_tldr(client, conn, symbol, today, financials_text, headlines_text):
    priors = get_recent_tldrs(conn, symbol, today, n=3)
    chronicle = get_chronicle(conn, symbol)

    priors_text = "\n".join(
        f"- {d} [{(o or 'none').upper()}]: {t}" for d, t, o in priors
    ) or "(none yet)"
    chronicle_text = "\n".join(f"- {d}: {e}" for d, e in chronicle) or "(none yet)"

    user_prompt = (
        f"Today's date: {today}\n\n"
        f"Ticker: {symbol}\n\n"
        f"Financials:\n{financials_text}\n\n"
        f"Recent headlines:\n{headlines_text}\n\n"
        f"Prior daily TLDRs (oldest first):\n{priors_text}\n\n"
        f"Chronicle (long-term arc):\n{chronicle_text}"
    )
    messages = [{"role": "user", "content": user_prompt}]

    while True:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=TLDR_SYSTEM_PROMPT,
            tools=[{
                "type": "web_fetch_20260209",
                "name": "web_fetch",
                "allowed_callers": ["direct"],
            }],
            output_config={"format": {"type": "json_schema", "schema": TLDR_SCHEMA}},
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": response.content},
        ]

    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)

    save_tldr(
        conn, symbol, today,
        result["daily"], result["outlook"], result["outlook_rationale"],
    )

    is_new_month = (not chronicle) or (chronicle[-1][0][:7] != today[:7])
    if is_new_month or result["material_change"]:
        save_chronicle_entry(conn, symbol, today, result["chronicle_entry"])

    return {
        "daily": result["daily"],
        "outlook": result["outlook"],
        "outlook_rationale": result["outlook_rationale"],
    }


def format_time_ago(iso_string):
    pub_time = datetime.fromisoformat(iso_string)
    seconds = max(0, (datetime.now(timezone.utc) - pub_time).total_seconds())
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    for column in ("outlook TEXT", "outlook_rationale TEXT"):
        try:
            conn.execute(f"ALTER TABLE tldrs ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass
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
        # yfinance occasionally returns a row (e.g. an in-progress trading day)
        # with volume but NaN OHLC. Skip those — a partial row stored as NULL
        # breaks the dashboard's price formatting.
        if not row[["Open", "High", "Low", "Close"]].isnull().any()
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
        "SELECT date, tldr, outlook FROM tldrs WHERE symbol = ? AND date < ? "
        "ORDER BY date DESC LIMIT ?",
        (symbol, today, n),
    ).fetchall()
    return list(reversed(rows))


def get_chronicle(conn, symbol):
    return conn.execute(
        "SELECT date, entry FROM chronicle WHERE symbol = ? ORDER BY date ASC",
        (symbol,),
    ).fetchall()


def save_tldr(conn, symbol, date, tldr, outlook, outlook_rationale):
    conn.execute(
        "INSERT OR REPLACE INTO tldrs (symbol, date, tldr, outlook, outlook_rationale) "
        "VALUES (?, ?, ?, ?, ?)",
        (symbol, date, tldr, outlook, outlook_rationale),
    )
    conn.commit()


def save_chronicle_entry(conn, symbol, date, entry):
    conn.execute(
        "INSERT OR REPLACE INTO chronicle (symbol, date, entry) VALUES (?, ?, ?)",
        (symbol, date, entry),
    )
    conn.commit()


conn = init_db()
ai = anthropic.Anthropic(max_retries=6) if not args.no_ai else None
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

try:
    with open(TICKERS_PATH) as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"Error: {TICKERS_PATH} not found. Create it with a list of ticker symbols. See CLAUDE.md.")
    sys.exit(1)

def process_ticker(symbol):
    """Fetch and process one ticker. Returns count of new price rows added.
    Raises on any per-ticker error so the caller can log + skip rather than
    aborting the whole run (yfinance is a flaky external boundary)."""
    ticker = yf.Ticker(symbol)
    info = ticker.info
    news = ticker.news
    hist = ticker.history(period="1y")

    new_rows = store_history(conn, symbol, hist)

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
        f"- Current price: ${price:.2f}\n"
        f"- Day change: {change_pct:+.2f}%\n"
        f"- 30-day trend: {trend_30d_str}\n"
        f"- 52-week range: ${low_52w:.2f} – ${high_52w:.2f}\n"
        f"- Market cap: {market_cap}\n"
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
        f"{i}. {item['content']['title']} ({item['content']['provider']['displayName']})\n"
        f"   URL: {item['content']['canonicalUrl']['url']}"
        for i, item in enumerate(news[:5], 1)
    ) or "(no recent headlines)"

    if not args.no_ai:
        result = generate_tldr(ai, conn, symbol, today, financials_text, headlines_text)
        print(f"\n  TLDR ({result['outlook'].upper()}):")
        print(textwrap.fill(result["daily"], width=78, initial_indent="    ", subsequent_indent="    "))
        print(textwrap.fill(
            f"Outlook rationale: {result['outlook_rationale']}",
            width=78, initial_indent="    ", subsequent_indent="    ",
        ))

    return new_rows


total_new_rows = 0
failed_symbols = []

for symbol in config["tickers"]:
    try:
        total_new_rows += process_ticker(symbol)
    except Exception as e:
        failed_symbols.append(symbol)
        print(f"\n[FAILED] {symbol} — {type(e).__name__}: {e}")
    print()

successful_count = len(config["tickers"]) - len(failed_symbols)
print(f"[DB] {total_new_rows} new price row(s) added across {successful_count} successful ticker(s)")
if failed_symbols:
    print(
        f"[WARN] Skipped {len(failed_symbols)} ticker(s): {', '.join(failed_symbols)} "
        "— likely transient yfinance/Yahoo issue; retry in a few minutes."
    )
