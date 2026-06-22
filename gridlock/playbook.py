"""Resource-allocation heuristic + playbook generator.

The combinatorial space is tiny (a handful of officers, known chokepoints), so
this is ONE transparent scoring heuristic with a human in the loop — not an
optimizer:

    score(chokepoint) = predicted_delay x population_exposure

Rank chokepoints by score, assign the officer pool down the list until it's
exhausted. The operator sees every score component and the analog events behind
the prediction, and can override any assignment. No genetic algorithms, no
simulation-optimization zoo. Decision-focused learning is named in the design
doc as the sophisticated upgrade — deliberately not built here.

A "chokepoint" in Phase 0 is an active event's location (the agency's own event
log is the chokepoint inventory). population_exposure is derived from data —
corridor membership and a vulnerable-route flag — so nothing is hardcoded to a
city.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config

# Vulnerable-route flag contribution: events whose cause/closure implies high
# population exposure get extra weight so aggregate-delay optimization does not
# quietly starve high-impact corridors (the equity guard from the design doc).
_HIGH_EXPOSURE_CAUSES = {"public_event", "procession", "vip_movement", "protest", "congestion"}


@dataclass
class Assignment:
    event_id: str
    rank: int
    predicted_delay: float
    population_exposure: float
    score: float
    officers_assigned: int
    requires_closure: bool
    cause: str
    corridor: str | None
    analog_ids: list[str]
    # filled if an operator overrides the recommendation
    overridden: bool = False
    override_officers: int | None = None
    override_reason: str | None = None


@dataclass
class Playbook:
    """The recommended deployment for one time-slice / event window."""

    slice_label: str
    assignments: list[Assignment]
    officer_pool: int
    officers_used: int = field(init=False)

    def __post_init__(self) -> None:
        self.officers_used = sum(
            (a.override_officers if a.overridden and a.override_officers is not None
             else a.officers_assigned)
            for a in self.assignments
        )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([a.__dict__ for a in self.assignments])


def population_exposure(df: pd.DataFrame, feats: pd.DataFrame) -> np.ndarray:
    """Static-ish exposure weight per event, data-derived in [1, ~3]:
      base 1.0 + corridor membership + high-exposure cause + closure.
    Higher => more people affected if this chokepoint jams."""
    exp = np.ones(len(df))
    exp += 0.8 * feats["is_corridor"].to_numpy()
    exp += 0.6 * df["event_cause"].isin(_HIGH_EXPOSURE_CAUSES).to_numpy()
    exp += 0.4 * feats["requires_closure"].to_numpy()
    return exp


def _allocate_officers(scores: np.ndarray, pool: int, *, min_per: int = 1, max_per: int = 4) -> np.ndarray:
    """Distribute the officer pool across ranked chokepoints proportional to
    score, clamped to [min_per, max_per], top chokepoints first. Transparent
    and greedy — the operator can override any number afterward."""
    n = len(scores)
    alloc = np.zeros(n, dtype=int)
    if n == 0 or pool <= 0:
        return alloc
    order = np.argsort(scores)[::-1]
    remaining = pool
    # Pass 1: give every chokepoint we can reach min_per officers, top-down,
    # so coverage spreads rather than piling onto the top few.
    reached = []
    for idx in order:
        if remaining < min_per:
            break
        alloc[idx] = min_per
        remaining -= min_per
        reached.append(idx)
    # Pass 2: distribute leftover officers to the highest-scoring reached
    # chokepoints first, up to max_per each.
    for idx in reached:
        if remaining <= 0:
            break
        extra = min(max_per - alloc[idx], remaining)
        alloc[idx] += extra
        remaining -= extra
    return alloc


def generate_playbook(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    predicted_delay: np.ndarray,
    analog_ids: list[list[str]],
    cfg: Config,
    *,
    event_mask: pd.Series,
    slice_label: str,
) -> Playbook:
    """Build a ranked, officer-allocated playbook for the events in event_mask."""
    idx = np.where(event_mask.to_numpy())[0]
    if len(idx) == 0:
        return Playbook(slice_label=slice_label, assignments=[], officer_pool=cfg.officer_pool)

    exposure = population_exposure(df, feats)
    score = predicted_delay * exposure

    sub_scores = score[idx]
    alloc = _allocate_officers(sub_scores, cfg.officer_pool)

    order = np.argsort(sub_scores)[::-1]
    assignments: list[Assignment] = []
    for rank, j in enumerate(order, start=1):
        i = idx[j]
        assignments.append(Assignment(
            event_id=str(df["event_id"].iloc[i]),
            rank=rank,
            predicted_delay=float(predicted_delay[i]),
            population_exposure=float(exposure[i]),
            score=float(score[i]),
            officers_assigned=int(alloc[j]),
            requires_closure=bool(df["requires_road_closure"].iloc[i]),
            cause=str(df["event_cause"].iloc[i]),
            corridor=df["corridor"].iloc[i],
            analog_ids=analog_ids[i] if i < len(analog_ids) else [],
        ))
    return Playbook(slice_label=slice_label, assignments=assignments, officer_pool=cfg.officer_pool)


def apply_override(pb: Playbook, event_id: str, officers: int, reason: str) -> Playbook:
    """Operator overrides an assignment's officer count. Logged (overridden flag
    + reason), and the outcome feeds the post-event learning loop."""
    for a in pb.assignments:
        if a.event_id == event_id:
            a.overridden = True
            a.override_officers = officers
            a.override_reason = reason
    pb.__post_init__()  # recompute officers_used
    return pb
