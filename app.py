import json
import sqlite3

from flask import Flask, render_template

app = Flask(__name__)


def load_tickers():
    with open("tickers.json") as f:
        return json.load(f)["tickers"]


def get_latest_tldr(conn, symbol):
    return conn.execute(
        "SELECT date, tldr FROM tldrs WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,),
    ).fetchone()


@app.route("/")
def index():
    conn = sqlite3.connect("stocks.db")
    tickers = []
    for symbol in load_tickers():
        latest = get_latest_tldr(conn, symbol)
        tickers.append({
            "symbol": symbol,
            "date": latest[0] if latest else None,
            "tldr": latest[1] if latest else None,
        })
    conn.close()
    return render_template("index.html", tickers=tickers)


@app.route("/ticker/<symbol>")
def ticker(symbol):
    conn = sqlite3.connect("stocks.db")
    tldrs = conn.execute(
        "SELECT date, tldr FROM tldrs WHERE symbol = ? ORDER BY date DESC",
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
    conn.close()
    return render_template(
        "ticker.html",
        symbol=symbol,
        tldrs=tldrs,
        chronicle=chronicle,
        prices=prices,
    )


if __name__ == "__main__":
    app.run(debug=True)
