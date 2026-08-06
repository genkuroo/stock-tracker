#!/bin/sh
# Prepares the container's data directory, then hands off to the real command.
set -e

# STOCK_DB points into a mounted volume, which starts out empty on first boot.
mkdir -p "$(dirname "${STOCK_DB:-stocks.db}")"

# The public demo instance generates its own synthetic database. Guarded on the
# file not existing so a container restart doesn't wipe the seeded data — and
# so this can never overwrite a real database if DEMO is ever set by mistake.
if [ "$DEMO" = "1" ] && [ ! -f "${STOCK_DB:-stocks.db}" ]; then
	echo "DEMO=1 and no database present — seeding synthetic data"
	python scripts/seed_demo.py
fi

exec "$@"
