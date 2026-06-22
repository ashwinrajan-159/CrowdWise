# Results — Does the wedge hold?

**Thesis:** a traffic-ops system built around the city's *known event calendar*
delivers most where the city has foreknowledge it controls — **planned events**.
Phase 0 tests that claim by historical replay on a real event log, with no live
action taken (so ground truth stays clean).

All numbers below are produced by `python run_phase0.py` and pinned by the test
suite. Nothing here is hand-tuned to look good.

---

## The data

Anonymized **Astram event log** (Bengaluru Traffic Police) — the agency's own
internal feed, which is exactly the privileged information advantage the thesis
rests on.

| | |
|---|---|
| Events (usable after validation) | **8,173** |
| Planned events (the wedge) | **467** — construction, public events, processions, VIP movement, protests |
| Window | 2023-11-09 → 2024-04-08 (~5 months) |
| Split | train on first 5,721 (chronological), replay the rest |

---

## The headline result

We measure the system's **lift** — how much it reduces vehicle-hours of delay
versus doing nothing — on two slices: *all events*, and *planned events only*.

| Slice | Events | System lift vs. do-nothing |
|---|---|---|
| All events | 2,452 | **+4.1%** |
| **Planned events only** | 96 | **+23.3%** |

**The system delivers ~6× more value on planned events than across all events.**

That is the wedge, quantified. With a fixed officer pool, the scarce resource
covers a far larger *fraction* of the smaller, concentrated planned set — so
targeting pays off most exactly where the thesis says it should. A general
congestion app, blind to the event calendar, cannot exploit this; the agency,
holding the calendar, can.

---

## Why this is the right thing to measure

Acting on a prediction destroys your own ground truth: if you reroute traffic
and the jam never appears, was the forecast wrong, or did your action prevent
it? A system that trains on its own interventions gets *worse* the more it's
trusted.

Phase 0 sidesteps this entirely by **replaying history with no live action** —
the cleanest possible ground truth. The lift figures use a transparent,
documented mitigation stand-in (officers reduce delay with diminishing returns,
stated effect size), computed on the synthetic-delay target. It is an honest
counterfactual, clearly bounded — not a claim of measured field impact. A real
probe/connected-vehicle feed drops into the same interface to make it real.

---

## What we are honest about

A pitch that hides its weak spots isn't credible. These are surfaced, not buried —
Phase 0 exists to find them cheaply.

- **Forecast accuracy is a *diagnostic*, not the goal.** Synthetic-target MAE is
  4.5 min; real-duration MAE is 316 min (the real data is heavily censored —
  only 868 of replayed events resolve — and long-tailed). Nobody in a control
  room cares about MAE; they care about clearance time and delay-hours. Our KPI
  hierarchy reports it accordingly.
- **Historical analogs add no point-prediction lift here.** The gradient-boosted
  model already learns from the same drivers (cause, hour, corridor). So analogs
  are kept for what they're actually good at — the *auditable explanation
  surface* ("here are the 5 past events behind this call") and an uncertainty
  signal — not as a predictor.
- **No native speed/volume in the source.** Delay-hours use a duration/synthetic
  proxy × population exposure. The `TargetProvider` seam exists precisely so a
  real feed replaces the proxy with zero downstream change.
- **Lift on a 96-event slice is a directional signal, not a field trial.** The
  +23% is a replay counterfactual. The honest next step is a real feed plus
  quasi-experimental intervention evaluation.

---

## See it in action

The system doesn't just score — it produces an operator-reviewable playbook.
The **Deployment Ledger** for 17 March 2024 shows the model's most striking real
call: **10 of 12 officers** land on a late-night VIP convoy down the Mysore Road
/ NICE corridor at 22:05 — a planned movement known days ahead — each
recommendation auditable down to the five past events behind it, and every
assignment overridable by the duty engineer.

➡️ `artifacts/operator_view.html` (open in a browser)

---

## Reproduce it

```bash
pip install -r requirements.txt
python run_phase0.py --target both        # KPIs on both targets
python run_phase0.py --planned-only        # the wedge result above
python run_phase0.py --walk-forward        # retrain each month as history grows
pytest -q                                  # 12 tests pin the invariants
```
