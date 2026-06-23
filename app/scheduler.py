"""Scheduled refresh of the event forecast.

Re-scrapes upcoming events and re-forecasts on an interval, swapping the cached
`latest_view`. This refreshes EVENT FORECASTS — not live traffic state. On a
sleeping free-tier host the in-process scheduler stops; the documented fallback
is an external cron hitting POST /api/scrape.
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from app import config
from app.cache import STATE
from app.pipeline import scrape_to_view

_sched: BackgroundScheduler | None = None


def refresh_job() -> None:
    """Scrape -> forecast -> swap latest_view. Never raises; keeps last good view."""
    if not STATE.ready:
        return
    try:
        view = scrape_to_view()
    except Exception as e:
        print(f"[scheduler] refresh skipped (kept previous view): {e}")
        return
    with STATE.lock:
        STATE.latest_view = view
    print(f"[scheduler] refreshed forecast ({view['meta']['source']}, "
          f"{view['meta']['event_count']} events)")


def start() -> None:
    global _sched
    if _sched is not None:
        return
    _sched = BackgroundScheduler(daemon=True)
    _sched.add_job(refresh_job, "interval", minutes=config.REFRESH_MINUTES,
                   max_instances=1, coalesce=True, id="refresh")
    _sched.start()
    print(f"[scheduler] started — refreshing every {config.REFRESH_MINUTES} min")


def stop() -> None:
    global _sched
    if _sched is not None:
        _sched.shutdown(wait=False)
        _sched = None
