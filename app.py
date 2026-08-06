import functools
import json
import os
import sqlite3
import subprocess
import sys

from flask import Flask, abort, flash, redirect, render_template, request, url_for

# Paths are configurable so the same image can run twice: once against the real
# watchlist on a private port, and once against a synthetic demo database that
# is safe to expose publicly. Defaults keep `python app.py` working unchanged
# for local development.
DB_PATH = os.environ.get("STOCK_DB", "stocks.db")
TICKERS_PATH = os.environ.get("STOCK_TICKERS", "tickers.json")

# The public instance runs with READ_ONLY=1. Hiding the buttons in the template
# is not enough on its own — anyone can POST to the route directly — so the
# routes themselves refuse to run.
READ_ONLY = os.environ.get("READ_ONLY") == "1"

app = Flask(__name__)
# Only signs flash-message cookies here. Overridable so the deployed instance
# isn't using a value that's published in the public repo.
app.secret_key = os.environ.get("SECRET_KEY", "stock-tracker-local-only")


def read_only_guard(view):
    """Refuse a state-changing route when running as a public demo."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if READ_ONLY:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


@app.context_processor
def inject_flags():
    # Lets templates hide controls that would only 403 if clicked.
    return {"read_only": READ_ONLY}


OUTLOOK_LABELS = {"green": "PROMISING", "yellow": "MIXED", "red": "RISKY"}


@app.template_filter("outlook_label")
def outlook_label(value):
    return OUTLOOK_LABELS.get(value, (value or "").upper())


def load_tickers():
    with open(TICKERS_PATH) as f:
        return json.load(f)["tickers"]


def save_tickers(tickers):
    with open(TICKERS_PATH, "w") as f:
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
        "SELECT date, close FROM prices WHERE symbol = ? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 30",
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
        "WHERE symbol = ? AND close IS NOT NULL ORDER BY date DESC LIMIT 30",
        (symbol,),
    ).fetchall()
    chart_rows = conn.execute(
        "SELECT date, close FROM prices WHERE symbol = ? AND close IS NOT NULL "
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
@read_only_guard
def refresh():
    subprocess.Popen([sys.executable, "tracker.py", "--no-ai"])
    flash("Refresh started — reload the page in ~10 seconds to see updated prices and news.")
    return redirect(url_for("index"))


@app.route("/watchlist/add", methods=["POST"])
@read_only_guard
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
@read_only_guard
def watchlist_remove(symbol):
    tickers = load_tickers()
    if symbol in tickers:
        tickers.remove(symbol)
        save_tickers(tickers)
        flash(f"Removed {symbol} from watchlist. Historical data is preserved in the DB if you re-add it later.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Local development only. In production the app is served by gunicorn
    # (see Dockerfile) — debug=True exposes the Werkzeug console, which is
    # remote code execution on anything reachable over a network.
    app.run(debug=True, port=5001)
