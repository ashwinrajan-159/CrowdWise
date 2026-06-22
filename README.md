# 🚦 Event-Driven Congestion

### A planned-event traffic-operations system

> Every city already knows when the next concert, procession, VIP convoy, or
> roadwork is coming — it's on a calendar weeks out. This project turns that
> **known event calendar** into a pre-positioned, accountable officer-deployment
> playbook, and proves with real data that it delivers most exactly where the
> city holds the advantage.

**This is deliberately *not* another real-time congestion predictor.** That space
is saturated and commoditized. The wedge here is the **planned event**, where the
agency has two things a general traffic app never will: *foreknowledge* (the event
calendar) and *control of the response* (officers, closures, signal timing) days
in advance.

📐 Full rationale: [DESIGN.md](DESIGN.md) · 📊 Results & honest caveats: [RESULTS.md](RESULTS.md)

---

## The headline result

Validated by historical replay on the **Bengaluru Traffic Police "Astram" event
log** — 8,173 real events, 467 planned, over 5 months:

| Slice | System reduces vehicle-hours of delay by |
|---|---|
| Across all events | **+4.1%** |
| **On planned events only** | **+23.3%** |

**~6× more value on planned events.** Same model, same fixed officer pool —
pointed where the city has foreknowledge it controls. That is the wedge, quantified.

---

## Why is there a "Phase 0"?

The hardest problem in any system that *acts* on traffic predictions is the
**lost-ground-truth problem**:

> If the system predicts a jam, an officer reroutes traffic, and the jam never
> appears — *was the forecast wrong, or did the action prevent it?* You can't
> tell. Worse, you've just logged "predicted jam → no jam" as training data,
> teaching the model the situation was fine — when it was only fine *because you
> intervened*. **Acting on a prediction destroys your own ground truth and poisons
> your training set.** A naive system gets *worse* the more it's trusted.

So you cannot honestly validate this kind of system by deploying it and watching.
The cheapest, cleanest validation is to **replay history** — take past events,
generate the playbook the system *would have* recommended, and score it against
what actually happened. No live action is taken, so ground truth stays
uncontaminated.

**Phase 0 is that historical replay.** It's the first stage of a staged roadmap
precisely because it's the cheapest way to prove (or kill) the core thesis before
spending on live integration:

| Phase | What it adds | Status |
|---|---|---|
| **Phase 0 — Historical replay** | Ingest the event log, train the model, replay past events, measure lift on un-intervened history. **Proves the wedge.** | ✅ **This repo** |
| Phase 1 — Shadow mode | Live data in, recommendations logged, operators run their own plans → clean baseline + causal effect estimates | planned |
| Phase 2 — Staggered live rollout | Randomized held-out events, synthetic controls, real probe/connected-vehicle feed | planned |
| Phase 3 — Digital twin (one venue) | Microsimulation for high-stakes closure scenarios | deferred |

Building Phase 0 first means the expensive phases only happen if the cheap one
says the idea works. It does — see the result above.

---

## How it works

The pipeline is one straight line — train on history, generate the playbook,
replay it, measure whether it helped:

```
ingest → features → target → analogs → model → playbook → replay → lift
```

| Stage | Module | What it does |
|---|---|---|
| **Ingest** | [gridlock/ingest.py](gridlock/ingest.py) | Loads the event log, validates rows, **quarantines** dirty data (bad coords, missing timestamps) instead of silently dropping it |
| **Features** | [gridlock/features.py](gridlock/features.py) | Cyclical time encodings, spatial/corridor flags, event-type flags, event concurrency |
| **Target** | [gridlock/targets.py](gridlock/targets.py) | `TargetProvider` interface: real **censored-duration** target + a **synthetic-delay** target. A real probe feed drops in here as a third provider with zero downstream change |
| **Analogs** | [gridlock/analogs.py](gridlock/analogs.py) | Retrieves the most similar past events — the **auditable explanation surface** ("here are the 5 events behind this call") |
| **Model** | [gridlock/model.py](gridlock/model.py) | LightGBM gradient-boosted baseline + a confidence-weighted analog blend |
| **Playbook** | [gridlock/playbook.py](gridlock/playbook.py) | Ranks chokepoints by `predicted delay × population exposure`, allocates the officer pool, supports operator override |
| **Replay** | [gridlock/replay.py](gridlock/replay.py) | Builds the predicted / acted / observed log and computes the KPI hierarchy |
| **Lift** | [gridlock/lift.py](gridlock/lift.py) | System vs. baselines (do-nothing / uniform) — *does it actually help?* |
| **Walk-forward** | [gridlock/walkforward.py](gridlock/walkforward.py) | Retrains month-by-month as history grows, to test stability over time |
| **Runner** | [run_phase0.py](run_phase0.py) | Ties it all together and writes the artifacts |

**A design choice worth calling out:** forecast accuracy (MAE/RMSE) is treated as
a *diagnostic, not the goal*. Nobody in a control room cares about RMSE — they care
about clearance time and vehicle-hours of delay. The KPI hierarchy reflects that.

---

## Tech stack — and why

Deliberately boring and commodity. The thesis is that the *coordination layer* is
the hard part, not the model — so the model stack is chosen for reliability and
explainability, not novelty.

| Tool | Version | Used for | Why this one |
|---|---|---|---|
| **Python** | 3.11+ | everything | Standard for data/ML pipelines |
| **pandas** | 2.3.3 | ingest, feature engineering, the whole data flow | The default for tabular event data |
| **NumPy** | 2.3.3 | numerical ops under the hood | Pandas/scikit foundation |
| **LightGBM** | 4.6.0 | the gradient-boosted forecast model | Fast, handles missing values *natively* (the event log is full of NULLs), and is **explainable enough for an operations center to trust** — a binding design constraint. No deep learning: a GNN was explicitly gated behind beating this baseline *and* having sensor density, neither of which is met |
| **scikit-learn** | 1.6.1 | metrics, splitting, utility transforms | Standard companion to a GBM workflow |
| **pytest** | — | the 12-test suite that pins pipeline invariants | Catches regressions (already caught a real censoring bug) |

> **No GNN, no optimizer zoo, no microsimulation.** Model simplicity is a feature
> here, not a limitation — see DESIGN.md for why each was deliberately deferred or
> rejected. The operator-facing views ([operator_view.html](artifacts/operator_view.html),
> [pitch.html](artifacts/pitch.html)) are self-contained HTML/CSS/JS with zero
> dependencies.

---

## Steps to run

**1. Install dependencies** (Python 3.11+):

```bash
pip install -r requirements.txt
```

**2. Run the replay.** The anonymized event log ships with the repo, so it works
on clone with no setup:

```bash
# Default: real censored-duration target
python run_phase0.py

# Synthetic-delay target — demonstrates the full north-star KPI loop
python run_phase0.py --target synthetic

# Both targets, writes both sets of artifacts
python run_phase0.py --target both

# The wedge result: evaluate on planned events only (this is the +23.3%)
python run_phase0.py --planned-only

# Walk-forward: retrain each month as history grows, to test stability
python run_phase0.py --walk-forward
```

**3. Check the outputs.** Artifacts land in `artifacts/`:
- `replay_log_<target>.csv` — the predicted / acted / observed triple per event
- `summary_<target>.json` — KPIs plus run metadata

**4. Run the tests:**

```bash
pytest -q          # 12 tests pinning pipeline behavior
```

**5. See the operator view.** Open [artifacts/operator_view.html](artifacts/operator_view.html)
in a browser — the **Deployment Ledger** for 17 Mar 2024. It shows the model's
most striking real call: 10 of 12 officers sent to a late-night VIP convoy down
Mysore Road known days ahead, each recommendation auditable down to the five past
events behind it, and every assignment overridable by the duty engineer.

---

## Repo layout

```
.
├── README.md              ← you are here
├── DESIGN.md              ← full design doc: thesis, principles, roadmap, risks
├── RESULTS.md             ← the wedge proof + honest caveats
├── run_phase0.py          ← end-to-end runner (the entry point)
├── requirements.txt
├── astram_event_data_anonymized.csv   ← the real (anonymized) event log
├── gridlock/              ← the pipeline package (10 modules, see table above)
├── tests/                 ← pytest suite (12 tests)
└── artifacts/
    ├── operator_view.html ← the Deployment Ledger (operator-facing playbook)
    └── pitch.html         ← single-page visual story of the project
```

---

## Honest about the limits

A credible project names its weak spots. These are surfaced, not buried — Phase 0
exists to find them cheaply. Full detail in [RESULTS.md](RESULTS.md):

- The **+23.3% is a replay counterfactual**, not a measured field trial. A real
  connected-vehicle feed plus quasi-experimental evaluation is the honest next step.
- The source log has **no native speed/volume** data — delay-hours use a
  duration/synthetic proxy × exposure. The `TargetProvider` seam exists exactly so
  a real feed replaces the proxy cleanly.
- **Real event durations are heavily censored** (only a fraction resolve), so
  real-target MAE is large and honestly reported as a diagnostic.
- **Historical analogs add no point-prediction lift** here (the GBM already learns
  the same signal), so they're kept for explanation and uncertainty — their real job.
