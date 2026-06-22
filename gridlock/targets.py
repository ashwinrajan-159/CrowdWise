"""Prediction targets behind one swappable interface.

The event log has no native speed/delay data and durations are mostly NULL
(only ~74 of 8,173 rows are fully resolvable). So Phase 0 builds TWO targets
behind a single `TargetProvider` protocol, "real first":

  * DurationTarget  — real, censored event-duration (start -> resolved/closed).
                      Most honest: real data only, NULLs treated as
                      right-censored rather than dropped.
  * SyntheticDelayTarget — a documented, reproducible per-corridor delay model
                      derived from event attributes + seeded noise, so the FULL
                      north-star KPI loop (delay-hours, clearance) is
                      demonstrable end-to-end. Clearly flagged SYNTHETIC.

A real probe/connected-vehicle feed drops in later as a third implementation of
the same protocol — nothing downstream changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class TargetFrame:
    """Aligned to the event frame's index.

    y         : the regression target (minutes of disruption).
    observed  : 1 if y is a real observation, 0 if right-censored (we only know
                y >= the recorded value). DurationTarget produces censoring;
                SyntheticDelayTarget is fully observed.
    name      : human label for reporting.
    is_synthetic : provenance flag, surfaced in the results summary.
    """

    y: pd.Series
    observed: pd.Series
    name: str
    is_synthetic: bool


class TargetProvider(Protocol):
    def build(self, df: pd.DataFrame, feats: pd.DataFrame, cfg: Config) -> TargetFrame: ...


# --------------------------------------------------------------------------- #
# Real, censored event-duration target
# --------------------------------------------------------------------------- #
class DurationTarget:
    """Disruption duration in minutes, from start_dt to the earliest of
    resolved_dt / closed_dt / end_dt. Right-censored where no resolution
    timestamp exists OR the event is still 'active' OR the duration exceeds a
    plausibility cap (likely a stale record, not a 3-day jam)."""

    name = "event_duration_min"
    is_synthetic = False

    def build(self, df: pd.DataFrame, feats: pd.DataFrame, cfg: Config) -> TargetFrame:
        # Earliest real resolution signal. Build explicitly as datetime columns
        # so an all-NaT column doesn't degrade the frame to object dtype (which
        # breaks the row-wise min) — a robustness fix surfaced by the tests.
        end_candidates = pd.DataFrame({
            "resolved": pd.to_datetime(df["resolved_dt"], utc=True),
            "closed": pd.to_datetime(df["closed_dt"], utc=True),
            "end": pd.to_datetime(df["end_dt"], utc=True),
        })
        end = end_candidates.min(axis=1, skipna=True)
        minutes = (end - df["start_dt"]).dt.total_seconds() / 60.0

        observed = minutes.notna() & (minutes >= 0)
        # implausibly long -> treat as censored at the cap, not as a real value
        too_long = observed & (minutes > cfg.max_plausible_duration_min)

        y = minutes.copy()
        # For censored rows we still need a floor value (lower bound on duration).
        # Use elapsed-to-last-known where possible; else a small positive floor.
        floor = (df["created_dt"].fillna(df["start_dt"]) - df["start_dt"])
        floor_min = (floor.dt.total_seconds() / 60.0).clip(lower=1.0)
        y = y.where(observed & ~too_long, other=cfg.max_plausible_duration_min)
        y = y.where(~(~observed), other=floor_min)
        y = y.clip(lower=0.0)

        obs_flag = (observed & ~too_long).astype(int)
        return TargetFrame(y=y, observed=obs_flag, name=self.name, is_synthetic=False)


# --------------------------------------------------------------------------- #
# Synthetic per-corridor delay target (for the full KPI loop)
# --------------------------------------------------------------------------- #
# Documented effect sizes (minutes of peak delay contributed by each factor).
# These are a TRANSPARENT generative model, not learned — they exist only so the
# predicted/acted/observed loop and north-star KPIs are demonstrable before a
# real probe feed is connected. Swap this whole class for the feed adapter.
_CAUSE_BASE_DELAY = {
    "public_event": 35.0,
    "procession": 30.0,
    "vip_movement": 25.0,
    "protest": 28.0,
    "construction": 18.0,
    "congestion": 22.0,
    "accident": 20.0,
    "water_logging": 24.0,
    "road_conditions": 12.0,
    "pot_holes": 8.0,
    "tree_fall": 14.0,
    "vehicle_breakdown": 10.0,
    "others": 6.0,
}


class SyntheticDelayTarget:
    """Peak corridor delay (minutes) = cause baseline
        + closure bump + high-priority bump + corridor bump
        + concurrency interaction (events collide) + rush-hour multiplier
        + seeded Gaussian noise. Fully observed (no censoring)."""

    name = "synthetic_peak_delay_min"
    is_synthetic = True

    def build(self, df: pd.DataFrame, feats: pd.DataFrame, cfg: Config) -> TargetFrame:
        rng = np.random.default_rng(cfg.random_seed)
        base = df["event_cause"].map(_CAUSE_BASE_DELAY).fillna(6.0).to_numpy(dtype=float)

        closure = feats["requires_closure"].to_numpy() * 12.0
        priority = feats["is_high_priority"].to_numpy() * 6.0
        corridor = feats["is_corridor"].to_numpy() * 8.0
        # concurrent events make it nonlinearly worse (playbook collisions)
        concur = np.sqrt(feats["concurrent_events"].to_numpy()) * 5.0
        span = feats["event_span_km"].to_numpy() * 3.0

        hour = feats["hour_raw"].to_numpy()
        # rush-hour multiplier: peaks ~9am and ~6pm
        rush = 1.0 + 0.4 * (np.exp(-((hour - 9) ** 2) / 4) + np.exp(-((hour - 18) ** 2) / 4))

        signal = (base + closure + priority + corridor + concur + span) * rush
        noise = rng.normal(0.0, 0.15 * signal)  # heteroscedastic, ~15%
        # Cap at a plausible per-event peak delay (3h). Without this, a single
        # extreme-concurrency row produces a multi-hundred-minute outlier that
        # dominates the KPI loop — not realistic for one corridor.
        y = np.clip(signal + noise, 0.0, 180.0)

        y = pd.Series(y, index=df.index)
        observed = pd.Series(1, index=df.index)
        return TargetFrame(y=y, observed=observed, name=self.name, is_synthetic=True)


_PROVIDERS = {
    "duration": DurationTarget,
    "synthetic": SyntheticDelayTarget,
}


def get_target_provider(name: str) -> TargetProvider:
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ValueError(
            f"unknown target '{name}'. options: {sorted(_PROVIDERS)} "
            "(a real probe-feed adapter would register here)"
        )
