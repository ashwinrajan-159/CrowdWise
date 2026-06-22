# 🚦 Event-Driven Congestion — Planned-Event Traffic Operations

> Every city already knows when the next concert, procession, VIP convoy, or
> roadwork is coming. We turn that **known event calendar** into a
> pre-positioned officer-deployment playbook — and prove it pays off most
> exactly where the city has the advantage.

**Not another real-time congestion predictor.** That space is saturated and
commoditized. Our wedge is **planned events**, where the agency holds an
information advantage no general traffic app exploits — and controls the levers
(officers, closures, signals) to act on it days ahead.

### The result that matters

Validated by historical replay on **8,173 real events** (Bengaluru Traffic
Police "Astram" log, 467 planned, 5 months):

| | System reduces delay by |
|---|---|
| Across all events | **+4.1%** |
| **On planned events** | **+23.3%** |

**~6× more value on planned events — the wedge, quantified.** → [full results & honest caveats](RESULTS.md)

### See it run

The system produces an operator-reviewable **Deployment Ledger** — ranked
chokepoints, the score behind each, the five past events that justify it, and a
one-tap override. → `artifacts/operator_view.html`

The standout real call: on 17 Mar 2024, **10 of 12 officers** go to a late-night
VIP convoy down Mysore Road known days ahead. That's foreknowledge of a demand
shock the city controls the response to.

📐 Full design rationale & binding principles: [DESIGN.md](DESIGN.md)

---

## Quick start

```bash
pip install -r requirements.txt

python run_phase0.py                      # real censored-duration target
python run_phase0.py --target synthetic   # synthetic delay (full KPI loop)
python run_phase0.py --target both        # both, writes both artifacts

python run_phase0.py --planned-only       # evaluate on the wedge (planned events) only
python run_phase0.py --walk-forward       # retrain each month as history grows

pytest -q                                 # run the test suite (12 tests)
```

Artifacts are written to `artifacts/`:
- `replay_log_<target>.csv` — the **predicted / acted / observed** triple per event
- `summary_<target>.json` — KPIs + run metadata
- `PHASE0_RESULTS.md` — human-readable KPI report

---

## What Phase 0 does

Train on history → generate the playbook the system *would have* recommended →
score it against observed outcomes. Because no live action is taken, ground
truth stays uncontaminated — which is exactly why historical replay is the
honest first validation (see the "lost ground truth" problem in DESIGN.md §9).

```
ingest → features → target → analogs → GBM → playbook → replay → lift
```

| Module | Responsibility |
|---|---|
| `gridlock/ingest.py` | Load + validate + **quarantine** dirty rows (no silent drops) |
| `gridlock/features.py` | Cyclical time, spatial, event flags, concurrency |
| `gridlock/targets.py` | `TargetProvider`: real censored-duration + synthetic-delay (+ real-feed slot) |
| `gridlock/analogs.py` | Historical-analog retrieval — prediction **and** explanation surface |
| `gridlock/model.py` | LightGBM baseline + confidence-weighted analog blend |
| `gridlock/playbook.py` | `delay × exposure` heuristic, officer allocation, operator override |
| `gridlock/replay.py` | Predicted/acted/observed log + KPI hierarchy |
| `gridlock/lift.py` | System vs. baseline (none / uniform) — does it actually help? |
| `gridlock/walkforward.py` | Month-by-month retrain-as-history-grows replay |
| `run_phase0.py` | End-to-end runner |

---

## Design fidelity

This build follows the binding principles in DESIGN.md, not a generic congestion
predictor:

- **Model simplicity is a feature.** GBM + historical-analog baseline. No GNN
  (gated behind beating the baseline *and* sensor density — not met here).
- **One explainable heuristic** for allocation (`delay × exposure`), human in
  the loop with override. No optimizer zoo.
- **KPI hierarchy.** North-star metrics (vehicle-hours of delay, network-clearance
  proxy) are the goal; forecast MAE/RMSE are reported as **diagnostics, not goals**.
- **Evaluation is central.** Replay-first, on un-intervened history.

## Honest findings (Phase 0, this dataset)

These are surfaced, not buried — Phase 0 exists to discover them cheaply:

- **Analogs add no point-prediction lift.** The GBM already learns from the same
  drivers (cause/hour/corridor), so any positive analog weight monotonically
  raised MAE. Analogs are retained as the *explanation + uncertainty surface*
  (their real job); blend weight capped at 0.10.
- **Real duration is heavily censored** (~37% observed) and long-tailed — MAE on
  it is large and honestly reported. The synthetic target exists so the full
  north-star loop is demonstrable before a real probe feed.
- **The wedge holds.** On planned-only events, system-vs-do-nothing lift jumps
  from ~4% (all events) to ~23% — the scarce officer pool covers a far larger
  fraction of the smaller planned set, so targeting pays off most exactly where
  the thesis says it should.
- **No native speed/volume** in the source — north-star delay-hours use
  duration/synthetic × exposure as a proxy. A connected-vehicle feed drops in as
  a third `TargetProvider` with no downstream change.
- **Lift uses a documented mitigation stand-in** (officers reduce delay with
  diminishing returns, stated effect size), computed only on the synthetic
  target — a real historical duration is a fixed fact a never-executed
  allocation can't change. This is the honest boundary of a Phase 0 counterfactual.

---

## Next steps (Phase 1+)

- Connect a real probe / connected-vehicle feed as the outcome target.
- Replace the lift mitigation stand-in with quasi-experimental intervention
  evaluation (the design's causal-inference job).
- Shadow mode on randomized held-out events; synthetic controls; staggered rollout.
- The three-horizon operational flow (days/hours/minutes) and the live-adjustment
  streaming layer.
