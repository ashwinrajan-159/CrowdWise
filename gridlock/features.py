"""Feature engineering for the Phase 0 baseline.

Four feature families, per the design brief:
  1. Cyclical time encoding (hour / day-of-week / day-of-year as sin/cos).
  2. Event flags & attributes (cause, closure, priority, point-vs-linear).
  3. Spatial distance-to-event (haversine to other events; corridor/zone).
  4. Concurrency (other active events within a radius and time window — where
     head-held playbooks break).

Deliberately NO `direction` feature: it is ~99% NULL in the source and
engineering around it would be noise. Everything is location-agnostic —
corridors/zones are treated as opaque categorical codes, never hardcoded.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2):
    """Vectorized haversine. Inputs in degrees; returns km."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _cyclical(values: pd.Series, period: int, name: str) -> pd.DataFrame:
    radians = 2 * np.pi * values / period
    return pd.DataFrame(
        {f"{name}_sin": np.sin(radians), f"{name}_cos": np.cos(radians)},
        index=values.index,
    )


def _concurrency_counts(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """For each event, count other events that are active within the time
    window AND within the spatial radius. O(n * window) via a time-sorted
    sweep, not O(n^2) — only events inside the time band are distance-checked.
    """
    n = len(df)
    counts = np.zeros(n, dtype=int)
    order = df["start_dt"].values.argsort()
    starts = df["start_dt"].values[order]
    lats = df["lat"].values[order]
    lons = df["lon"].values[order]
    window = np.timedelta64(int(cfg.concurrency_window_hours * 3600), "s")

    lo = 0
    for i in range(n):
        # advance the lower bound so [lo, i) are all within the time window
        while starts[i] - starts[lo] > window:
            lo += 1
        if i == lo:
            continue
        # distance check only against the time-neighbors
        nb_lat, nb_lon = lats[lo:i], lons[lo:i]
        if np.isnan(lats[i]) or np.isnan(lons[i]):
            continue
        d = _haversine_km(lats[i], lons[i], nb_lat, nb_lon)
        counts[i] = int(np.nansum(d <= cfg.concurrency_radius_km))

    result = np.zeros(n, dtype=int)
    result[order] = counts
    return pd.Series(result, index=df.index, name="concurrent_events")


def build_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Return a feature frame aligned to `df`'s index. Categorical columns are
    left as pandas 'category' dtype so LightGBM consumes them natively (handles
    missingness without imputation — important given NULL-heavy source data).
    """
    feats = pd.DataFrame(index=df.index)
    start = df["start_dt"].dt

    # --- 1. cyclical time ---------------------------------------------------
    feats = feats.join(_cyclical(start.hour, 24, "hour"))
    feats = feats.join(_cyclical(start.dayofweek, 7, "dow"))
    feats = feats.join(_cyclical(start.dayofyear, 366, "doy"))
    feats["is_weekend"] = (start.dayofweek >= 5).astype(int)
    feats["hour_raw"] = start.hour  # kept for analog matching / readability

    # --- 2. event flags & attributes ---------------------------------------
    feats["is_planned"] = (df["event_type"] == "planned").astype(int)
    feats["requires_closure"] = df["requires_road_closure"].astype(int)
    feats["is_high_priority"] = (df["priority"] == "High").astype(int)
    feats["is_linear"] = df["is_linear"].astype(int)
    feats["authenticated"] = df["authenticated"].astype(int)
    for col in ("event_cause", "corridor", "zone", "veh_type"):
        feats[col] = df[col].astype("category")

    # --- 3. spatial ---------------------------------------------------------
    # Span of a linear event (closures/processions have a footprint length).
    feats["event_span_km"] = np.where(
        df["is_linear"],
        _haversine_km(df["lat"], df["lon"], df["end_lat"], df["end_lon"]),
        0.0,
    )
    feats["is_corridor"] = (df["corridor"].fillna("Non-corridor") != "Non-corridor").astype(int)

    # --- 4. concurrency -----------------------------------------------------
    feats["concurrent_events"] = _concurrency_counts(df, cfg)

    return feats


# Columns the GBM trains on (everything in build_features except readability-only).
def feature_columns(feats: pd.DataFrame) -> list[str]:
    drop = {"hour_raw"}
    return [c for c in feats.columns if c not in drop]
