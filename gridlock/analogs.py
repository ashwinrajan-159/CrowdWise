"""Historical-analog retrieval.

The experienced traffic engineer's mental model — *"what happened the last 5
times this venue had a sold-out show on a rainy Friday?"* — turned into a
retrievable feature AND the explanation surface that earns operator trust.

For a target event we find the k most similar PAST events (strictly earlier in
time — no leakage from the future) by a weighted match over cause, location,
time-of-day, weekday, closure flag, and corridor, then summarize their realized
outcomes:
  * analog point estimate (support-weighted mean outcome),
  * analog spread (wide spread => low confidence => flag for operator),
  * the analog event ids themselves (so a recommendation is auditable).

This is deliberately a transparent nearest-neighbor scheme, not a learned
embedding: an operator must be able to see and trust the five events behind a
number.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .features import _haversine_km

# Similarity component weights. Cause and location dominate — an engineer first
# asks "same kind of event, same place?" then refines by time.
_W_CAUSE = 3.0
_W_LOCATION = 2.5
_W_HOUR = 1.5
_W_DOW = 1.0
_W_CLOSURE = 1.0
_W_CORRIDOR = 1.5

_LOCATION_SCALE_KM = 2.0  # distance at which the location score halves-ish
_HOUR_SCALE = 4.0         # hours; gaussian falloff


@dataclass
class AnalogResult:
    """Per-event analog summary, aligned to the query event's index position."""

    estimate: np.ndarray      # support-weighted mean outcome over analogs
    spread: np.ndarray        # std of analog outcomes (uncertainty)
    support: np.ndarray       # number of analogs found
    analog_ids: list[list[str]]  # the event_ids behind each estimate


@dataclass
class _Cols:
    """Pre-extracted numpy columns — avoids pandas per-row access in the hot loop."""

    cause: np.ndarray
    closure: np.ndarray
    corridor: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    hour: np.ndarray
    dow: np.ndarray
    start: np.ndarray
    event_id: np.ndarray

    @classmethod
    def of(cls, df: pd.DataFrame) -> "_Cols":
        return cls(
            cause=df["event_cause"].fillna("~").to_numpy(),
            closure=df["requires_road_closure"].to_numpy(),
            corridor=df["corridor"].fillna("~").to_numpy(),
            lat=df["lat"].to_numpy(dtype=float),
            lon=df["lon"].to_numpy(dtype=float),
            hour=df["start_dt"].dt.hour.to_numpy(),
            dow=df["start_dt"].dt.dayofweek.to_numpy(),
            start=df["start_dt"].to_numpy(),
            event_id=df["event_id"].to_numpy(),
        )


def _similarity(c: _Cols, i: int, cand_idx: np.ndarray) -> np.ndarray:
    """Weighted similarity of candidates (by row index `cand_idx`) to query row i."""
    s = np.zeros(len(cand_idx))
    s += _W_CAUSE * (c.cause[cand_idx] == c.cause[i])
    s += _W_CLOSURE * (c.closure[cand_idx] == c.closure[i])
    s += _W_CORRIDOR * (c.corridor[cand_idx] == c.corridor[i])

    if not (np.isnan(c.lat[i]) or np.isnan(c.lon[i])):
        d = _haversine_km(c.lat[i], c.lon[i], c.lat[cand_idx], c.lon[cand_idx])
        loc = np.exp(-(d ** 2) / (2 * _LOCATION_SCALE_KM ** 2))
        s += _W_LOCATION * np.nan_to_num(loc)

    hour_diff = np.minimum(np.abs(c.hour[cand_idx] - c.hour[i]), 24 - np.abs(c.hour[cand_idx] - c.hour[i]))
    s += _W_HOUR * np.exp(-(hour_diff ** 2) / (2 * _HOUR_SCALE ** 2))
    s += _W_DOW * (c.dow[cand_idx] == c.dow[i])
    return s


def retrieve_analogs(
    df: pd.DataFrame,
    y: pd.Series,
    observed: pd.Series,
    cfg: Config,
    *,
    query_mask: pd.Series | None = None,
    history_mask: pd.Series | None = None,
) -> AnalogResult:
    """For each event in `query_mask`, find analogs drawn from `history_mask`
    that occurred STRICTLY EARLIER. Only uncensored (observed==1) outcomes are
    used for the estimate — a censored duration is a lower bound, not a value.

    If masks are None: every event queries against all earlier events (the
    "online" replay setting). Pass explicit masks for a train/replay split.
    """
    n = len(df)
    if query_mask is None:
        query_mask = pd.Series(True, index=df.index)
    if history_mask is None:
        history_mask = pd.Series(True, index=df.index)

    estimate = np.full(n, np.nan)
    spread = np.full(n, np.nan)
    support = np.zeros(n, dtype=int)
    analog_ids: list[list[str]] = [[] for _ in range(n)]

    df = df.reset_index(drop=True)
    y = y.reset_index(drop=True)
    observed = observed.reset_index(drop=True)
    qmask = query_mask.reset_index(drop=True).to_numpy()
    hmask = history_mask.reset_index(drop=True).to_numpy()

    c = _Cols.of(df)
    y_arr = y.to_numpy(dtype=float)

    # candidate pool: in history, observed outcome only. Sorted by start time so
    # "strictly earlier than query i" is a single searchsorted cut.
    pool_idx = np.where(hmask & observed.to_numpy().astype(bool))[0]
    pool_idx = pool_idx[np.argsort(c.start[pool_idx], kind="stable")]
    pool_starts = c.start[pool_idx]

    k = cfg.analog_k
    for i in np.where(qmask)[0]:
        cut = np.searchsorted(pool_starts, c.start[i], side="left")
        if cut == 0:
            continue
        cand_idx = pool_idx[:cut]
        sim = _similarity(c, i, cand_idx)

        # top-k by similarity (require some minimal match > 0)
        top = np.argpartition(sim, -min(k, len(sim)))[-min(k, len(sim)):]
        top = top[sim[top] > 0]
        if len(top) == 0:
            continue
        w = sim[top]
        vals = y_arr[cand_idx[top]]
        est = float(np.average(vals, weights=w))
        estimate[i] = est
        spread[i] = float(np.sqrt(np.average((vals - est) ** 2, weights=w)))
        support[i] = len(top)
        analog_ids[i] = c.event_id[cand_idx[top]].tolist()

    return AnalogResult(estimate=estimate, spread=spread, support=support, analog_ids=analog_ids)
