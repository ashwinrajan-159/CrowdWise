# Submission cheat-sheet — Prototype Round 2

Paste-ready content for each field of the submission form. Copy straight across.

---

## Title

```
CrowdWise — Event-Driven Congestion Management for Cities
```

(Alt, more descriptive: *"CrowdWise — turning a city's known event calendar into pre-positioned traffic playbooks"*)

---

## Description

```
Cities already know about many disruptive events — concerts, festivals, processions, VIP movements, and roadworks — days or weeks before they occur. Yet traffic management for these events stays largely experience-driven, leading to inconsistent deployments, avoidable congestion, and loss of institutional knowledge when veteran officers retire.

CrowdWise turns a city's known event calendar into coordinated, pre-positioned operational playbooks. For each upcoming event it forecasts the traffic impact, ranks the resulting chokepoints by predicted delay × population exposure, and produces an officer-deployment plan — every recommendation auditable down to the five most similar past events behind it, and every assignment overridable by the duty engineer.

It is deliberately NOT another real-time congestion predictor (a saturated space). The wedge is the PLANNED event, where the agency has two advantages no general traffic app does: foreknowledge (the calendar) and control of the response (officers, closures, signals) days in advance. The system is designed as a decision-support platform for traffic management centers — not an autonomous traffic-control system.

We validated the idea by historical replay on 8,173 real events from the Bengaluru Traffic Police "Astram" log (467 planned, 5 months). Because acting on a live prediction destroys your own ground truth (you can't tell whether the jam was wrong or your action prevented it), the only honest first validation is to replay un-intervened history. Result: the system reduces vehicle-hours of delay by +4.1% across all events — and by +23.3% on planned events specifically. ~6× more value exactly where the thesis says it should be.

Tech: Python, pandas, LightGBM (gradient-boosted forecast, chosen for native missing-value handling and explainability), a historical-analog engine for the auditable explanation surface, and a transparent delay×exposure allocation heuristic. 12 automated tests pin the pipeline. A forward-prediction tool (predict.py) takes any new calendar of upcoming events and generates the deployment dashboard for it.
```

---

## How it maps to the problem statement (paste into Description or pitch)

```
Problem statement: forecast event-related traffic impact and recommend optimal
manpower, barricading, and diversion plans, from historical + real-time data.

CrowdWise delivers, per the brief:
- Quantify impact in advance  -> per-event severity forecast (delay × exposure)
- Resource deployment          -> manpower recommendation per chokepoint by severity
- Barricading                  -> closure flag + barricade guidance per chokepoint
- Diversion                    -> corridor-aware reroute guidance per closure
- Post-event learning          -> retrain-as-history-grows loop (/api/retrain)
- Historical data              -> 8,173-event Astram log, trained model
- Real-time data               -> live PredictHQ event feed, scheduled refresh

Validated by historical replay: +23.3% less delay on planned events (vs +4.1% all).

Honest Phase-2 boundaries (documented, deliberate): live traffic-speed feed (vs.
event data) and turn-by-turn routing (vs. text diversion guidance) plug into
existing seams — deferred for evaluation integrity (the lost-ground-truth problem).
```

---

## Theme

> Pick the closest available option — likely **Smart Cities / Urban Mobility / Public-Sector / AI-for-Good**. (Depends on the list shown; choose mobility/smart-city if present.)

---

## Snapshots (upload)

Upload these three PNGs from the repo's `images/` folder:

1. `images/pitch.png` — the one-page pitch (hero + the +4.1% vs +23.3% proof bars)
2. `images/operator.png` — the operator Deployment Ledger with an expanded chokepoint (analogs + override)
3. `images/upcoming.png` — the dashboard generated for a NEW upcoming-events file (proves forward prediction)

---

## Video URL

> Record a 2–3 min screen capture (see `pitch-script` below). Paste the YouTube/Loom/Drive link.

---

## Presentation (upload deck)

> Build a short deck from the outline in the "Pitch deck outline" section below and export to PDF.

---

## Demo Link

Use the published, always-on interactive pages (no server needed — they work on click):

- **Pitch one-pager:** https://claude.ai/code/artifact/5a1b32e1-01ef-4a70-9abf-830f1bad65b8
- **Operator dashboard:** https://claude.ai/code/artifact/11cef353-1780-4f24-b67e-afcf7904a3cb

> Put the **pitch one-pager** in the Demo Link field — it opens with the result and links to the dashboard.

---

## Repository URL

```
https://github.com/ashwinrajan-159/CrowdWise
```

---

## Source Code (upload zip)

Upload `CrowdWise-source.zip` (built by the command in the "Build the zip" section — code + data + tests, no junk).

---

## Instructions to Run

```
PREREQUISITES
  Python 3.11+

SETUP
  pip install -r requirements.txt

1) VALIDATE THE THESIS ON REAL HISTORY (this is the +23.3% result)
  python run_phase0.py --planned-only
  # trains on 5,721 historical events, replays the planned events,
  # prints system-vs-do-nothing lift. Add --target both for full KPIs.

2) RUN THE FULL TEST SUITE
  pytest -q
  # 12 tests pinning pipeline behavior

3) FORWARD PREDICTION ON A NEW / UPCOMING EVENT FILE
  python predict.py upcoming_events.csv
  # trains on all history, forecasts the chokepoints for the new events,
  # and writes artifacts/operator_view_upcoming.html
  # (a sample upcoming_events.csv ships with the repo)

4) SEE THE OPERATOR DASHBOARD
  Open artifacts/operator_view.html          (the 17 Mar 2024 example)
  Open artifacts/operator_view_upcoming.html (generated in step 3)
  in any web browser. Click a chokepoint to see the past events behind
  the prediction; use the stepper to override the officer allocation.

NOTE ON ACCURACY NUMBERS
  Lift/accuracy is measured only on historical events (step 1) — future
  events have no observed outcome yet to score against. Step 3 shows the
  operational OUTPUT on unseen events; step 1 is the validation.
```

---

## Pitch deck outline (build → export PDF)

1. **Title** — CrowdWise: Event-Driven Congestion Management. One line: *"Cities already know what's coming. We turn that into a plan."*
2. **Problem** — Disruptive events are known days ahead, but managed ad hoc → inconsistent deployments, avoidable congestion, knowledge lost when officers retire.
3. **Insight / wedge** — Don't compete on real-time prediction (saturated). Win on PLANNED events: foreknowledge + control of the response.
4. **Solution** — Calendar → forecast → ranked chokepoints → officer playbook. Decision-support, human-in-the-loop, every call auditable.
5. **The hard problem we got right** — Lost-ground-truth: acting on predictions poisons your data, so we validate by replaying un-intervened history. (Judges love this — few teams address it.)
6. **Result** — +4.1% all events → **+23.3% planned events**. ~6× more value on the wedge. Real data: 8,173 Astram events.
7. **Demo** — screenshot of the Deployment Ledger; "10 of 12 officers to a VIP convoy known days ahead."
8. **Forward prediction** — show `predict.py` turning a new calendar into a fresh dashboard.
9. **Tech & honesty** — LightGBM + analogs, 12 tests; honest caveats (counterfactual not field trial; proxy until a real probe feed; TargetProvider seam ready).
10. **Roadmap** — Phase 0 (done) → shadow mode → staggered live rollout → digital twin.

---

## 2–3 minute video script

> **[0:00]** "Every city already knows when the next concert, procession, or VIP convoy is coming — it's on a calendar weeks ahead. But traffic for those events is still managed by gut feel."
>
> **[0:20]** "CrowdWise turns that calendar into a deployment plan." — show the pitch page, the +23.3% bars growing.
>
> **[0:45]** "Here's the operator dashboard for one real day." — open operator_view.html, click a chokepoint, show the five past events behind the call and the override stepper.
>
> **[1:20]** "It works on events it's never seen." — run `python predict.py upcoming_events.csv`, open the generated dashboard.
>
> **[1:50]** "The hard part isn't the model — it's honesty. Acting on a prediction destroys your ground truth, so we validate by replaying history that was never intervened on. On planned events, +23.3% less delay."
>
> **[2:20]** "Decision-support for traffic centers, validated on the past, deployable on the future. That's CrowdWise." — end on the repo URL.
```
