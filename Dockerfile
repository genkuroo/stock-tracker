# stock-tracker — production image for the Raspberry Pi (ARM64).
#
# The same image runs twice on the Pi: once privately against the real
# watchlist, and once publicly with DEMO=1 and READ_ONLY=1 against a synthetic
# database. Nothing about the real data is baked in — see .dockerignore.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# curl_cffi (a yfinance dependency) needs libcurl present at runtime; the
# aarch64 wheel links against the system library rather than vendoring it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so this layer survives code-only rebuilds. On ARM64 the
# numpy/pandas wheels exist for Debian, so this doesn't trigger a source build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]

# 2 workers is plenty for this traffic and keeps SQLite write contention low.
# The dashboard is read-mostly; threads cover the I/O wait on DB reads.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", \
     "--timeout", "60", "app:app"]
