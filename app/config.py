"""Server configuration, driven by environment variables.

Secrets (the PredictHQ token) come ONLY from the environment — never hardcoded
or committed. See .env.example for the variables.
"""
from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Network
PORT = _int("PORT", 8000)

# Events API (PredictHQ). Absent -> the scraper falls back to the cached sample.
PREDICTHQ_TOKEN = os.getenv("PREDICTHQ_TOKEN", "").strip()

# Scheduled-refresh cadence (event forecasts, not live traffic).
REFRESH_MINUTES = _int("REFRESH_MINUTES", 60)

# Retrain-as-history-grows cadence, in hours (refit on events that have passed).
RETRAIN_HOURS = _int("RETRAIN_HOURS", 24)

# City to scrape events for.
CITY = os.getenv("CITY", "Bengaluru").strip() or "Bengaluru"

# Whether to start the in-process refresh scheduler (disable for tests/CLI).
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1").strip() not in ("0", "false", "False")
