# stock-tracker

A personal stock portfolio CLI and local dashboard. Pulls live data via `yfinance`, stores history in SQLite, generates AI-written daily TLDRs and a long-term "chronicle" per ticker via the Claude API, and exposes everything through a small Flask dashboard.

Built as a learning project — not a deployable product. Runs entirely on your local machine.

---

## Project structure

```
stock-tracker/
├── tracker.py            CLI: pulls yfinance data, writes to DB, optionally calls Claude
├── app.py                Flask dashboard (localhost:5001)
├── run_tracker.sh        Wrapper for launchd / cron — loads .env, invokes the venv's Python
├── tickers.json          Watchlist config: a list of ticker symbols
├── requirements.txt      Python dependencies
├── CLAUDE.md             Project guidance for Claude Code (phases, decisions)
├── README.md             This file
├── .gitignore
└── templates/
    ├── index.html        Home: ticker cards with snapshot stats + latest TLDR + outlook pill
    └── ticker.html       Per-ticker detail: TLDR history, chronicle, price chart, prices table
```

**Not in the repo (gitignored, you create locally):**

- `.venv/` — Python virtual environment
- `.env` — your Anthropic API key
- `stocks.db` — the SQLite database (built up as you run `tracker.py`)
- `logs/` — launchd job output

---

## Getting it running on your own machine

1. **Clone the repo** and `cd` into it.

2. **Create the venv and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Get an Anthropic API key** at https://console.anthropic.com. Put $5 of credit on the account (lasts a long time at this project's usage).

4. **Create `.env`** with the key (literal `ANTHROPIC_API_KEY=` prefix, no quotes, no spaces around `=`):
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ```

5. **Edit `tickers.json`** to put your own watchlist symbols (default has a few examples).

6. **First full run** — creates `stocks.db`, pulls a year of price history, generates first TLDR per ticker:
   ```bash
   python tracker.py
   ```
   This costs roughly $0.08 per run (Claude Haiku 4.5 + article fetching for ~3 tickers).

7. **Cheap refresh** (no AI, free) — just pulls latest yfinance data:
   ```bash
   python tracker.py --no-ai
   ```

8. **Boot the dashboard:**
   ```bash
   python app.py
   ```
   Then open http://127.0.0.1:5001.

**Optional: schedule it.** Two `launchd` jobs (daily free refresh + weekly AI run) are described in CLAUDE.md → Phase 8. The plist files are machine-specific (absolute paths) and intentionally not committed.

---

## How it works

**Data flow per ticker:**

```
yfinance API   →   tracker.py   →   SQLite (stocks.db)   →   app.py (Flask)   →   browser
                       ↓
                Claude Haiku 4.5
                (structured JSON:
                 daily, outlook,
                 chronicle_entry)
```

**Three tables in `stocks.db`:**

- `prices` — one row per trading day per ticker (OHLCV from yfinance)
- `tldrs` — one row per ticker per day, with the AI-written TLDR, outlook (green/yellow/red), and rationale
- `chronicle` — append-only multi-month narrative arc, one entry per ticker per material change or per month (whichever comes first)

**Per AI run** (`generate_tldr` in `tracker.py`), Claude receives: that day's financials block, headline titles + URLs, the 3 most recent prior TLDRs (for continuity), and the full chronicle. It returns a structured JSON with five fields:

1. `daily` — 2-3 sentence TLDR
2. `material_change` — boolean (does this warrant a chronicle entry?)
3. `chronicle_entry` — 1-2 sentence dated note
4. `outlook` — `green` / `yellow` / `red`
5. `outlook_rationale` — one sentence

The dashboard reads from `stocks.db` only — it never calls yfinance or Claude itself. A "refresh" button on the home page shells out to `tracker.py --no-ai` in the background to update prices without spending API tokens.

---

## Things I learned building this

### Claude API access

The Claude API integration evolved across three phases worth highlighting:

- **Structured outputs over prose parsing.** Using `output_config={"format": {"type": "json_schema", ...}}` gives Claude an enforced JSON schema. Five fields come back in one round-trip, validated, with no regex parsing of free-form text. Massively more reliable than asking for "respond in this format" in the prompt.

- **Server-side tools (`web_fetch_20260209`).** Anthropic-hosted tools that run on their infrastructure — no client-side fetch/parse code needed. Claude decides which article URLs are worth reading and fetches them transparently. One caveat: Haiku 4.5 doesn't support programmatic tool calling (the model writing code to filter results), so the tool definition requires `allowed_callers=["direct"]`. The API returns a clear error message telling you exactly this if you forget.

- **Prompts that constrain hallucination.** Early TLDRs invented numbers ("4% dividend raise", "$312.51" prices not in our data). Two layers fixed it: (1) move the "use only numbers from the Financials section" rule to the **top** of the system prompt — buried rules get ignored, (2) make sure the model has a *sanctioned source* for everything it might want to mention (we expanded the Financials block to include price, day change, 30d trend, etc.). After both layers, leakage dropped from "multiple per run" to "one stubborn one per run."

- **`--no-ai` mode.** Token cost adds up if you re-run on a schedule. Splitting `tracker.py` into a "full" mode and a `--no-ai` mode lets daily automation run for free; the API call only fires when you actually want refreshed AI analysis.

### The macOS Documents folder will fight you

The single biggest unexpected hurdle of the project. Starting in macOS Mojave, `~/Documents/` is part of the TCC (Transparency, Consent, Control) protected folders. Processes launched by the user from Terminal can access it (Terminal has been granted permission). Processes launched by `launchd` cannot — they hit:

```
shell-init: error retrieving current directory: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: ~/Documents/.../run_tracker.sh: Operation not permitted
```

There are two fixes:

1. **Grant `/bin/bash` Full Disk Access** in System Settings → Privacy & Security. Quick but broad — any shell script your account runs gets access to Documents/Desktop/Downloads.
2. **Move the project out of `~/Documents/`** to somewhere like `~/Code/`. This project lives at `~/Code/stock-tracker/` for exactly this reason. **The macOS convention is to never put dev projects in `~/Documents/`** — `~/Code/`, `~/Projects/`, or `~/Developer/` (Apple's recommended one) all sidestep TCC entirely.

Bonus gotcha while we're listing macOS oddities: **port 5000 returns HTTP 403 on macOS 12+** because Apple's AirPlay Receiver squats there. Flask defaults to port 5000 — we use port 5001 instead.

---

## What it's not

- **Not deployed.** Runs only on the machine you cloned it to. No production deploy story, no Docker, no cloud.
- **Not real-time.** The dashboard renders whatever's in `stocks.db` at page load. Fresh data requires either running `tracker.py` manually or clicking the refresh button.
- **Not investment advice.** The AI Outlook signal (PROMISING / MIXED / RISKY) is a directional read on the model's interpretation of fundamentals + news + trend — not a buy/sell recommendation. Same goes for the TLDRs.
