#!/bin/bash
# Wrapper for launchd / cron / manual scheduled runs.
# - cd's to the project directory (so relative paths to stocks.db, tickers.json work)
# - loads ANTHROPIC_API_KEY from .env (since launchd has no shell environment)
# - invokes tracker.py via the venv's Python (so installed packages are available)
#
# Usage:
#   ./run_tracker.sh           — full run (yfinance + AI TLDR)
#   ./run_tracker.sh --no-ai   — yfinance refresh only, no Claude API call

set -e

cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

exec ./.venv/bin/python tracker.py "$@"
