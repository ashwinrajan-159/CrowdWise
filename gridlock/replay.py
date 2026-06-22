"""Phase 0 historical-replay harness — the centerpiece.

We replay past events chronologically: train on history, generate the playbook
the system *would have* recommended, and compare against what we can observe.
For every event we log the triple the design doc mandates:

    (predicted / acted / observed)

      predicted : the model's delay forecast + the recommended officers + the
                  analogs behind it (the explanation surface).
      acted     : in pure replay there is no live intervention, so "acted" is
                  the recommended deployment recorded as the counterfactual plan
                  (the seam where a real shadow/live deployment would be logged).
      observed  : the realized outcome from the target (real duration where
                  uncensored; synthetic delay for the full KPI loop).

KPI hierarchy is enforced in the report:
  * NORTH-STAR (what leadership sees): vehicle-hours of delay on affected
    corridors, network-clearance proxy.
  * DIAGNOSTIC (ML team only, explicitly NOT a goal): forecast MAE / RMSE.

The lost-ground-truth problem is acknowledged structurally: replay measures the
forecast on UN-intervened history (clean ground truth), which is exactly why
historical replay is the honest Phase 0 validation before any live action
contaminates the signal.

PHASE-1 EXTENSIONS (documented, not built here):
  * Walk-forward replay — retrain monthly as history grows, replay the next
    slice, accumulate KPIs. More faithful to how the system learns each event
    cycle than this single 70/30 split, which is a clean but static holdout.
  * No-system baseline — a uniform/naive officer spread, so the KPI report can
    show the system's LIFT vs. doing nothing smart (the "beat the experienced
    engineer, not no-plan" framing). Requires the synthetic/probe target to
    score a counterfactual outcome under each allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .model import TrainedModel
from .playbook import generate_playbook, population_exposure


@dataclass
class ReplayKPIs:
    # --- north-star (reported to leadership) -------------------------------
    total_vehicle_hours_delay: float       # sum(observed_delay_min/60 * exposure)
    mean_clearance_proxy_min: float        # mean observed disruption duration/delay
    # --- diagnostics (ML team only — NOT success criteria) -----------------
    forecast_mae: float
    forecast_rmse: float
    n_events: int
    n_observed: int
    target_name: str
    is_synthetic: bool

    def report(self) -> str:
        prov = "SYNTHETIC target" if self.is_synthetic else "REAL censored-duration target"
        return (
            f"REPLAY KPIs ({prov}, {self.n_events} events, {self.n_observed} observed)\n"
            f"  NORTH-STAR (leadership):\n"
            f"    vehicle-hours of delay on affected corridors : {self.total_vehicle_hours_delay:,.0f}\n"
            f"    mean network-clearance proxy (min)            : {self.mean_clearance_proxy_min:,.1f}\n"
            f"  DIAGNOSTIC (ML team only — NOT a goal):\n"
            f"    forecast MAE  (min) : {self.forecast_mae:,.1f}\n"
            f"    forecast RMSE (min) : {self.forecast_rmse:,.1f}"
        )


@dataclass
class ReplayResult:
    log: pd.DataFrame          # the predicted/acted/observed triple, one row/event
    kpis: ReplayKPIs
    playbook_count: int
    overall_officers_used: int = field(default=0)


def _kpis(log: pd.DataFrame, target_name: str, is_synthetic: bool) -> ReplayKPIs:
    obs = log[log["observed"] == 1]
    n_obs = len(obs)
    if n_obs:
        err = obs["predicted_delay"] - obs["observed_outcome"]
        mae = float(err.abs().mean())
        rmse = float(np.sqrt((err ** 2).mean()))
        # vehicle-hours of delay: observed minutes -> hours, weighted by exposure
        veh_hours = float((obs["observed_outcome"] / 60.0 * obs["population_exposure"]).sum())
        clearance = float(obs["observed_outcome"].mean())
    else:
        mae = rmse = veh_hours = clearance = float("nan")
    return ReplayKPIs(
        total_vehicle_hours_delay=veh_hours,
        mean_clearance_proxy_min=clearance,
        forecast_mae=mae,
        forecast_rmse=rmse,
        n_events=len(log),
        n_observed=n_obs,
        target_name=target_name,
        is_synthetic=is_synthetic,
    )


def run_replay(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    tf,                      # TargetFrame
    model: TrainedModel,
    cfg: Config,
    *,
    train_mask: pd.Series,
    replay_mask: pd.Series,
) -> ReplayResult:
    """Generate the predicted/acted/observed log over the replay window and
    compute the KPI hierarchy. Playbooks are generated per replay DAY (the
    natural operational slice for the days-out horizon)."""
    pred = model.predict(
        df, feats, tf.y, tf.observed,
        query_mask=replay_mask, history_mask=train_mask,
    )
    exposure = population_exposure(df, feats)

    idx = np.where(replay_mask.to_numpy())[0]
    rows = []
    for i in idx:
        rows.append({
            "event_id": str(df["event_id"].iloc[i]),
            "start_dt": df["start_dt"].iloc[i],
            "event_type": df["event_type"].iloc[i],
            "cause": df["event_cause"].iloc[i],
            "corridor": df["corridor"].iloc[i],
            # --- predicted ---
            "predicted_delay": float(pred.blended[i]),
            "analog_weight": float(pred.analog_weight[i]),
            "analog_support": int(pred.analog_support[i]),
            "n_analogs": len(pred.analog_ids[i]) if i < len(pred.analog_ids) else 0,
            # --- acted (counterfactual plan recorded; seam for shadow/live) ---
            "population_exposure": float(exposure[i]),
            "recommended_score": float(pred.blended[i] * exposure[i]),
            # --- observed ---
            "observed": int(tf.observed.iloc[i]),
            "observed_outcome": float(tf.y.iloc[i]) if tf.observed.iloc[i] == 1 else np.nan,
        })
    log = pd.DataFrame(rows)

    # generate per-day playbooks (operational slice) and tally officers
    officers_used = 0
    day = df["start_dt"].dt.date
    pb_count = 0
    for d, _ in df.loc[replay_mask].groupby(day):
        slice_mask = (day == d) & replay_mask
        pb = generate_playbook(
            df, feats, pred.blended, pred.analog_ids, cfg,
            event_mask=slice_mask, slice_label=str(d),
        )
        officers_used += pb.officers_used
        pb_count += 1

    kpis = _kpis(log, tf.name, tf.is_synthetic)
    return ReplayResult(log=log, kpis=kpis, playbook_count=pb_count, overall_officers_used=officers_used)
