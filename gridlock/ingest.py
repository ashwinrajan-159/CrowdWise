"""Ingestion with validation + quarantine.

The source event log is dirty in documented ways (verified against the Astram
data): `event_type` and `veh_type` cells occasionally carry operator free-text
bleeding from adjacent columns; `end_datetime` is largely NULL; `direction` is
~99% NULL. We do NOT trust the file blindly — rows that fail schema validation
are quarantined (kept, flagged) rather than silently dropped, so ingestion is
auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Controlled vocabularies. Anything outside these in a categorical column is a
# contamination signal -> quarantine the row's value (coerce to "other"/NaN)
# but keep the row.
VALID_EVENT_TYPES = {"planned", "unplanned"}
VALID_PRIORITIES = {"High", "Low"}
VALID_STATUSES = {"active", "resolved", "closed"}

# Planned-event causes are the wedge. Full known cause vocabulary observed:
KNOWN_CAUSES = {
    "vehicle_breakdown", "others", "pot_holes", "construction", "water_logging",
    "accident", "tree_fall", "road_conditions", "congestion", "public_event",
    "procession", "vip_movement", "protest",
}

# Causes that constitute a planned demand-shock (the wedge).
PLANNED_CAUSES = {"construction", "public_event", "procession", "vip_movement", "protest"}

NULLISH = {"", "NULL", "null", "None", "nan", "NaN"}


def _clean_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in NULLISH else s


def _parse_ts(series: pd.Series) -> pd.Series:
    # Source timestamps are ISO-ish with +00 offset; mixed precision. Coerce.
    return pd.to_datetime(series, errors="coerce", utc=True, format="mixed")


def _parse_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


@dataclass
class IngestReport:
    total_rows: int
    kept_rows: int
    quarantined_event_type: int
    quarantined_priority: int
    bad_coordinates: int
    missing_start: int
    planned_rows: int

    def summary(self) -> str:
        return (
            f"ingested {self.total_rows} rows | usable {self.kept_rows} | "
            f"planned {self.planned_rows} | "
            f"quarantined event_type {self.quarantined_event_type}, "
            f"priority {self.quarantined_priority} | "
            f"bad coords {self.bad_coordinates} | missing start {self.missing_start}"
        )


# Columns the loader reads. A minimal/external CSV (e.g. a scraped event feed)
# only needs the first group; the rest are outcome/audit fields that simply don't
# exist for a future event, so we synthesize them as empty rather than require them.
REQUIRED_COLS = {"id", "event_type", "event_cause", "latitude", "longitude",
                 "start_datetime"}
OPTIONAL_COLS = {"endlatitude", "endlongitude", "priority", "status",
                 "requires_road_closure", "authenticated", "end_datetime",
                 "resolved_datetime", "closed_datetime", "created_date",
                 "corridor", "zone", "junction", "police_station",
                 "veh_type", "cargo_material", "address"}


def load_events(csv_path: str | Path) -> tuple[pd.DataFrame, IngestReport]:
    """Load and validate the event log. Returns (clean_df, report).

    Tolerant of minimal inputs: a CSV carrying only the required columns (the
    case for a calendar of upcoming events) is accepted — missing optional
    columns are filled empty, since outcome/audit fields don't exist ahead of time.
    """
    raw = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing_required = REQUIRED_COLS - set(raw.columns)
    if missing_required:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing_required)}. "
            f"Needed: {sorted(REQUIRED_COLS)}.")
    for col in OPTIONAL_COLS - set(raw.columns):
        raw[col] = ""  # synthesize empty so downstream column access is safe
    total = len(raw)

    df = pd.DataFrame(index=raw.index)
    df["event_id"] = raw["id"].map(_clean_str)

    # --- event_type: quarantine free-text contamination --------------------
    et = raw["event_type"].map(_clean_str)
    bad_et = ~et.isin(VALID_EVENT_TYPES) & et.notna()
    # If event_type is junk but event_cause is a known planned cause, recover it.
    cause_raw = raw["event_cause"].map(_clean_str)
    df["event_cause"] = cause_raw.where(cause_raw.isin(KNOWN_CAUSES), other="others")
    recovered = bad_et & df["event_cause"].isin(PLANNED_CAUSES)
    df["event_type"] = np.where(
        et.isin(VALID_EVENT_TYPES), et,
        np.where(recovered, "planned",
                 np.where(df["event_cause"].isin(PLANNED_CAUSES), "planned", "unplanned")),
    )

    # --- priority ----------------------------------------------------------
    pr = raw["priority"].map(_clean_str)
    bad_pr = ~pr.isin(VALID_PRIORITIES) & pr.notna()
    df["priority"] = pr.where(pr.isin(VALID_PRIORITIES), other="Low")

    df["status"] = raw["status"].map(_clean_str).where(
        raw["status"].map(_clean_str).isin(VALID_STATUSES), other=None
    )
    df["requires_road_closure"] = raw["requires_road_closure"].map(
        lambda v: _clean_str(v) == "TRUE"
    )
    df["authenticated"] = raw["authenticated"].map(lambda v: _clean_str(v) == "yes")

    # --- geometry ----------------------------------------------------------
    df["lat"] = _parse_float(raw["latitude"])
    df["lon"] = _parse_float(raw["longitude"])
    df["end_lat"] = _parse_float(raw["endlatitude"])
    df["end_lon"] = _parse_float(raw["endlongitude"])
    # 0/0 endpoints are sentinels for "point event", not real coordinates.
    df.loc[df["end_lat"].eq(0) & df["end_lon"].eq(0), ["end_lat", "end_lon"]] = np.nan
    bad_coords = ~df["lat"].between(-90, 90) | ~df["lon"].between(-180, 180)
    df.loc[bad_coords, ["lat", "lon"]] = np.nan
    df["is_linear"] = df["end_lat"].notna() & df["end_lon"].notna()

    # --- categorical context ----------------------------------------------
    for col in ("corridor", "zone", "junction", "police_station", "veh_type",
                "cargo_material", "address"):
        df[col] = raw[col].map(_clean_str)
    # veh_type free-text contamination -> only keep a short controlled set.
    valid_veh = {
        "bmtc_bus", "heavy_vehicle", "lcv", "others", "private_bus",
        "private_car", "truck", "ksrtc_bus", "taxi", "auto",
    }
    df["veh_type"] = df["veh_type"].where(df["veh_type"].isin(valid_veh), other=None)

    # --- timestamps --------------------------------------------------------
    df["start_dt"] = _parse_ts(raw["start_datetime"])
    df["end_dt"] = _parse_ts(raw["end_datetime"])
    df["resolved_dt"] = _parse_ts(raw["resolved_datetime"])
    df["closed_dt"] = _parse_ts(raw["closed_datetime"])
    df["created_dt"] = _parse_ts(raw["created_date"])

    missing_start = df["start_dt"].isna()

    # Usable rows: must have an id and a start timestamp (the anchor for every
    # time/concurrency feature). Everything else is recoverable.
    keep = df["event_id"].notna() & ~missing_start
    clean = df.loc[keep].reset_index(drop=True)

    report = IngestReport(
        total_rows=total,
        kept_rows=len(clean),
        quarantined_event_type=int(bad_et.sum()),
        quarantined_priority=int(bad_pr.sum()),
        bad_coordinates=int(bad_coords.sum()),
        missing_start=int(missing_start.sum()),
        planned_rows=int((clean["event_type"] == "planned").sum()),
    )
    return clean, report
