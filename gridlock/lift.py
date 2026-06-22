"""System-vs-baseline lift — does the system actually help?

Phase 0's replay scores the system's plan but compares it to nothing. The whole
thesis ("beat the experienced engineer, not no-plan") requires measuring LIFT:
the north-star delta between the system's allocation and a naive one.

To compare allocations you need an outcome that RESPONDS to allocation. The raw
targets don't (duration/synthetic delay are allocation-blind). So we add a
transparent, documented MITIGATION MODEL: officers assigned to a chokepoint
reduce its delay with diminishing returns.

    mitigated_delay = base_delay * (1 - effectiveness * (1 - exp(-officers / k)))

This is a STAND-IN with a stated effect size, not a measured causal estimate —
its only job is to let the lift machinery run end-to-end before a real probe
feed + quasi-experimental intervention evaluation (the design doc's causal-
inference job) replaces it. We therefore compute lift ONLY on the synthetic
target: a real historical duration is a fixed fact and cannot be retroactively
changed by an allocation that never happened. That restriction is the honest
boundary of a Phase 0 counterfactual.

Three allocators, all reusing the same officer-distribution mechanism — only the
SCORES differ:
  * system  : delay x exposure (the heuristic) — officers go where impact is.
  * uniform : equal scores — officers spread blindly, ignoring predicted impact.
  * none    : no officers (the do-nothing floor).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .playbook import _allocate_officers, population_exposure

# Mitigation effect size (documented stand-in, not measured):
#   at saturation, officers remove up to MAX_EFFECTIVENESS of a chokepoint's delay;
#   K controls how fast returns diminish per officer.
_MAX_EFFECTIVENESS = 0.35
_MITIGATION_K = 2.0


def _mitigate(base_delay: np.ndarray, officers: np.ndarray) -> np.ndarray:
    factor = 1.0 - _MAX_EFFECTIVENESS * (1.0 - np.exp(-officers / _MITIGATION_K))
    return base_delay * factor


def _alloc_for_day(scores_day: np.ndarray, pool: int, mode: str) -> np.ndarray:
    if mode == "none" or pool <= 0:
        return np.zeros(len(scores_day), dtype=int)
    if mode == "uniform":
        # equal scores -> spread officers blindly across active chokepoints
        return _allocate_officers(np.ones(len(scores_day)), pool)
    if mode == "system":
        return _allocate_officers(scores_day, pool)
    raise ValueError(f"unknown allocator mode {mode!r}")


@dataclass
class LiftResult:
    veh_hours: dict[str, float]      # mode -> total vehicle-hours of delay (mitigated)
    clearance: dict[str, float]      # mode -> mean mitigated delay (min)
    lift_vs_none_pct: dict[str, float]
    lift_system_vs_uniform_pct: float
    n_events: int

    def report(self) -> str:
        l = self.lift_vs_none_pct
        return (
            f"LIFT (system vs baselines, mitigation stand-in, {self.n_events} events)\n"
            f"  vehicle-hours of delay:  none {self.veh_hours['none']:,.0f}  |  "
            f"uniform {self.veh_hours['uniform']:,.0f}  |  system {self.veh_hours['system']:,.0f}\n"
            f"  reduction vs do-nothing: uniform {l['uniform']:+.1f}%  |  system {l['system']:+.1f}%\n"
            f"  SYSTEM vs UNIFORM (the real test): {self.lift_system_vs_uniform_pct:+.1f}% "
            f"fewer vehicle-hours of delay"
        )


def compute_lift(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    predicted_delay: np.ndarray,
    observed_delay: pd.Series,
    observed_flag: pd.Series,
    cfg: Config,
    *,
    replay_mask: pd.Series,
) -> LiftResult:
    """Score the same observed events under each allocator and compare north-star
    vehicle-hours of delay. Allocations are decided per DAY from the PREDICTED
    delay (operations act on the forecast); mitigation is applied to the OBSERVED
    delay (you mitigate what actually happens)."""
    exposure = population_exposure(df, feats)
    score = predicted_delay * exposure
    day = df["start_dt"].dt.date

    modes = ("none", "uniform", "system")
    officers = {m: np.zeros(len(df), dtype=int) for m in modes}

    # decide allocations day by day, over events active that day in the replay window
    for d, _ in df.loc[replay_mask].groupby(day):
        slice_idx = np.where(((day == d) & replay_mask).to_numpy())[0]
        if len(slice_idx) == 0:
            continue
        for m in modes:
            officers[m][slice_idx] = _alloc_for_day(score[slice_idx], cfg.officer_pool, m)

    # score on observed events only (we can only measure realized delay)
    ev = (replay_mask.to_numpy()) & (observed_flag.to_numpy() == 1)
    base = observed_delay.to_numpy()[ev]
    exp_ev = exposure[ev]

    veh_hours, clearance = {}, {}
    for m in modes:
        mitig = _mitigate(base, officers[m][ev])
        veh_hours[m] = float((mitig / 60.0 * exp_ev).sum())
        clearance[m] = float(mitig.mean())

    base_none = veh_hours["none"] or 1.0
    lift_vs_none = {m: 100.0 * (veh_hours["none"] - veh_hours[m]) / base_none for m in modes}
    base_uniform = veh_hours["uniform"] or 1.0
    lift_sys_vs_uni = 100.0 * (veh_hours["uniform"] - veh_hours["system"]) / base_uniform

    return LiftResult(
        veh_hours=veh_hours,
        clearance=clearance,
        lift_vs_none_pct=lift_vs_none,
        lift_system_vs_uniform_pct=lift_sys_vs_uni,
        n_events=int(ev.sum()),
    )
