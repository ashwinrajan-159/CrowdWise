"""Phase 0 end-to-end runner: ingest -> features -> target -> train -> replay.

Usage:
    python run_phase0.py                  # real censored-duration target
    python run_phase0.py --target synthetic
    python run_phase0.py --target both    # run both, write both artifacts

Writes to artifacts/:
    replay_log_<target>.csv   the predicted/acted/observed triple per event
    summary_<target>.json     KPIs + run metadata
    PHASE0_RESULTS.md         human-readable summary across targets
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import pandas as pd

from gridlock.config import DEFAULT, Config
from gridlock.features import build_features
from gridlock.ingest import load_events
from gridlock.lift import compute_lift
from gridlock.model import feature_importance, train_gbm
from gridlock.replay import run_replay
from gridlock.targets import get_target_provider
from gridlock.walkforward import run_walk_forward


def _run_walk_forward(df, feats, targets, cfg) -> None:
    print("\n[mode] WALK-FORWARD (retrain each month as history grows)")
    for tname in targets:
        print("\n" + "-" * 72)
        print(f"TARGET: {tname}")
        print("-" * 72)
        wf = run_walk_forward(df, feats, get_target_provider(tname), cfg)
        print(wf.report())
        wf.combined.log.to_csv(cfg.artifacts_dir / f"walkforward_log_{tname}.csv", index=False)
        print(f"  wrote walkforward_log_{tname}.csv")
    print(f"\n[done] artifacts in {cfg.artifacts_dir}")


def run_one(df, feats, train_mask, replay_mask, target_name: str, cfg: Config):
    tf = get_target_provider(target_name).build(df, feats, cfg)
    model = train_gbm(df, feats, tf, cfg, train_mask=train_mask)
    res = run_replay(df, feats, tf, model, cfg, train_mask=train_mask, replay_mask=replay_mask)
    imp = feature_importance(model)
    # Lift (system vs baselines) needs an allocation-responsive outcome — only
    # meaningful on the synthetic target (see lift.py). Skip for real duration.
    lift = None
    if tf.is_synthetic:
        pred = model.predict(df, feats, tf.y, tf.observed,
                             query_mask=replay_mask, history_mask=train_mask)
        lift = compute_lift(df, feats, pred.blended, tf.y, tf.observed, cfg,
                            replay_mask=replay_mask)
    return tf, model, res, imp, lift


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 0 historical-replay runner")
    ap.add_argument("--target", default="duration", choices=["duration", "synthetic", "both"])
    ap.add_argument("--csv", default=str(DEFAULT.data_csv))
    ap.add_argument("--planned-only", action="store_true",
                    help="evaluate the replay window on PLANNED events only (the wedge). "
                         "Still trains on all history.")
    ap.add_argument("--walk-forward", action="store_true",
                    help="month-by-month walk-forward replay (retrain each cycle) "
                         "instead of a single chronological split.")
    args = ap.parse_args()

    cfg = DEFAULT
    print("=" * 72)
    print("PLANNED-EVENT TRAFFIC OPS — PHASE 0 (historical replay)")
    print("=" * 72)

    df, report = load_events(args.csv)
    print(f"\n[ingest] {report.summary()}")

    feats = build_features(df, cfg)
    print(f"[features] {feats.shape[0]} rows x {feats.shape[1]} columns")

    cut = df["start_dt"].quantile(cfg.train_time_quantile)
    train_mask = df["start_dt"] <= cut
    replay_mask = df["start_dt"] > cut
    if args.planned_only:
        # train on all history; evaluate only on planned events (the wedge).
        replay_mask = replay_mask & (df["event_type"] == "planned")
        print(f"[split] train {int(train_mask.sum())} | replay {int(replay_mask.sum())} "
              f"PLANNED-ONLY (cut at {pd.Timestamp(cut).date()})")
    else:
        print(f"[split] train {int(train_mask.sum())} | replay {int(replay_mask.sum())} "
              f"(chronological, cut at {pd.Timestamp(cut).date()})")

    targets = ["duration", "synthetic"] if args.target == "both" else [args.target]

    if args.walk_forward:
        _run_walk_forward(df, feats, targets, cfg)
        return
    md_sections = []
    for tname in targets:
        print("\n" + "-" * 72)
        print(f"TARGET: {tname}")
        print("-" * 72)
        tf, model, res, imp, lift = run_one(df, feats, train_mask, replay_mask, tname, cfg)
        print(res.kpis.report())
        print(f"  playbooks: {res.playbook_count} | officer-deployments (sum): {res.overall_officers_used}")
        print(f"  top features (gain): {', '.join(imp.head(6).index)}")
        if lift is not None:
            print("  " + lift.report().replace("\n", "\n  "))

        # artifacts
        log_path = cfg.artifacts_dir / f"replay_log_{tname}.csv"
        res.log.to_csv(log_path, index=False)
        summary = {
            "target": tname,
            "ingest": asdict(report),
            "split_cut": str(pd.Timestamp(cut).date()),
            "train_rows": int(train_mask.sum()),
            "replay_rows": int(replay_mask.sum()),
            "kpis": asdict(res.kpis),
            "playbook_count": res.playbook_count,
            "officer_deployments": res.overall_officers_used,
            "top_features": imp.head(10).round(0).to_dict(),
            "lift": asdict(lift) if lift is not None else None,
        }
        (cfg.artifacts_dir / f"summary_{tname}.json").write_text(json.dumps(summary, indent=2))
        print(f"  wrote {log_path.name}, summary_{tname}.json")
        md_sections.append((tname, res, imp, lift))

    _write_markdown(cfg, report, cut, train_mask, replay_mask, md_sections)
    print(f"\n[done] artifacts in {cfg.artifacts_dir}")


def _write_markdown(cfg, report, cut, train_mask, replay_mask, sections) -> None:
    lines = [
        "# Phase 0 — Historical Replay Results",
        "",
        "Generated by `run_phase0.py`. Phase 0 validates the planned-event wedge by "
        "replaying past events: train on history, generate the playbook the system "
        "*would have* recommended, and score it against observed outcomes — the cheapest "
        "honest validation, with no live action to contaminate ground truth.",
        "",
        "## Data",
        f"- Source: `{cfg.data_csv.name}`",
        f"- {report.summary()}",
        f"- Chronological split: train {int(train_mask.sum())} / replay {int(replay_mask.sum())} "
        f"(cut at {pd.Timestamp(cut).date()})",
        "",
        "## KPI hierarchy",
        "North-star metrics are what an operations center actually cares about. "
        "Forecast MAE/RMSE are **diagnostics, not goals** — reported for the ML team only.",
        "",
    ]
    for tname, res, imp, lift in sections:
        k = res.kpis
        prov = "synthetic delay (enables full KPI loop)" if k.is_synthetic else "real censored event-duration"
        lines += [
            f"### Target: `{tname}` — {prov}",
            "",
            f"- Events replayed: {k.n_events} (observed/uncensored: {k.n_observed})",
            "",
            "| Tier | Metric | Value |",
            "|---|---|---|",
            f"| **North-star** | Vehicle-hours of delay (affected corridors) | {k.total_vehicle_hours_delay:,.0f} |",
            f"| **North-star** | Mean network-clearance proxy (min) | {k.mean_clearance_proxy_min:,.1f} |",
            f"| Diagnostic | Forecast MAE (min) | {k.forecast_mae:,.1f} |",
            f"| Diagnostic | Forecast RMSE (min) | {k.forecast_rmse:,.1f} |",
            f"| Operational | Daily playbooks generated | {res.playbook_count} |",
            f"| Operational | Officer-deployments (sum) | {res.overall_officers_used} |",
            "",
            f"Top model features (gain): {', '.join(imp.head(6).index)}",
            "",
        ]
        if lift is not None:
            lines += [
                "**Lift — does the system beat doing nothing / spreading blindly?** "
                "(mitigation stand-in; see `lift.py` for the honest boundary)",
                "",
                "| Allocation | Vehicle-hours of delay | Reduction vs do-nothing |",
                "|---|---|---|",
                f"| None (do-nothing floor) | {lift.veh_hours['none']:,.0f} | — |",
                f"| Uniform (spread blindly) | {lift.veh_hours['uniform']:,.0f} | {lift.lift_vs_none_pct['uniform']:+.1f}% |",
                f"| **System (delay × exposure)** | {lift.veh_hours['system']:,.0f} | {lift.lift_vs_none_pct['system']:+.1f}% |",
                "",
                f"**System vs Uniform (the real test): {lift.lift_system_vs_uniform_pct:+.1f}% "
                f"fewer vehicle-hours of delay.** A positive number means targeting impact "
                f"beats blind spreading — the planned-event wedge earning its keep.",
                "",
            ]
    lines += [
        "## Honest findings",
        "- **Analogs add no point-prediction lift on this data.** The GBM already learns "
        "from the same drivers (cause/hour/corridor), so any positive analog weight "
        "monotonically raised MAE. Analogs are retained as the *explanation + uncertainty "
        "surface* (auditable 'here are the 5 events behind this' + spread-based confidence), "
        "which is their real job. Blend weight capped at 0.10.",
        "- **Real duration is heavily censored** (~37% observed) and long-tailed (median "
        "~54 min, p90 ~12 h). MAE on it is large and honestly reported; the synthetic target "
        "exists precisely so the full north-star loop is demonstrable before a real probe feed.",
        "- **No speed/volume in the source** — north-star delay-hours use the duration/synthetic "
        "outcome × exposure as a proxy. A connected-vehicle feed drops in as a third "
        "`TargetProvider` with no downstream change.",
        "",
        "## Documented next steps (Phase 1)",
        "- Walk-forward replay (retrain each cycle) + a no-system baseline to measure *lift*.",
        "- Connect a real probe/connected-vehicle feed as the outcome target.",
        "- Shadow mode on randomized held-out events; synthetic controls; staggered rollout.",
    ]
    (cfg.artifacts_dir / "PHASE0_RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
