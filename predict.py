#!/usr/bin/env python
"""Forecast chokepoints + officer deployment for a NEW / UPCOMING events CSV.

This is the forward-looking counterpart to `run_phase0.py` (which validates on
history). Here we:

    1. train on the full historical Astram log (events with known outcomes), then
    2. predict the traffic impact of the events in a NEW csv — which need NOT
       carry any outcome columns, because the outcome is exactly what we predict —
       and
    3. write a Deployment Ledger (the operator dashboard) for those events.

Every input the model needs (event cause, location, time, priority) is known
*before* a planned event happens. The only column a historical row has that a
future row doesn't — the resolution time — is the thing being forecast. That is
why a calendar of upcoming events is enough to produce a plan.

Usage:
    python predict.py upcoming_events.csv
    python predict.py upcoming_events.csv --out artifacts/operator_view_upcoming.html

Note (honest): no accuracy/lift number is produced for a future file. Future
events have no observed outcome yet, so there is nothing to score against — that
is the lost-ground-truth problem. Accuracy is validated on history
(`run_phase0.py --planned-only` → +23.3% lift on planned events); this tool
shows the operational *output* on unseen events.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from dataclasses import dataclass

from gridlock.config import DEFAULT
from gridlock.ingest import load_events
from gridlock.features import build_features
from gridlock.targets import get_target_provider
from gridlock.model import train_gbm
from gridlock.playbook import generate_playbook


@dataclass
class PredictionResult:
    """Output of predict_new: the dashboard view dict + the combined frame.

    `combined` is returned so the caller can join lat/lon back by event_id
    (the view itself omits coordinates by design — see app/pipeline.attach_coords).
    """
    view: dict
    combined: "pd.DataFrame"
    playbook: object


def train_on_history(hist_df, cfg=DEFAULT):
    """Train the LightGBM booster on the full history. The cached ~1-2s cost.

    Importable by the web backend so the fit happens once at startup rather than
    per request. Uses the synthetic target (history has no native speed).
    """
    feats = build_features(hist_df, cfg)
    tf = get_target_provider("synthetic").build(hist_df, feats, cfg)
    train_mask = pd.Series(True, index=hist_df.index)
    return train_gbm(hist_df, feats, tf, cfg, train_mask=train_mask)


def predict_new(model, hist_df, new_df, new_rep, cfg=DEFAULT) -> PredictionResult:
    """Forecast the new events using an already-trained booster.

    build_features must run on the COMBINED frame (concurrency/spatial features
    for a new event depend on neighbouring events), so only train_gbm is skipped
    here — the per-request cost is features + predict, not a refit.
    """
    hist_df = hist_df.reset_index(drop=True)
    new_df = new_df.reset_index(drop=True)
    combined = pd.concat([hist_df, new_df], ignore_index=True)
    is_new = pd.Series([False] * len(hist_df) + [True] * len(new_df), index=combined.index)

    feats = build_features(combined, cfg)
    tf = get_target_provider("synthetic").build(combined, feats, cfg)
    pred = model.predict(
        combined, feats, tf.y, tf.observed,
        query_mask=is_new, history_mask=~is_new,
    )
    label = f"upcoming · {new_rep.kept_rows} events"
    pb = generate_playbook(
        combined, feats, pred.blended, pred.analog_ids, cfg,
        event_mask=is_new, slice_label=label,
    )
    view = _build_view(combined, feats, pred, pb, new_rep)
    return PredictionResult(view=view, combined=combined, playbook=pb)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("new_csv", help="CSV of upcoming events (same columns as the Astram log)")
    ap.add_argument("--history", default=str(DEFAULT.data_csv),
                    help="historical event log to train on (default: bundled Astram log)")
    ap.add_argument("--out", default="artifacts/operator_view_upcoming.html",
                    help="where to write the Deployment Ledger HTML")
    ap.add_argument("--template", default="artifacts/operator_view.html",
                    help="existing operator view used as the HTML template")
    args = ap.parse_args()

    cfg = DEFAULT
    print("=" * 72)
    print("PLANNED-EVENT TRAFFIC OPS — forward prediction on a new event file")
    print("=" * 72)

    # --- load history + new events, validate both through the same ingest -----
    hist_df, hist_rep = load_events(args.history)
    new_df, new_rep = load_events(args.new_csv)
    print(f"\n[history] {hist_rep.summary()}")
    print(f"[new]     {new_rep.summary()}")

    if new_rep.kept_rows == 0:
        raise SystemExit("No usable events in the new CSV. Check the columns match the Astram schema.")

    # train on history (cached cost), then forecast the new events
    model = train_on_history(hist_df, cfg)
    result = predict_new(model, hist_df, new_df, new_rep, cfg)
    pb = result.playbook
    print(f"[features] combined frame ({new_rep.kept_rows} upcoming)")

    print(f"\n[playbook] {len(pb.assignments)} chokepoints ranked | "
          f"{pb.officers_used}/{pb.officer_pool} officers deployed")
    print("\n  TOP CHOKEPOINTS")
    for a in pb.assignments[:8]:
        flag = " [CLOSURE]" if a.requires_closure else ""
        print(f"   #{a.rank:<2} {a.cause:14} score={a.score:6.1f}  "
              f"delay={a.predicted_delay:5.1f}m  officers={a.officers_assigned}{flag}")

    # --- write the HTML -------------------------------------------------------
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _render_html(Path(args.template), out_path, result.view)
    print(f"\n[done] Deployment Ledger written to {out_path}")
    print("       open it in a browser to review and override the plan.")


def _build_view(df, feats, pred, pb, rep) -> dict:
    """Assemble the JSON payload the operator dashboard renders."""
    raw_addr = df["address"] if "address" in df.columns else df.get("corridor")
    addr_of = dict(zip(df["event_id"], raw_addr)) if raw_addr is not None else {}
    cause_of = dict(zip(df["event_id"], df["event_cause"]))
    type_of = dict(zip(df["event_id"], df["event_type"]))
    start_of = dict(zip(df["event_id"], df["start_dt"]))

    def short(eid):
        a = addr_of.get(eid)
        if a is None or pd.isna(a):
            return ""
        return str(a).split(",")[0].strip()[:40]

    rows = []
    for a in pb.assignments[:14]:
        rows.append({
            "rank": a.rank, "event_id": a.event_id, "cause": a.cause,
            "type": type_of.get(a.event_id, "planned"),
            "addr": short(a.event_id) or (a.corridor or "Event location"),
            "corridor": a.corridor or "Non-corridor",
            "start": str(start_of.get(a.event_id))[11:16],
            "predicted_delay": round(a.predicted_delay, 1),
            "exposure": round(a.population_exposure, 1),
            "score": round(a.score, 1),
            "officers": a.officers_assigned,
            "closure": a.requires_closure,
            "analogs": [{"id": aid, "cause": cause_of.get(aid, "?"), "addr": short(aid)}
                        for aid in a.analog_ids],
        })
    closures = sum(1 for a in pb.assignments if a.requires_closure)
    # date range of the UPCOMING events only (not the historical training data)
    upcoming_starts = [start_of.get(r["event_id"]) for r in rows if start_of.get(r["event_id"]) is not None]
    if upcoming_starts:
        lo, hi = str(min(upcoming_starts))[:10], str(max(upcoming_starts))[:10]
        day = lo if lo == hi else f"{lo}|{hi}"
    else:
        day = "upcoming"
    return {
        "day": day, "pool": pb.officer_pool, "used": pb.officers_used,
        "total_chokepoints": len(pb.assignments), "planned": rep.planned_rows,
        "closures": closures, "assignments": rows,
    }


def _render_html(template: Path, out: Path, view: dict) -> None:
    """Swap the DATA constant in the operator-view template for the new payload."""
    html = template.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = html.index(marker) + len(marker)
    end = html.index(";", start)
    new_html = html[:start] + json.dumps(view) + html[end:]
    # retitle so it's clearly the upcoming-events ledger
    new_html = new_html.replace(
        "<title>Deployment Ledger — 17 Mar 2024</title>",
        f"<title>Deployment Ledger — {view['day']} (upcoming)</title>",
    )
    out.write_text(new_html, encoding="utf-8")


if __name__ == "__main__":
    main()
