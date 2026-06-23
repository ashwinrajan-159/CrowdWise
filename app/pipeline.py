"""Bridge between the FastAPI layer and the gridlock pipeline.

Reuses predict.train_on_history / predict_new and scrape_events as libraries.
Nothing here retrains the model on the request path — the cached booster in
cache.STATE is reused.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pandas as pd

from gridlock.config import DEFAULT as CFG
from gridlock.ingest import load_events
from predict import train_on_history, predict_new

from app import config
from app.cache import STATE


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def attach_coords(view: dict, combined: pd.DataFrame) -> dict:
    """Join lat/lon onto each assignment by event_id (the view omits them).

    Un-geocodable rows have NaN coords in `combined`; they emit JSON null so the
    frontend shows them in the ledger but skips them on the map.
    """
    coord_of = dict(zip(combined["event_id"], zip(combined["lat"], combined["lon"])))
    for a in view["assignments"]:
        lat, lon = coord_of.get(a["event_id"], (None, None))
        a["lat"] = None if lat is None or pd.isna(lat) else float(lat)
        a["lon"] = None if lon is None or pd.isna(lon) else float(lon)
    return view


def warm() -> None:
    """Load history + train the booster once. Called at server startup."""
    try:
        hist_df, hist_rep = load_events(CFG.data_csv)
        model = train_on_history(hist_df, CFG)
        with STATE.lock:
            STATE.hist_df = hist_df
            STATE.hist_rep = hist_rep
            STATE.model = model
            STATE.warm_error = None
        # seed an initial forecast so the first page paint is non-empty
        try:
            view = scrape_to_view()
            with STATE.lock:
                STATE.latest_view = view
        except Exception as e:  # seeding is best-effort; don't fail startup
            print(f"[warm] initial forecast seed skipped: {e}")
    except Exception as e:
        STATE.warm_error = str(e)
        raise


def predict_from_csv(raw_bytes: bytes) -> dict:
    """Forecast an uploaded CSV. Returns the view dict with lat/lon + meta.

    Raises ValueError (missing required columns) or a 'no usable rows' ValueError;
    main.py maps those to 400/422.
    """
    if not STATE.ready:
        raise RuntimeError("warming up")
    new_df, new_rep = load_events(io.BytesIO(raw_bytes))
    if new_rep.kept_rows == 0:
        raise ValueError(f"No usable events in the upload. {new_rep.summary()}")
    result = predict_new(STATE.model, STATE.hist_df, new_df, new_rep, CFG)
    view = attach_coords(result.view, result.combined)
    view["meta"] = {"source": "upload", "generated_at": _now_iso(),
                    "event_count": new_rep.kept_rows}
    return view


def _rows_to_df(rows: list[dict]):
    """Turn scraped row dicts into a (df, report) via the same ingest path."""
    buf = io.StringIO()
    # union of keys keeps every column the rows carry
    fieldnames = list({k for r in rows for k in r})
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    buf.seek(0)
    return load_events(io.StringIO(buf.getvalue()))


def scrape_to_view() -> dict:
    """Scrape upcoming events (PredictHQ -> cache fallback), forecast, return view.

    Imported lazily so a missing scraper dependency never breaks the API import.
    """
    import scrape_events as se

    events = []
    source = "cache"
    try:
        for fetch in se.SOURCES:
            got = fetch(config.CITY)
            if got:
                events.extend(got)
                source = "predicthq" if fetch.__name__ == "fetch_predicthq" else source
    except Exception as e:
        print(f"[scrape] fetch error, falling back to cache: {e}")
        events = []

    if not events:
        events = se.load_cached_events()
        source = "cache"

    geocode_cache = se._load_json(se.GEOCODE_CACHE)
    rows = se.to_rows(events, geocode_cache)
    try:
        se.GEOCODE_CACHE.write_text(json.dumps(geocode_cache, indent=2), encoding="utf-8")
    except Exception:
        pass  # read-only filesystem on some hosts; geocode cache is optional

    if not rows:
        raise RuntimeError("no events available (scrape empty and cache empty)")

    new_df, new_rep = _rows_to_df(rows)
    if new_rep.kept_rows == 0:
        raise RuntimeError("scraped events failed ingest validation")
    result = predict_new(STATE.model, STATE.hist_df, new_df, new_rep, CFG)
    view = attach_coords(result.view, result.combined)
    view["meta"] = {"source": f"scrape:{source}", "generated_at": _now_iso(),
                    "event_count": new_rep.kept_rows}
    return view
