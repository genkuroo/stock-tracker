import json
import sqlite3
import subprocess
import sys

from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = "stock-tracker-local-only"  # local dev only; not a secret


OUTLOOK_LABELS = {"green": "PROMISING", "yellow": "MIXED", "red": "RISKY"}


@app.template_filter("outlook_label")
def outlook_label(value):
    return OUTLOOK_LABELS.get(value, (value or "").upper())


def load_tickers():
    with open("tickers.json") as f:
        return json.load(f)["tickers"]


def save_tickers(tickers):
    with open("tickers.json", "w") as f:
        json.dump({"tickers": tickers}, f, indent=2)


def get_latest_tldr(conn, symbol):
    return conn.execute(
        "SELECT date, tldr, outlook, outlook_rationale FROM tldrs "
        "WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()


def get_snapshot(conn, symbol):
    """Derive close, day change %, and 30-day trend % from the prices table."""
    rows = conn.execute(
        "SELECT date, close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 30",
        (symbol,),
    ).fetchall()
    if not rows:
        return None
    latest_date, latest_close = rows[0]
    day_change_pct = None
    if len(rows) > 1:
        prev_close = rows[1][1]
        day_change_pct = (latest_close - prev_close) / prev_close * 100
    trend_30d_pct = None
    if len(rows) >= 30:
        old_close = rows[-1][1]
        trend_30d_pct = (latest_close - old_close) / old_close * 100
    return {
        "date": latest_date,
        "close": latest_close,
        "day_change_pct": day_change_pct,
        "trend_30d_pct": trend_30d_pct,
    }


@app.route("/")
def index():
    conn = sqlite3.connect("stocks.db")
    tickers = []
    for symbol in load_tickers():
        latest = get_latest_tldr(conn, symbol)
        snapshot = get_snapshot(conn, symbol)
        tickers.append({
            "symbol": symbol,
            "date": latest[0] if latest else None,
            "tldr": latest[1] if latest else None,
            "outlook": latest[2] if latest else None,
            "outlook_rationale": latest[3] if latest else None,
            "snapshot": snapshot,
        })
    conn.close()
    return render_template("index.html", tickers=tickers)


@app.route("/ticker/<symbol>")
def ticker(symbol):
    conn = sqlite3.connect("stocks.db")
    tldrs = conn.execute(
        "SELECT date, tldr, outlook, outlook_rationale FROM tldrs "
        "WHERE symbol = ? ORDER BY date DESC",
        (symbol,),
    ).fetchall()
    chronicle = conn.execute(
        "SELECT date, entry FROM chronicle WHERE symbol = ? ORDER BY date DESC",
        (symbol,),
    ).fetchall()
    prices = conn.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE symbol = ? ORDER BY date DESC LIMIT 30",
        (symbol,),
    ).fetchall()
    chart_rows = conn.execute(
        "SELECT date, close FROM prices WHERE symbol = ? "
        "ORDER BY date DESC LIMIT 90",
        (symbol,),
    ).fetchall()
    conn.close()
    chart_rows.reverse()
    chart_dates = [row[0] for row in chart_rows]
    chart_closes = [row[1] for row in chart_rows]
    return render_template(
        "ticker.html",
        symbol=symbol,
        tldrs=tldrs,
        chronicle=chronicle,
        prices=prices,
        chart_dates=chart_dates,
        chart_closes=chart_closes,
    )


@app.route("/refresh", methods=["POST"])
def refresh():
    subprocess.Popen([sys.executable, "tracker.py", "--no-ai"])
    flash("Refresh started — reload the page in ~10 seconds to see updated prices and news.")
    return redirect(url_for("index"))


@app.route("/watchlist/add", methods=["POST"])
def watchlist_add():
    symbol = request.form.get("symbol", "").strip().upper()
    if symbol:
        tickers = load_tickers()
        if symbol in tickers:
            flash(f"{symbol} is already in your watchlist.")
        else:
            tickers.append(symbol)
            save_tickers(tickers)
            flash(f"Added {symbol}. Run a refresh or full update to populate its data.")
    return redirect(url_for("index"))


@app.route("/watchlist/remove/<symbol>", methods=["POST"])
def watchlist_remove(symbol):
    tickers = load_tickers()
    if symbol in tickers:
        tickers.remove(symbol)
        save_tickers(tickers)
        flash(f"Removed {symbol} from watchlist. Historical data is preserved in the DB if you re-add it later.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
