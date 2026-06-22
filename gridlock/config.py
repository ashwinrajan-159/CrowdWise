"""Central configuration. Location-agnostic by design — anything that would
hardcode a city (corridor names, exposure weights) is data-driven or
parameterized here, per the brief's location-agnostic assumption."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The anonymized Astram event log shipped alongside this build.
# Anonymized Astram event log. Repo ships the renamed file; the original long
# filename is honored as a fallback for local runs.
_CSV_CANDIDATES = [
    "astram_event_data_anonymized.csv",
    "Astram event data_anonymized - Astram event data_anonymizedb40ac87.csv",
]
DEFAULT_DATA_CSV = next((ROOT / c for c in _CSV_CANDIDATES if (ROOT / c).exists()), ROOT / _CSV_CANDIDATES[0])

ARTIFACTS_DIR = ROOT / "artifacts"


@dataclass(frozen=True)
class Config:
    data_csv: Path = DEFAULT_DATA_CSV
    artifacts_dir: Path = ARTIFACTS_DIR

    # --- Target selection (Phase 0 builds BOTH; "real first") ---------------
    # "duration" = real censored event-duration model (primary, real data only)
    # "synthetic" = synthetic per-corridor delay, enables full north-star KPI loop
    target: str = "duration"

    # --- Feature engineering knobs -----------------------------------------
    # concurrency window: other active events within this radius / time band
    concurrency_radius_km: float = 3.0
    concurrency_window_hours: float = 3.0

    # historical-analog retrieval
    analog_k: int = 5
    # minimum analogs before we trust the analog estimate over the GBM
    analog_min_support: int = 3
    # max weight the analog estimate can take in the blend.
    # FINDING (Phase 0, this dataset): analogs add NO point-prediction lift —
    # the GBM already learns from the same drivers (cause/hour/corridor), so any
    # positive weight slightly raises MAE (monotonic in the cap). We keep a small
    # cap so the blend mechanism is exercised and analogs remain the explanation
    # + uncertainty surface (their real job per the brief), at negligible cost.
    # A real probe-feed target with venue-specific structure may revisit this.
    analog_weight_cap: float = 0.10

    # --- Replay / split -----------------------------------------------------
    # chronological split: train on everything before this quantile of time,
    # replay on the rest (no leakage from future into past).
    train_time_quantile: float = 0.7
    random_seed: int = 42

    # --- Censoring ----------------------------------------------------------
    # durations above this (minutes) are treated as suspect/censored cap
    max_plausible_duration_min: float = 60 * 24 * 3  # 3 days

    # --- Playbook / allocation ---------------------------------------------
    # number of officers available to assign per replayed time-slice
    officer_pool: int = 12

    def __post_init__(self) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


DEFAULT = Config()
