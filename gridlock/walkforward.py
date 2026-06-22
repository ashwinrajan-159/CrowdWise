"""Walk-forward replay — retrain as history grows, one event cycle at a time.

The single 70/30 split in replay.py is a clean but STATIC holdout: it trains
once and judges the model on the tail. That understates how the system actually
operates, which is to refresh its model after each event cycle and apply it to
the next. Walk-forward replay models that honestly:

    for each month M in the replay window:
        train on everything strictly before M
        replay the events in M (history = everything before M)
        accumulate the predicted/acted/observed log + KPIs

This needs enough history before the first fold, so folds whose training set is
too small are skipped (and reported, never silently dropped). The accumulated
log is directly comparable to the single-split log — same columns — so lift and
KPI code is reused unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Config
from .model import train_gbm
from .replay import ReplayResult, _kpis, run_replay
from .targets import TargetProvider


@dataclass
class FoldResult:
    period: str
    train_rows: int
    replay_rows: int
    mae: float
    veh_hours: float


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    combined: ReplayResult       # KPIs over the concatenated per-fold logs
    skipped: list[str]

    def report(self) -> str:
        lines = [
            f"WALK-FORWARD REPLAY ({len(self.folds)} folds"
            + (f", {len(self.skipped)} skipped (insufficient history)" if self.skipped else "")
            + ")",
            f"  {'period':<10} {'train':>7} {'replay':>7} {'MAE(min)':>10} {'veh-hrs':>10}",
        ]
        for f in self.folds:
            lines.append(
                f"  {f.period:<10} {f.train_rows:>7} {f.replay_rows:>7} "
                f"{f.mae:>10.1f} {f.veh_hours:>10,.0f}"
            )
        k = self.combined.kpis
        lines.append(
            f"  COMBINED: {k.n_observed} observed | MAE {k.forecast_mae:.1f} min | "
            f"{k.total_vehicle_hours_delay:,.0f} veh-hours"
        )
        return "\n".join(lines)


def run_walk_forward(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    provider: TargetProvider,
    cfg: Config,
    *,
    min_train_rows: int = 1000,
) -> WalkForwardResult:
    """Month-by-month walk-forward. The target is rebuilt per fold from the same
    provider so a synthetic target's seeded noise is identical to the single-split
    run (reproducibility)."""
    tf_full = provider.build(df, feats, cfg)  # built once; sliced per fold below
    # tz-naive period for month bucketing (values are all UTC; tz dropped only here)
    month = df["start_dt"].dt.tz_localize(None).dt.to_period("M")
    periods = sorted(month.unique())

    fold_results: list[FoldResult] = []
    logs: list[pd.DataFrame] = []
    skipped: list[str] = []

    for p in periods:
        train_mask = month < p
        replay_mask = month == p
        n_train = int(train_mask.sum())
        if n_train < min_train_rows or replay_mask.sum() == 0:
            skipped.append(str(p))
            continue

        model = train_gbm(df, feats, tf_full, cfg, train_mask=train_mask)
        res = run_replay(
            df, feats, tf_full, model, cfg,
            train_mask=train_mask, replay_mask=replay_mask,
        )
        logs.append(res.log)
        fold_results.append(FoldResult(
            period=str(p),
            train_rows=n_train,
            replay_rows=int(replay_mask.sum()),
            mae=res.kpis.forecast_mae,
            veh_hours=res.kpis.total_vehicle_hours_delay,
        ))

    combined_log = pd.concat(logs, ignore_index=True) if logs else pd.DataFrame()
    combined_kpis = _kpis(combined_log, tf_full.name, tf_full.is_synthetic)
    combined = ReplayResult(
        log=combined_log,
        kpis=combined_kpis,
        playbook_count=len(fold_results),  # one model-refresh cycle per fold
    )
    return WalkForwardResult(folds=fold_results, combined=combined, skipped=skipped)
