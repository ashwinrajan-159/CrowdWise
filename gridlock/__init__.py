"""Planned-Event Traffic Operations System — Phase 0 (historical replay).

A planning-and-coordination system that converts a city's known event calendar
into pre-positioned operational playbooks, validated by historical replay.

Phase 0 ingests the agency event log, engineers features, trains a
gradient-boosted + historical-analog baseline, and replays past events to
measure how the generated playbooks would have performed. No live integration,
no streaming, no digital twin — the cheapest possible validation of the wedge.
"""

__version__ = "0.1.0"
