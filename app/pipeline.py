"""Bridge between the FastAPI layer and the gridlock pipeline.

Reuses predict.train_on_history / predict_new and scrape_events as libraries.
Nothing here retrains the model on the request path — the cached booster in
cache.STATE is reused.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridlock.config import DEFAULT as CFG
from gridlock.ingest import load_events
from predict import train_on_history, predict_new

from app import config
from app.cache import STATE

# Accumulating store of events the app has seen (scraped/uploaded). As real-world
# events recede into the past, they become extra TRAINING history — this is the
# "improves with each event cycle" loop. We only ever retrain on events whose
# start time has passed (real occurrences), never on future guesses.
SEEN_EVENTS = Path(__file__).resolve().parent.parent / "seen_events.csv"

# Trained-model cache. Training LightGBM from scratch takes 30-90s on a small
# free-tier instance; persisting it turns every cold start after the first into a
# fast load. We save the booster in LightGBM's NATIVE text format (portable across
# library versions) + a small JSON sidecar — NOT pickle, which breaks when the
# deployed lightgbm/numpy versions differ from the machine that trained it.
# Cache key = fingerprint of the training history, so any data change invalidates.
MODEL_CACHE_DIR = Path(__file__).resolve().parent.parent / ".model_cache"


def _history_fingerprint(hist_df) -> str:
    """Stable hash of the training history so the model cache invalidates on change."""
    ids = ",".join(map(str, hist_df["event_id"].tolist()))
    return hashlib.sha256(f"{len(hist_df)}|{ids}".encode()).hexdigest()[:16]


def _load_cached_model(fp: str):
    txt = MODEL_CACHE_DIR / f"model_{fp}.txt"
    meta = MODEL_CACHE_DIR / f"model_{fp}.json"
    if not (txt.exists() and meta.exists()):
        return None
    try:
        import lightgbm as lgb
        from gridlock.model import TrainedModel
        booster = lgb.Booster(model_file=str(txt))
        m = json.loads(meta.read_text(encoding="utf-8"))
        return TrainedModel(booster=booster, feature_cols=m["feature_cols"],
                            cfg=CFG, target_name=m["target_name"])
    except Exception as e:
        print(f"[model-cache] load failed ({e}); will retrain")
        return None


def _save_cached_model(fp: str, model) -> None:
    try:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        model.booster.save_model(str(MODEL_CACHE_DIR / f"model_{fp}.txt"))
        (MODEL_CACHE_DIR / f"model_{fp}.json").write_text(
            json.dumps({"feature_cols": model.feature_cols,
                        "target_name": model.target_name}), encoding="utf-8")
        print(f"[model-cache] saved model_{fp} (native format)")
    except Exception as e:
        print(f"[model-cache] save skipped (non-fatal): {e}")


def train_or_load(hist_df):
    """Load the trained model from disk if the history matches; else train + cache."""
    fp = _history_fingerprint(hist_df)
    model = _load_cached_model(fp)
    if model is not None:
        print(f"[model-cache] loaded cached model ({fp}) — skipped training")
        return model
    print("[model-cache] no cached model — training…")
    model = train_on_history(hist_df, CFG)
    _save_cached_model(fp, model)
    return model


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
    """Load history + get the booster (cached on disk if available). At startup."""
    try:
        hist_df, hist_rep = load_events(CFG.data_csv)
        model = train_or_load(hist_df)
        with STATE.lock:
            STATE.hist_df = hist_df
            STATE.hist_rep = hist_rep
            STATE.model = model
            STATE.warm_error = None
        # Seed an initial forecast from the bundled CACHED sample (instant, no
        # network) so the first page paint is non-empty. The scheduler pulls live
        # PredictHQ events shortly after. A live scrape here could hang warm-up.
        try:
            view = scrape_to_view(offline=True)
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


def scrape_to_view(offline: bool = False) -> dict:
    """Scrape upcoming events (PredictHQ -> cache fallback), forecast, return view.

    offline=True skips the network and uses the bundled cached sample only —
    used for the instant startup seed so warm-up never hangs on a live fetch.
    Imported lazily so a missing scraper dependency never breaks the API import.
    """
    import scrape_events as se

    events = []
    source = "cache"
    if not offline:
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

    record_seen(rows)  # accumulate for the retrain-as-history-grows loop
    new_df, new_rep = _rows_to_df(rows)
    if new_rep.kept_rows == 0:
        raise RuntimeError("scraped events failed ingest validation")
    result = predict_new(STATE.model, STATE.hist_df, new_df, new_rep, CFG)
    view = attach_coords(result.view, result.combined)
    view["meta"] = {"source": f"scrape:{source}", "generated_at": _now_iso(),
                    "event_count": new_rep.kept_rows}
    return view


def record_seen(rows: list[dict]) -> None:
    """Append scraped/uploaded rows to the seen-events store (dedup by id).

    Best-effort: a read-only filesystem (some hosts) just skips persistence.
    """
    if not rows:
        return
    try:
        existing_ids = set()
        if SEEN_EVENTS.exists():
            prev = pd.read_csv(SEEN_EVENTS, dtype=str, keep_default_na=False)
            existing_ids = set(prev.get("id", pd.Series([], dtype=str)))
        fresh = [r for r in rows if str(r.get("id")) not in existing_ids]
        if not fresh:
            return
        fieldnames = sorted({k for r in rows for k in r})
        write_header = not SEEN_EVENTS.exists()
        with SEEN_EVENTS.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                w.writeheader()
            w.writerows(fresh)
    except Exception as e:
        print(f"[seen] could not persist seen events (non-fatal): {e}")


def retrain() -> dict:
    """Rebuild history (original log + PAST seen events) and refit the model.

    Only events whose start time has already passed are added as training history —
    we learn from what actually happened, never from future guesses. The refit is
    ~1-2s; the new booster is swapped into the cache atomically under the lock.
    Returns a small summary.
    """
    base_df, base_rep = load_events(CFG.data_csv)
    added = 0
    hist_df = base_df

    if SEEN_EVENTS.exists():
        try:
            seen_df, _ = load_events(SEEN_EVENTS)
            now = pd.Timestamp.now(tz="UTC")
            past = seen_df[seen_df["start_dt"].notna() & (seen_df["start_dt"] <= now)]
            if len(past):
                # de-dup against the base log by event_id, then append
                past = past[~past["event_id"].isin(set(base_df["event_id"]))]
                if len(past):
                    hist_df = pd.concat([base_df, past], ignore_index=True)
                    added = len(past)
        except Exception as e:
            print(f"[retrain] seen-events merge skipped: {e}")

    # retrain always fits fresh (history grew); cache the result so the next
    # restart loads it instead of retraining.
    hist_df = hist_df.reset_index(drop=True)
    model = train_on_history(hist_df, CFG)
    _save_cached_model(_history_fingerprint(hist_df), model)
    with STATE.lock:
        STATE.hist_df = hist_df
        STATE.model = model
    summary = {"base_events": base_rep.kept_rows, "added_from_seen": added,
               "history_total": len(hist_df), "retrained_at": _now_iso()}
    print(f"[retrain] refit on {len(hist_df)} events (+{added} newly seen)")
    return summary
