# Planned-Event Traffic Operations System — Design Document

*A planning-and-coordination system that converts a city's known event calendar into pre-positioned, continuously-adjusted operational playbooks across the agencies it controls — validated by a rigorous shadow-mode evaluation design, and improving with each event cycle.*

This document is grounded against a real operational dataset: the anonymized **Astram event log** (Bengaluru Traffic Police), 8,173 events over 2023-11-09 → 2024-04-08, including 467 *planned* events (construction, public events, processions, VIP movements, protests) with location, corridor, zone, closure flags, and resolution timestamps. Where the design makes a claim about feasibility, it is checked against what this log actually contains.

---

## 1. Redefined problem statement and the planned-event wedge

### What we are *not* building
We are deliberately not building a real-time congestion-prediction platform. That space is saturated: Google, TomTom, Waze, HERE, and a dozen startups all forecast 15–60-minute network speeds from probe data. A graduate-scale team cannot out-sensor or out-data them, and even if it matched their MAE, it would have built a worse copy of a free product. Worse, a pure forecaster produces *information a control room cannot act on* — knowing a jam is coming in 20 minutes is useless when dispatching an officer takes 40.

### The wedge: planned events
The defensible position is **planned events**, where the city holds an information advantage no general traffic app exploits well. A concert promoter files for a permit weeks out. A roadwork closure is scheduled. A religious procession has a fixed route and a fixed date every year. The traffic-management agency *knows these are coming* — and it controls the levers that matter for them: officer deployment, signal timing, lane/ramp closures, transit augmentation, variable signage, and coordination with the organizer.

A general traffic app sees a planned event only as anomalous congestion *after it starts*. The agency can pre-position for it *days ahead*. That asymmetry — privileged forward knowledge of demand shocks plus control of the supply-side response — is the entire thesis.

In the Astram data this wedge is concrete and recurring: **construction (311), public events (84), processions (38), VIP movement (20), protests (8)** — 461 planned demand-shocks in five months, ~3–4 per active week, in a single city. 169 of the 467 planned events required road closures. These are exactly the events where a playbook, prepared in advance, beats a live forecast.

### Why now
Three things have matured simultaneously: (1) cities have digitized their event/permit calendars and incident logs (Astram itself is proof — a structured, geocoded, agency-internal event feed already exists); (2) connected-vehicle and probe data (Waze CCP, HERE, commercial CV feeds) give corridor-level ground truth without a city installing its own sensor grid; (3) gradient-boosted models over good features are commodity, reliable, and explainable enough for an operations center to trust. The missing piece was never the forecast — it was the **coordination layer that turns foreknowledge into a pre-positioned, accountable plan and learns from each event**. That is what this system is.

### The honest framing of the competitor
The real incumbent is not "no plan." It is the **experienced traffic engineer** who already keeps a good playbook in their head: *"Stadium sold out on a Friday? Put two officers at the south ramp, close the inner lane at 6, hold the transit buses an extra 20 minutes."* That person is good. The system's job is to **capture, systematize, and incrementally improve** that expertise — make it survive staff turnover, scale across venues, and get measurably better every event cycle — not to replace judgment with a model.

### Unplanned incidents: a degraded mode, not the headline
The same dataset shows the volume reality: 7,706 of 8,173 events are *unplanned* (vehicle breakdowns dominate at 4,896). The system handles these — but as a **degraded mode** of the same pipeline. An unplanned incident is just a planned event with zero lead time: it skips the "days-out" and "hours-out" horizons and enters directly at the "minutes-out" live-adjustment layer, reusing the same chokepoint scoring and resource-assignment logic. We do not center the product on incidents; we let the planned-event machinery fall back to them gracefully.

---

## 2. Objectives and KPI hierarchy

The single most important design decision in this section: **prediction accuracy is a diagnostic, not a goal.** Nobody in a traffic-management center has ever cared about RMSE. They care about how fast the network clears and how much delay people ate. The KPI hierarchy enforces that.

### North-star operational outcomes (1–2, and only 1–2)
1. **Network-clearance time after venue empties** — minutes from event end until affected corridors return to within X% of their typical speed for that time-of-day/day-of-week. This is the cleanest signal that coordination worked.
2. **Vehicle-hours of delay on affected corridors** — integrated excess travel time over background, summed across the affected sub-network during the event window. This is the headline harm the system exists to reduce.

These are the only two numbers reported to leadership. Everything else explains *why* they moved.

### Operational/process KPIs (the second tier — these the ops team watches)
- **Pre-position lead time**: minutes between resource-assignment recommendation and event onset (target: officers in place *before* demand arrives, not after).
- **Plan adherence**: fraction of recommended actions actually executed (a low number is an *adoption* signal, not a model signal — see §12).
- **Override rate and override outcome**: how often operators override, and whether overrides did better or worse than the recommendation (this is how the system earns trust and how the playbook improves).
- **Clearance of the chokepoint queue**: time for the top-ranked chokepoint to drain.

### Diagnostics (third tier — for the ML team only, never reported as success)
- Forecast MAE/RMSE on delay and clearance, by horizon.
- Calibration of the historical-analog match (did the "last 5 similar events" actually resemble this one?).
- Attribution residuals (event-caused vs. background congestion — see §5).

A model that improves MAE but does not move clearance time or delay-hours has *failed*. The hierarchy makes that judgment automatic.

---

## 3. Data sources, schemas, and prioritization

### Prioritization (binding)
- **HIGH** — official agency sensors/feeds and connected-vehicle/probe data. This is ground truth for the north-star KPIs. In our grounding set, the Astram event log itself is the privileged HIGH-value internal feed: structured, geocoded, agency-authored, with closure flags and resolution times.
- **MEDIUM** — weather, transit-disruption feeds, roadwork schedules, official incident logs, the event/permit calendar. These are strong features and strong context but are not the speed ground truth.
- **LOW** — social media (Twitter/X). Dated and expensive given current API restrictions; **prefer Waze and connected-vehicle signals for the same job** (crowd-sourced incident reports, jam alerts). We do not build a social-listening pipeline.

### Core schemas

**Event calendar / event log** (grounded in the Astram schema — 46 fields; the operationally useful subset):

| Field | Type | Notes |
|---|---|---|
| `event_id` | string | stable key |
| `event_type` | enum | `planned` \| `unplanned` — the wedge selector |
| `event_cause` | enum | construction, public_event, procession, vip_movement, protest, accident, vehicle_breakdown, water_logging, … |
| `latitude`, `longitude` | float | point geometry |
| `endlatitude`, `endlongitude` | float | for linear events (closures, processions) — often 0 when point-only |
| `address`, `end_address` | string | human-readable |
| `corridor` | enum | named corridor or `Non-corridor` (Mysore Rd, Bellary Rd, Tumkur Rd, Hosur Rd, ORR segments…) |
| `zone`, `junction` | string | administrative geography |
| `requires_road_closure` | bool | direct supply-side flag |
| `start_datetime`, `end_datetime` | timestamp | planned window (note: `end_datetime` frequently NULL in source — see gap below) |
| `resolved_datetime`, `closed_datetime` | timestamp | actual resolution — enables duration ground truth |
| `priority` | enum | High \| Low (operator-assigned) |
| `status` | enum | active \| resolved \| closed |
| `veh_type`, `cargo_material` | enum | relevant for breakdown/heavy-vehicle incidents |
| `assigned_to_police_id`, `police_station` | string | the resource/coordination link |
| `route_path`, `map_file` | geom/blob | for linear/route events |

**Probe/CV speed snapshot** (the HIGH-value external feed we join in):

| Field | Type |
|---|---|
| `segment_id` | string (matched to corridor/road-segment graph) |
| `timestamp` | timestamp (e.g., 1–5 min cadence) |
| `mean_speed`, `free_flow_speed` | float |
| `occupancy` / `jam_factor` | float |
| `sample_count` | int (confidence) |

**Resource/roster** (what the agency controls): officer availability by shift and station, signal-controller inventory at junctions, transit-augmentation capacity, signage assets.

### Known data gaps (state them; do not paper over them)
- **`end_datetime` is largely NULL** in the source log; resolution timestamps exist for only a fraction (≈74 rows had both start and resolved in the sample). **Implication:** event-*duration* ground truth is sparse and must be reconstructed from `status`/`resolved_datetime` transitions, or treated as right-censored. Survival/duration modeling must handle censoring explicitly rather than dropping NULLs.
- **`direction` is ~99% NULL** — effectively unusable as a feature; do not engineer around it.
- **Free-text contamination**: a handful of `event_type`/`veh_type` cells contain operator free-text (Kannada notes, descriptions) bleeding from adjacent fields. Ingestion needs a validation/quarantine step, not blind trust.
- **No native speed/volume** in the event log — congestion ground truth *must* come from the external probe/CV join. The event log tells you *what was scheduled and what the agency did*; the probe feed tells you *what traffic did*. Neither alone is enough.

---

## 4. Feature engineering

Features are organized by the three principles that matter for this problem: time is cyclical, events have a spatial footprint, and the best predictor of the next event is the last few similar ones.

### Cyclical time encoding
Hour-of-day, day-of-week, and day-of-year encoded as `sin/cos` pairs (so 23:00 and 00:00 are neighbors, December and January are neighbors). Plus categorical flags: is_weekend, is_public_holiday, is_festival_day (India-specific calendar — directly relevant given the procession/festival events in the data), school-term flag.

### Event flags and attributes
- `event_cause` one-hot, `requires_road_closure`, `priority`, expected_attendance (joined from permit data where available; bucketed otherwise).
- Event *footprint*: point vs. linear (derived from whether `endlat/lng` are populated and `route_path` exists). Processions and closures are linear and need corridor-segment expansion.
- Concurrency: count of other planned/active events within R km and ±T hours (multiple simultaneous events is where playbooks break — and the data has clustered days).

### Spatial / distance-to-event
- Distance from each candidate road segment to the event geometry (point distance, or distance-to-line for linear events).
- Corridor membership and graph-hop distance from the event's corridor to each affected corridor (using the road-segment graph; the named-corridor field gives a ready-made coarse partition: Mysore Rd, Bellary Rd 1/2, Tumkur Rd, Hosur Rd, ORR segments, Old Madras Rd).
- Upstream/downstream position relative to the event (a closure hurts upstream approaches, not downstream).
- Zone/junction context for administrative roll-ups.

### Historical-analog features (the heart of the baseline)
For a given upcoming event, retrieve the **k most similar past events** by (venue/location, cause, weekday, time-of-day, weather band, attendance band, closure flag) and compute, from their realized outcomes:
- analog mean/median peak delay and clearance time,
- analog spread (a wide spread = low confidence = flag for operator attention),
- analog "what worked" — which interventions were applied in those analogs and how the outcome differed.

This is literally the engineer's mental model — *"what happened the last 5 times this venue had a sold-out show on a rainy Friday?"* — turned into retrievable features. It is also the most explainable feature class: the operator can see the five analog events the recommendation is built on.

### Exogenous context
Weather (rain band — directly relevant given water-logging is a top cause), concurrent transit disruptions, recent background-traffic level on the corridor (so the model separates event effect from an already-bad day).

---

## 5. Modeling approach

**Model simplicity is a feature.** The ordering below is deliberate: ship the baseline, prove the north-star KPIs move, and only add complexity where it earns its place against that baseline.

### Baseline (this is the product, not the warm-up)
- **Gradient-boosted models** (LightGBM/XGBoost) over the temporal/spatial/event features above, predicting per-corridor: peak delay, time-to-peak, and clearance time. Robust to mixed feature types, handles missingness natively (important given the NULL-heavy source), trains in seconds, and gives feature attributions operators can read.
- **Historical-analog matching** running alongside, providing both a prediction (analog-weighted outcome) and the *explanation surface* (the retrieved comparable events). When analogs are dense and tight, the analog estimate is trusted heavily; when sparse, the GBM carries more weight, and the system surfaces "low historical support — operator judgment needed."

This pairing — GBM for generalization, analog retrieval for trust and explanation — is the whole modeling story for v1. It is operable, debuggable, and a control room can believe it.

### When (and whether) a GNN earns its place
A Graph Neural Network over the road network is proposed **only as a conditional upgrade**, gated on two tests, both of which must pass:
1. It **beats the GBM baseline** on the diagnostic forecast metrics *and*, more importantly, on a downstream KPI proxy, on held-out events.
2. **Sensor/probe density justifies it** — GNNs exploit spatial propagation structure, which only helps when segment-level data is dense enough to learn it. On a sparse corridor-only graph (which the named-corridor partition in our data resembles), there is nothing for the message-passing to learn that distance features don't already capture.

Until both hold, a GNN adds operational risk (harder to debug, harder to explain a recommendation built on it, heavier to serve) for a couple points of MAE that the KPI hierarchy says don't matter. **Operability and operator trust beat MAE.** We will not present a GNN as the goal.

### The role of causal inference (NOT a forecasting class)
Causal inference does **two jobs, neither of them forecasting**:
1. **Attribution** — decompose observed congestion into *event-caused* vs. *background*. A jam on a rainy Friday with a concert is partly the concert and partly the rain and partly that the corridor is always bad at 6pm. Without this split you cannot judge whether your plan helped.
2. **Intervention evaluation** — measure which actions *actually worked*. Did closing that ramp help, or would delays have eased anyway? This uses quasi-experimental methods (difference-in-differences against comparable control events/venues, synthetic controls, regression discontinuity around closure timing).

Causal methods never appear in the "list of forecasting models." They appear in evaluation and the learning loop (§9, §10).

---

## 6. The three-horizon operational flow

The system is structured as **plan → pre-position → adjust**, and the order reflects both lead time and value. The minute-scale forecast is the *cheapest, last* layer — not the product.

### Days out — the staffing and closure plan
Input: the event calendar (permits, scheduled roadworks, known processions/festivals). Output: a **draft playbook** per event — projected affected corridors, recommended officer count and stations, proposed closures/lane-reductions with timing, transit-augmentation asks, signage plan. Built from historical analogs + the GBM corridor forecast + the closure flag. This is where the system delivers most of its value, because this is the horizon where the agency can actually arrange staffing, file closure notices, and coordinate with the organizer. A plan produced here, reviewed by the duty engineer, *is* the deliverable.

### Hours out — pre-positioning resources
Input: refreshed forecast with near-term weather, confirmed attendance, any concurrent events that materialized. Output: **dispatch instructions** — move officers to assigned chokepoints, pre-stage tow/recovery for high-breakdown corridors, set signal plans, activate signage. The binding reality: **dispatch takes real time; you cannot move officers in 20 minutes.** This horizon exists precisely because the minute-scale layer is too late to position people. Getting resources to the right place *before* demand arrives is the operational win the pre-position lead-time KPI measures.

### Minutes out — live adjustment
Input: live probe/CV speeds, live incident reports (Waze/CV), execution status of the plan. Output: small, reversible tweaks — hold a signal phase longer, open a contraflow lane, redirect the on-scene officer to the chokepoint that's actually forming. This is a streaming layer (§8). It is the *cheapest and last* layer: it fine-tunes a plan that already exists. If the days-out and hours-out work was done, the minutes-out layer has little to do; if it wasn't, no minute-scale forecast can save the event. **We do not center the design here.**

---

## 7. Resource-allocation logic

**The combinatorial space is tiny** — a handful of officers, a known set of chokepoints, a few closure decisions. It does not need an optimization zoo. It needs one transparent heuristic and a human who can override.

### The heuristic (v1, and probably v2 and v3)
For each known chokepoint in the event's affected sub-network, compute a **priority score**:

```
score(chokepoint) = predicted_delay × population_exposure
```

where `predicted_delay` comes from the §5 model and `population_exposure` is a static weight per chokepoint (traffic volume / vulnerable-route flag — e.g., is it on a hospital or emergency route). Rank chokepoints by score; assign the top resources (officers, signal control, signage) down the list until resources are exhausted. **The operator sees the ranked list with the score components and can override any assignment.** Overrides are logged and feed the learning loop (§10).

This is explainable to the minute: *"We put an officer here because this junction has the highest predicted delay × exposure, here are the five analog events behind that prediction."* An operator can trust, audit, and correct it. That is worth more than an opaque optimum.

### The one paragraph on sophistication
**Decision-focused learning / predict-and-optimize** is the genuinely sophisticated upgrade worth naming: instead of training the forecaster to minimize prediction error and then optimizing separately, train the model end-to-end to minimize the *decision regret* of the downstream allocation — so the model spends its accuracy where allocation decisions are sensitive to it. It is the right long-term direction and a paragraph of intent. It is **not** v1: it needs a stable allocation objective and enough event cycles to estimate regret, both of which come only after the heuristic has been running. No genetic algorithms, no simulation-optimization menagerie.

---

## 8. Real-time architecture for the live layer

A streaming architecture (Kafka/Flink-style) exists **only for the live-adjustment layer.** "Event-driven architecture" here is a plumbing choice for the minutes-out tier — it must not be read as "real-time is the whole product." Keep *special events* (the domain) and *event-driven architecture* (the plumbing) conceptually separate; they are unrelated uses of the word "event."

```
                          ┌─────────────────────────────────────────────┐
   DAYS / HOURS (batch)    │  Event calendar ─┐                          │
   ─────────────────────   │  Permits/roadwork ├─► Feature store ─► GBM + │
   (Airflow / cron)        │  Historical log ─┘   (offline)        analog │
                           │                                        │      │
                           │                          Playbook generator ─┼──► Plan UI
                           └────────────────────────────────────────┼─────┘   (operator
                                                                     │          review +
                                                                     ▼          override)
   MINUTES (streaming)     ┌─────────────────────────────────────────────┐
   ─────────────────────   │  Probe/CV speeds ─┐                          │
   (Kafka → Flink)         │  Waze/CV incidents ├─► stream join with the  │
                           │  Execution status ─┘   active playbook        │
                           │            │                                  │
                           │            ▼                                  │
                           │   Live chokepoint re-score (§7 heuristic) ────┼──► Live tweak
                           │            │                                  │   suggestions
                           │            ▼                                  │   (operator
                           │   (predicted / acted / observed) logger ──────┼──► to KPI +
                           └────────────────────────────────────────┼─────┘   learning loop
                                                                     ▼
                                                          Evaluation / training store
```

Key points:
- The batch tier (days/hours) does the heavy lifting and runs on a scheduler, not a stream. It writes playbooks to a store the UI reads.
- The streaming tier consumes probe speeds, crowd incident reports, and plan-execution status; it re-scores chokepoints with the *same* §7 heuristic and proposes small adjustments. It does not retrain models live.
- Every live decision writes the **(predicted / acted / observed)** triple (§10) — the streaming layer is also the instrumentation layer.
- Serving the GBM + analog retrieval is lightweight (sub-second), so the live tier stays simple. No GNN-serving infrastructure unless §5's gates are passed.

---

## 9. Evaluation design — the central problem

This is the section that most distinguishes a serious design from a demo. **Evaluation is not an afterthought; it is designed from day one** because of a problem unique to acting on predictions.

### The predict-then-act / lost-ground-truth problem
If the system predicts a jam, the operator reroutes traffic, and the jam doesn't appear — **was the forecast wrong, or did the action prevent it?** You cannot tell. Worse, you just logged "predicted jam, observed no jam" as a training example, teaching the model that this situation is *fine* — when in fact it was only fine *because you intervened*. Acting on a prediction **destroys your own ground truth and contaminates your training set.** A naive system gets *worse* the more it's used and the more it's trusted.

### The solution: shadow mode + synthetic controls + staggered rollout

**1. Advisory/shadow mode by default.** The system produces playbooks and live suggestions but, for evaluation purposes, a **randomly held-out subset of events is run in pure shadow mode**: the recommendation is logged but the operator runs their own plan (or a no-system baseline). On shadow events we observe the *un-intervened-by-the-system* outcome, giving uncontaminated ground truth for the forecast and a clean baseline for "what the experienced engineer does without us." Randomization of which events are shadow vs. live is the experimental backbone.

**2. Comparable events/venues as synthetic controls.** For a live (system-acted) event, construct a synthetic control from *comparable* past/parallel events — same venue class, cause, weekday, attendance band — that did not get the system's intervention. The difference between the acted event and its synthetic control estimates the system's causal effect, and is how we attribute outcome changes to the system rather than to weather or a quiet week (ties directly to §5's causal-inference jobs).

**3. Staggered rollout.** Introduce the system across venues/zones in a staggered schedule rather than everywhere at once. The not-yet-onboarded venues are time-varying controls; difference-in-differences across the rollout schedule gives a defensible effect estimate and de-risks adoption (§12).

**4. Explicit logging of (predicted / acted / observed) for every event** — see §10. This triple is the atomic unit of evaluation and learning.

The honesty here is the point: we *design around* the fact that we can't naively measure ourselves, instead of pretending the contamination doesn't exist.

---

## 10. Post-event learning loop and the predicted/acted/observed log

The system improves **each event cycle** by closing the loop with a single disciplined data structure.

### The (predicted / acted / observed) log
For every event — planned or unplanned, shadow or live — the system records the triple:

| Component | Contents |
|---|---|
| **Predicted** | the forecast (per-corridor delay/clearance), the analogs it was built on, the recommended playbook, model version, feature snapshot |
| **Acted** | what was actually executed — officers placed, closures applied, signal plans, *and operator overrides* with reason codes |
| **Observed** | realized speeds/delays/clearance from the probe feed, north-star KPI values, whether the event was shadow or live |

### The learning loop, per event cycle
1. **Attribution** (§5 causal job 1): split observed congestion into event-caused vs. background, so outcomes are comparable across events with different baselines.
2. **Intervention evaluation** (§5 causal job 2): against the synthetic control / shadow baseline, estimate the effect of *each acted intervention*. Closing that ramp — did it help, or would delay have eased anyway?
3. **Playbook update**: interventions that demonstrably helped (and overrides that beat the recommendation) are promoted into the venue's playbook as new analogs / adjusted defaults. Interventions that didn't help are demoted. This is the mechanism by which the system **captures and systematizes expert judgment** — every operator override that worked becomes institutional memory.
4. **Model refresh** on *uncontaminated* examples (shadow events + properly weighted live events), guarding against the predict-then-act contamination by never training the forecaster to treat a prevented jam as a non-event.

The loop is what makes the product compound: a one-off forecaster is static; a coordination system with a clean (predicted/acted/observed) log gets measurably better at *this city's* events every cycle, and the improvement is auditable.

---

## 11. Phased rollout, roles, and tiered cost scenarios

### Phases
- **Phase 0 — Historical replay (no live integration).** Ingest the event log + a historical probe feed; build features; train the GBM + analog baseline; *replay* past events and measure how well predicted playbooks would have done. Cheapest possible validation — this is where the 467 planned events in the Astram-style log earn their keep before a single live integration. No streaming, no digital twin.
- **Phase 1 — Shadow mode at a few venues.** Live data in, recommendations logged, operators run their own plans. Establishes the clean baseline and the (predicted/acted/observed) log. Staggered onboarding begins.
- **Phase 2 — Advisory live at onboarded venues.** Operators act on recommendations with override; synthetic-control + staggered-rollout evaluation runs continuously. North-star KPIs reported.
- **Phase 3 — Learning loop matures + selective sophistication.** Playbook auto-updates from the loop; evaluate (against the baseline and KPIs) whether a GNN (§5 gates) or decision-focused learning (§7) earns its place. Microsimulation digital twin considered for *one* high-stakes venue only (§optional, below).

### Roles
- **Duty traffic engineer / operator** — reviews and overrides playbooks; the human in the loop and the source of the expertise the system captures.
- **Operations lead** — owns the north-star KPIs and the rollout schedule.
- **Data/ML engineer** — owns the feature store, models, and the integrity of the (predicted/acted/observed) log and evaluation.
- **Agency liaisons** — transit, DOT, event organizers, police dispatch — the coordination endpoints the playbook reaches.

### Tiered cost scenarios
- **Lean (graduate-project / pilot):** Phase 0–1 only. Batch on a single VM, Postgres + a feature store, GBM + analog, one Kafka topic for a single venue's live feed, off-the-shelf probe data trial. Minimal cost; proves the wedge.
- **Standard (single-city deployment):** Phases 0–2. Managed Kafka/Flink for the live tier, a proper feature store, dashboarding for operators, integration with the agency's dispatch system, paid probe/CV feed. The realistic operating point.
- **Advanced (multi-city / high-stakes):** Phase 3 — selective GNN where density justifies, decision-focused learning, and a microsimulation digital twin for the one or two venues where a bad call is very expensive. Highest cost; only justified once the KPI gains from Standard are proven.

### Optional, later-phase: the digital twin
Microsimulation (VISSIM/Aimsun/SUMO) is an **optional, high-stakes scenario-testing capability for one venue** — *not* a pipeline default. Building and calibrating a microsim is expensive and slow; **historical replay (Phase 0) is far cheaper and is the early-validation method.** Reserve the twin for testing closure scenarios at a single venue where the cost of getting it wrong justifies the calibration effort.

---

## 12. Risks and mitigations

**Adoption / organizational risk is treated as core, not buried**, because it is the most likely cause of failure.

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Operators don't trust or use it** (the #1 risk) | The real competitor is the experienced engineer with a playbook in their head; an opaque tool gets ignored. | Explainable-by-construction (analog events shown, score components shown); human-in-the-loop with easy override; frame as *capturing their* expertise, not replacing it; track override-outcome to show the system learns from them. Plan-adherence KPI surfaces non-adoption early. |
| **Lost-ground-truth contamination** | Acting on predictions destroys ground truth and poisons training. | Shadow mode on randomized held-out events; synthetic controls; staggered rollout; never train a prevented jam as a non-event (§9, §10). |
| **Data gaps** (`end_datetime`/duration NULL, no native speed, field contamination) | Sparse, censored, and dirty source data breaks naive pipelines. | Treat durations as right-censored (survival modeling); join external probe feed for speed ground truth; ingestion validation/quarantine; native-missingness GBM. (All observed directly in the grounding dataset.) |
| **Over-engineering** (GNN/optimization zoo/early digital twin) | Complexity that doesn't move the north-star KPIs adds risk and erodes trust. | Baseline-first; GNN gated on beating baseline *and* density; one explainable allocation heuristic; digital twin deferred to one high-stakes venue; KPI hierarchy auto-rejects accuracy gains that don't move outcomes. |
| **Concurrent events / playbook collisions** | Multiple simultaneous events (present in the data) are where head-held playbooks fail. | Concurrency features (§4); the allocation heuristic ranks across *all* active chokepoints citywide, not per-event in isolation. |
| **Probe-data cost / coverage** | HIGH-value feed is a paid external dependency with coverage gaps. | Start with a trial on the lean tier; corridor-level (not segment-level) granularity is enough for v1; degrade gracefully where coverage is thin (wider analog reliance, flagged low-confidence). |
| **Equity blind spots** | Optimizing aggregate delay can quietly worsen outcomes for low-volume / vulnerable routes. | `population_exposure` weight in the heuristic includes vulnerable-route flags (hospital/emergency corridors); report KPIs broken out by route class, not just aggregate. |

---

## Appendix — design choices and their trade-offs (quick reference)

- **Planned-event wedge over general congestion prediction** — defensible information advantage; trade-off: smaller addressable surface than a generic app (accepted — that surface is saturated and we'd lose it).
- **GBM + analog baseline over GNN** — operable, explainable, trusted; trade-off: a few points of MAE we don't care about (KPI hierarchy makes that explicit).
- **One scoring heuristic over an optimizer** — auditable, overridable; trade-off: leaves theoretical optimality on the table (accepted — the space is tiny and trust matters more).
- **Shadow mode + synthetic controls** — clean ground truth and causal effect estimates; trade-off: slower to show "live" wins and some events deliberately un-helped (accepted — the alternative is measuring nothing real).
- **Streaming for the live tier only** — keeps the system honest about where real-time belongs; trade-off: two execution models (batch + stream) to maintain (accepted — they serve genuinely different horizons).
- **Digital twin deferred** — historical replay validates far more cheaply; trade-off: no microsim scenario-testing until Phase 3 at one venue (accepted).
