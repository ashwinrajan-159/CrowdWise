# Project Prompt — Planned-Event Traffic Operations System

This is a standing project brief for an AI assistant. It is written primarily to
generate the full design document, but works as a project brief too — to point it
at implementation instead, swap the final **"What to produce"** block for an
implementation-plan request.

The design principles below are **binding constraints**. Their job is to stop the
model from drifting back to the generic "real-time congestion predictor" version,
which is a saturated, commoditized space we are deliberately avoiding.

---

## The prompt

```
You are a systems architect and ML engineer helping me design a traffic
management system for a graduate-level / early-stage engineering project.
Produce a complete, rigorous design document for the system defined below.
Treat the design principles as binding constraints — do not revert to a
generic "real-time congestion prediction platform," which is a saturated,
commoditized space we are deliberately avoiding.

CORE IDEA (the one thing that must stay central)
A planning-and-coordination system that converts a city's KNOWN event
calendar (concerts, games, festivals, planned roadworks) into pre-positioned,
continuously-adjusted operational playbooks across the agencies a city
actually controls (traffic police, DOT, transit, event organizers, signage),
validated by a rigorous shadow-mode evaluation design, and improving with
each event cycle. The wedge is PLANNED events, where we hold an information
advantage no general traffic app exploits well. Unplanned incidents are a
secondary mode the same system degrades into — not the headline.

BINDING DESIGN PRINCIPLES
1. Three time horizons, structured plan -> pre-position -> adjust:
   - Days out: the staffing and closure plan.
   - Hours out: pre-positioning resources (dispatch takes real time; you
     cannot move officers in 20 minutes).
   - Minutes out: live adjustment. The minute-scale forecast is the CHEAPEST,
     LAST layer — not the product. Do not center the design on 15–60 min
     forecasts.
2. Model simplicity is a feature. Baseline = gradient-boosted models over
   good temporal/spatial/event features + historical-analog matching
   ("what happened the last 5 times this venue had a sold-out show on a rainy
   Friday?"). Only propose a Graph Neural Network where you can show it beats
   the baseline AND sensor density justifies it. Operability and operator
   trust beat a couple points of MAE. Do not oversell GNNs as the goal.
3. Evaluation is CENTRAL, not an afterthought. Acting on a prediction
   destroys your own ground truth (if you reroute and the jam doesn't appear,
   was the forecast wrong or did the action prevent it?) and contaminates the
   training set. Design for this from day one: advisory/shadow mode on a
   randomly held-out subset of events, comparable events/venues as synthetic
   controls, staggered rollout, and explicit logging of the triple
   (predicted / acted / observed) for every event.
4. Causal inference is for ATTRIBUTION and INTERVENTION EVALUATION
   (quasi-experimental methods), NOT forecasting. Two jobs: attribute
   congestion to the event vs. background traffic, and measure which
   interventions actually worked (did closing that ramp help, or would delays
   have eased anyway?). Do not list it as a forecasting model class.
5. Optimization layer stays small and explainable. The real combinatorial
   space is tiny (a handful of officers, known chokepoints). Use ONE
   transparent scoring heuristic (rank chokepoints by predicted delay ×
   population exposure; assign top resources; operator can override) with a
   human in the loop. Mention decision-focused learning / predict-and-optimize
   as the sophisticated option worth one paragraph — but no genetic-algorithm
   or simulation-optimization zoo.
6. Digital twin (VISSIM/Aimsun/SUMO microsimulation) is an OPTIONAL,
   later-phase capability for high-stakes scenario testing at one venue — not
   a pipeline default. Early validation is historical replay, which is far
   cheaper.
7. The human/organizational layer is first-class. The real competitor is the
   experienced traffic engineer who already has a good playbook in their head.
   The system must beat THAT, not "no plan." Frame the system as capturing,
   systematizing, and incrementally improving expert playbooks event over
   event — coordination + institutional memory that happens to use ML.
8. KPI hierarchy, not a flat list. Pick 1–2 operational north-star outcomes
   (e.g., reduction in network-clearance time after the venue empties, and
   reduction in vehicle-hours of delay on affected corridors). Make explicit
   that prediction accuracy (MAE/RMSE) is a DIAGNOSTIC, not a goal — nobody in
   a traffic management center cares about RMSE.
9. Data prioritization: official sensors/feeds and connected-vehicle/probe
   data are HIGH priority; weather, transit disruption, roadwork schedules,
   official incident logs are MEDIUM; social media (Twitter/X) is LOW and
   dated given current API cost/restrictions — prefer Waze and connected-
   vehicle signals for the same job.
10. Keep a streaming architecture (Kafka/Flink-style) for the live-adjustment
    layer only. Do not let "event-driven architecture" imply that real-time
    is the whole product. Keep "special events" and "event-driven
    architecture" conceptually separate.

ASSUMPTIONS
Location-agnostic design (parameterized for road-network size, sensor
density, typical patterns). State any such assumptions explicitly rather than
hardcoding a city.

WHAT TO PRODUCE
A structured design document with these sections:
  - Redefined problem statement and the planned-event wedge (why this, why now)
  - Objectives and KPI hierarchy (north-stars vs. diagnostics)
  - Data sources, schemas, and prioritization
  - Feature engineering (incl. cyclical time encoding, event flags,
    spatial-distance-to-event, historical-analog features)
  - Modeling approach (baseline-first; when/whether a GNN earns its place;
    the role of causal inference)
  - The three-horizon operational flow (plan -> pre-position -> adjust)
  - Resource-allocation logic (the explainable heuristic + human-in-loop;
    one paragraph on decision-focused learning)
  - Real-time architecture for the live layer
  - Evaluation design (the predict-then-act / lost-ground-truth problem and
    how shadow mode + synthetic controls + staggered rollout solve it)
  - Post-event learning loop and the predicted/acted/observed log
  - Phased rollout, roles, and tiered cost scenarios
  - Risks and mitigations (with adoption/organizational risk treated as core,
    not buried)

STYLE
Be concrete and decision-oriented, not a survey of everything possible.
Where you make a design choice, briefly justify it and name the trade-off.
Prefer the simplest design that meets the need; flag where sophistication is
genuinely warranted. Use prose with tables only where a comparison earns one.
```

---

## Notes on using it

- **To build instead of document:** replace the *What to produce* block with a request for an implementation plan, data-pipeline scaffold, or specific components.
- **Tight context window:** principles 6 and 10 are the most trimmable — drop those first.
- **One-line summary of the core idea** (useful as a title or pitch opener): a planning-and-coordination system that converts a city's known event calendar into pre-positioned, continuously-adjusted operational playbooks across the agencies it controls, validated by a rigorous shadow-mode evaluation design, and improving with each event cycle.
