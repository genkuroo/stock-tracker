# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal stock portfolio tracker. CLI tool that pulls live data for a configurable list of tickers; grows phase by phase into a full company-info dashboard with AI summaries and (eventually) a web UI.

## Phases

- [ ] **Phase 1** — MVP: current price, day change %, 52-week high/low, market cap. CLI output.
- [ ] **Phase 2** — Recent news headlines per ticker.
- [ ] **Phase 3** — Historical price data. Introduces SQLite for storage.
- [ ] **Phase 4** — Company financials (P/E, EPS, revenue, earnings dates, analyst ratings).
- [ ] **Phase 5** — AI-generated TLDR per ticker via the Claude API.
- [ ] **Phase 6** — Web dashboard (Flask or FastAPI, decision deferred).

## Tech decisions

- **Language**: Python — continuity with prior CS-fundamentals work.
- **Data source**: `yfinance` library — no API key required, covers prices/news/financials in one package, good for Phases 1–4.
- **Config**: `tickers.json` — flat list of ticker symbols, edited by hand.
- **Storage**: JSON files (Phases 1–2), then SQLite via stdlib `sqlite3` starting in Phase 3. SQL introduced when historical queries make it worthwhile, not before.
- **AI summaries (Phase 5)**: Claude API via the Anthropic Python SDK.
- **Privacy**: ticker watchlist may be committed publicly; any position-size or dollar-amount data added in later phases must stay out of git.

## Running

(To be filled in once Phase 1 code lands.)
