# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal stock portfolio tracker. CLI tool that pulls live data for a configurable list of tickers; grows phase by phase into a full company-info dashboard with AI summaries and (eventually) a web UI.

## Phases

- [x] **Phase 1** — MVP: current price, day change %, 52-week high/low, market cap. CLI output. *(`tracker.py`)*
- [x] **Phase 2** — Top 5 recent news headlines per ticker, with source and relative time.
- [x] **Phase 3** — Historical price data (1y per ticker) stored in `stocks.db` (SQLite); 30-day trend displayed per ticker, computed from the DB.
- [x] **Phase 4** — Company financials (P/E, EPS, revenue, earnings dates, analyst ratings).
- [x] **Phase 5a** — AI-generated TLDR per ticker via the Claude API (Haiku 4.5, titles + financials only).
- [x] **Phase 5b** — TLDR history in SQLite for cross-run continuity (avoid hot/cold whipsaw on small noise).
- [x] **Phase 5c** — Real article reading via Claude's `web_fetch` tool.
- [x] **Phase 6a** — Flask web dashboard with home (ticker cards + latest TLDR) and per-ticker detail page (TLDR history, chronicle, recent prices). Pico.css for styling, DB-only (no live yfinance from web layer).
- [ ] **Phase 6b** — Dashboard polish: ~~price chart per ticker~~ ✓, ~~current-snapshot summary~~ ✓, "refresh" button that shells out to `tracker.py --no-ai` (still pending).
- [x] **Phase 7a** — AI Outlook (PROMISING/MIXED/RISKY) per ticker. Structured-output field in `generate_tldr`, conservative-yellow bias, colored pills on dashboard with editorial labels.

## Tech decisions

- **Language**: Python — continuity with prior CS-fundamentals work.
- **Data source**: `yfinance` library — no API key required, covers prices/news/financials in one package, good for Phases 1–4.
- **Config**: `tickers.json` — flat list of ticker symbols, edited by hand.
- **Storage**: JSON files (Phases 1–2), then SQLite via stdlib `sqlite3` starting in Phase 3. SQL introduced when historical queries make it worthwhile, not before.
- **AI summaries (Phase 5)**: Claude API via the Anthropic Python SDK.
- **Privacy**: ticker watchlist may be committed publicly; any position-size or dollar-amount data added in later phases must stay out of git.
- **Data quality bar**: data should be credible enough to *inform* trading decisions but not be the sole driver of them. Yahoo Finance meets this; don't trade architecture simplicity for marginal accuracy gains.

## Running

**First-time setup** (or when cloning fresh):

```bash
cd ~/Documents/dev/stock-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Every working session:**

```bash
cd ~/Documents/dev/stock-tracker
source .venv/bin/activate
# ... run scripts, edit code ...
deactivate   # optional, when done
```

**Phase 1 script:**

```bash
python tracker.py
```

Reads tickers from `tickers.json`, fetches live data via yfinance, prints one line per ticker with price, day change %, 52-week range, and market cap.
