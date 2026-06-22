"""Tests pinning the Phase 0 pipeline's critical invariants.

These are not accuracy tests (Phase 0 accuracy is data-dependent and reported as
a diagnostic). They pin the properties that, if broken, make results WRONG or
DISHONEST: no future leakage, correct censoring, blend bounds, allocation budget,
and lift directionality.

Most tests use a tiny synthetic frame (fast, deterministic). A few smoke tests
run against the real CSV when present.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from gridlock.analogs import retrieve_analogs
from gridlock.config import DEFAULT
from gridlock.features import build_features
from gridlock.ingest import load_events
from gridlock.lift import compute_lift
from gridlock.model import _analog_weight, train_gbm
from gridlock.playbook import _allocate_officers, generate_playbook
from gridlock.targets import get_target_provider


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _toy_events(n: int = 60) -> pd.DataFrame:
    """Deterministic toy event frame with the columns the pipeline needs."""
    base = pd.Timestamp("2024-01-01", tz="UTC")
    rng = np.random.default_rng(0)
    causes = ["construction", "public_event", "vehicle_breakdown", "accident"]
    rows = []
    for i in range(n):
        start = base + pd.Timedelta(hours=i * 5)
        resolved = start + pd.Timedelta(minutes=30 + (i % 7) * 10) if i % 3 else pd.NaT
        rows.append({
            "event_id": f"E{i:04d}",
            "event_cause": causes[i % len(causes)],
            "event_type": "planned" if causes[i % len(causes)] in {"construction", "public_event"} else "unplanned",
            "priority": "High" if i % 2 else "Low",
            "status": "closed" if i % 3 else "active",
            "requires_road_closure": bool(i % 4 == 0),
            "authenticated": True,
            "lat": 12.9 + rng.normal(0, 0.02),
            "lon": 77.6 + rng.normal(0, 0.02),
            "end_lat": np.nan, "end_lon": np.nan, "is_linear": False,
            "corridor": "Corridor A" if i % 2 else None,
            "zone": "Z1", "junction": None, "police_station": None,
            "veh_type": None, "cargo_material": None,
            "start_dt": start, "end_dt": pd.NaT,
            "resolved_dt": resolved, "closed_dt": resolved, "created_dt": start,
        })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def toy():
    df = _toy_events()
    feats = build_features(df, DEFAULT)
    return df, feats


# --------------------------------------------------------------------------- #
# ingestion / target invariants
# --------------------------------------------------------------------------- #
def test_duration_target_marks_censoring(toy):
    df, feats = toy
    tf = get_target_provider("duration").build(df, feats, DEFAULT)
    # rows with no resolution timestamp must be censored (observed == 0)
    no_res = df["resolved_dt"].isna() & df["closed_dt"].isna() & df["end_dt"].isna()
    assert (tf.observed[no_res] == 0).all()
    # observed durations are non-negative
    assert (tf.y[tf.observed == 1] >= 0).all()


def test_synthetic_target_fully_observed_and_bounded(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    assert (tf.observed == 1).all()
    assert (tf.y >= 0).all() and (tf.y <= 180.0).all()  # capped


def test_synthetic_target_reproducible(toy):
    df, feats = toy
    a = get_target_provider("synthetic").build(df, feats, DEFAULT).y
    b = get_target_provider("synthetic").build(df, feats, DEFAULT).y
    pd.testing.assert_series_equal(a, b)  # seeded => identical


# --------------------------------------------------------------------------- #
# analog invariants — the leakage guard
# --------------------------------------------------------------------------- #
def test_analogs_never_use_future_events(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    ar = retrieve_analogs(df, tf.y, tf.observed, DEFAULT)
    starts = df["start_dt"].to_numpy()
    id_to_start = dict(zip(df["event_id"], starts))
    for i, ids in enumerate(ar.analog_ids):
        for aid in ids:
            assert id_to_start[aid] < starts[i], "analog from the future — leakage!"


def test_first_event_has_no_analogs(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    ar = retrieve_analogs(df, tf.y, tf.observed, DEFAULT)
    earliest = df["start_dt"].idxmin()
    assert ar.support[earliest] == 0


# --------------------------------------------------------------------------- #
# blend invariants
# --------------------------------------------------------------------------- #
def test_analog_weight_within_cap(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    ar = retrieve_analogs(df, tf.y, tf.observed, DEFAULT)
    w = _analog_weight(ar, DEFAULT)
    assert (w >= 0).all() and (w <= DEFAULT.analog_weight_cap + 1e-9).all()
    # no-analog rows get zero weight
    assert (w[np.isnan(ar.estimate)] == 0).all()


def test_blend_predictions_nonnegative(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    train_mask = df["start_dt"] <= df["start_dt"].quantile(0.6)
    replay_mask = ~train_mask
    model = train_gbm(df, feats, tf, DEFAULT, train_mask=train_mask)
    pred = model.predict(df, feats, tf.y, tf.observed,
                         query_mask=replay_mask, history_mask=train_mask)
    assert (pred.blended >= 0).all()


# --------------------------------------------------------------------------- #
# allocation invariants — never overspend the officer pool
# --------------------------------------------------------------------------- #
def test_allocation_respects_pool():
    scores = np.array([10.0, 8.0, 6.0, 4.0, 2.0])
    for pool in (0, 1, 3, 7, 100):
        alloc = _allocate_officers(scores, pool)
        assert alloc.sum() <= pool
        assert (alloc >= 0).all()


def test_allocation_prioritises_high_scores():
    scores = np.array([1.0, 100.0, 2.0])
    alloc = _allocate_officers(scores, pool=2)
    assert alloc[1] >= alloc[0] and alloc[1] >= alloc[2]


def test_playbook_within_pool(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    train_mask = df["start_dt"] <= df["start_dt"].quantile(0.6)
    replay_mask = ~train_mask
    model = train_gbm(df, feats, tf, DEFAULT, train_mask=train_mask)
    pred = model.predict(df, feats, tf.y, tf.observed,
                         query_mask=replay_mask, history_mask=train_mask)
    pb = generate_playbook(df, feats, pred.blended, pred.analog_ids, DEFAULT,
                           event_mask=replay_mask, slice_label="t")
    assert pb.officers_used <= DEFAULT.officer_pool


# --------------------------------------------------------------------------- #
# lift invariants — system never worse than do-nothing
# --------------------------------------------------------------------------- #
def test_lift_system_beats_or_ties_nothing(toy):
    df, feats = toy
    tf = get_target_provider("synthetic").build(df, feats, DEFAULT)
    train_mask = df["start_dt"] <= df["start_dt"].quantile(0.6)
    replay_mask = ~train_mask
    model = train_gbm(df, feats, tf, DEFAULT, train_mask=train_mask)
    pred = model.predict(df, feats, tf.y, tf.observed,
                         query_mask=replay_mask, history_mask=train_mask)
    lr = compute_lift(df, feats, pred.blended, tf.y, tf.observed, DEFAULT,
                      replay_mask=replay_mask)
    # mitigation only reduces delay => system and uniform never exceed 'none'
    assert lr.veh_hours["system"] <= lr.veh_hours["none"] + 1e-6
    assert lr.veh_hours["uniform"] <= lr.veh_hours["none"] + 1e-6


# --------------------------------------------------------------------------- #
# smoke tests against the real CSV (skip if absent)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not DEFAULT.data_csv.exists(), reason="real CSV not present")
def test_real_csv_ingests():
    df, report = load_events(DEFAULT.data_csv)
    assert report.kept_rows > 8000
    assert report.planned_rows > 400
    assert df["start_dt"].notna().all()  # usable rows must have a start
