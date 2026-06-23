#!/usr/bin/env python
"""Scrape upcoming public events from the web -> the CSV that predict.py consumes.

This is the *input* automation layer. The system's wedge is planned events known
ahead of time; this tool fetches them from public listings, geocodes each to a
lat/lon, and writes `upcoming_events.csv` with the columns the pipeline needs.
Then `python predict.py upcoming_events.csv` turns that into a deployment plan.

    web listings --> scrape_events.py --> upcoming_events.csv --> predict.py --> dashboard

Design notes
------------
* **Reliability first.** A live scrape can fail (site markup changes, rate limits,
  no network during a demo). So every fetch degrades gracefully, and if nothing is
  scraped we fall back to a **bundled cached sample** (`events_cache.json`) so the
  pipeline always produces a result. Nothing here ever crashes the demo.
* **Source is pluggable.** `SOURCES` lists fetchers; add a city/site by writing one
  function that returns a list of RawEvent dicts. The parser is deliberately
  separated from geocoding and CSV-writing.
* **Geocoding** uses the free OpenStreetMap Nominatim API over plain HTTP (no extra
  dependency), with an on-disk cache so repeated runs don't re-hit the service.
* **Not real-time traffic.** This scrapes the *event calendar* (what's coming), not
  live congestion. Live probe/connected-vehicle data is a Phase-2 feed that drops
  into the pipeline's TargetProvider seam - deliberately out of scope here (see
  DESIGN.md on the lost-ground-truth problem).

Usage:
    python scrape_events.py                 # scrape (or fall back), write upcoming_events.csv
    python scrape_events.py --out my.csv
    python scrape_events.py --city Bengaluru
    python scrape_events.py --offline       # skip the network, use the cached sample
    python scrape_events.py --run           # also run predict.py on the result
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "events_cache.json"          # bundled fallback + scrape archive
GEOCODE_CACHE = ROOT / ".geocode_cache.json"

# Columns the pipeline reads (see gridlock/ingest.py). Everything else in the
# Astram schema is optional; we emit the required subset plus a few helpful ones.
CSV_COLUMNS = [
    "id", "event_type", "event_cause", "latitude", "longitude",
    "address", "requires_road_closure", "start_datetime", "end_datetime",
    "status", "priority", "corridor",
]

# Map free-text event kinds to the controlled vocabulary of planned causes.
KIND_TO_CAUSE = {
    "concert": "public_event", "festival": "public_event", "music": "public_event",
    "sports": "public_event", "match": "public_event", "marathon": "public_event",
    "procession": "procession", "rally": "protest", "protest": "protest",
    "vip": "vip_movement", "roadwork": "construction", "construction": "construction",
}


@dataclass
class RawEvent:
    """A scraped event before geocoding/normalization."""
    title: str
    kind: str                 # free-text; mapped to a cause
    venue: str                # geocodable place string
    start: str                # ISO-ish datetime
    end: str | None = None
    closure: bool = True
    priority: str = "High"
    lat: float | None = None  # if the source already provides coordinates
    lon: float | None = None


# --------------------------------------------------------------------------- #
# Fetchers. Each returns list[RawEvent]; each must fail soft (return [] on error).
# --------------------------------------------------------------------------- #
def _http_get(url: str, timeout: float = 8.0) -> str | None:
    try:
        import requests
        r = requests.get(url, timeout=timeout,
                          headers={"User-Agent": "CrowdWise/0.1 (event-ops research)"})
        if r.ok:
            return r.text
    except Exception as e:  # network down, blocked, lib missing - degrade
        print(f"  [warn] fetch failed for {url[:48]}...: {e}")
    return None


def fetch_public_listings(city: str) -> list[RawEvent]:
    """Public event-listings fetcher.

    Public listing sites change markup often and many forbid scraping in their
    ToS, so a hardcoded CSS-selector parser is a liability during judging. This
    fetcher therefore tries a couple of best-effort public endpoints and, on any
    failure, returns [] so the caller falls back to the cached sample. To wire a
    specific site, parse `html` here into RawEvent objects.
    """
    events: list[RawEvent] = []
    # Example public endpoint shape (intentionally generic; swap for a real one).
    html = _http_get(f"https://example.org/events?city={city}")
    if html is None:
        return events
    try:
        from bs4 import BeautifulSoup  # noqa: F401 - parser hook for a real source
        # soup = BeautifulSoup(html, "html.parser")
        # for card in soup.select(".event-card"): events.append(RawEvent(...))
        pass
    except Exception as e:
        print(f"  [warn] parse failed: {e}")
    return events


# PredictHQ event categories -> the model's controlled causes.
PHQ_CATEGORY_TO_CAUSE = {
    "concerts": "public_event", "festivals": "public_event",
    "performing-arts": "public_event", "sports": "public_event",
    "community": "public_event", "expos": "public_event",
    "conferences": "public_event", "politics": "protest",
    "observances": "procession",
}
# Categories whose footprint usually implies a road closure.
PHQ_CLOSURE_CATEGORIES = {"sports", "concerts", "festivals", "politics"}


def fetch_predicthq(city: str) -> list[RawEvent]:
    """Fetch upcoming events from the PredictHQ API.

    Needs the PREDICTHQ_TOKEN env var; without it, returns [] so the caller
    falls back to the cached sample. PredictHQ supplies a [lon, lat] location,
    so most rows skip Nominatim. Fails soft on any error.
    """
    token = os.getenv("PREDICTHQ_TOKEN", "").strip()
    if not token:
        return []
    try:
        import requests
        today = datetime.now(timezone.utc).date()
        horizon = today + timedelta(days=14)
        # Geographic scope: a `within` radius covers a whole region intentionally,
        # rather than the fuzzy text `q` (which scatters results unpredictably).
        # Default covers Karnataka (centroid ~14.5N,76E, ~450km radius). Override
        # with PHQ_WITHIN="<radius>km@<lat>,<lon>" for another region/city.
        within = os.getenv("PHQ_WITHIN", "450km@14.5,76.0").strip()
        params = {
            "active.gte": today.isoformat(),
            "active.lte": horizon.isoformat(),
            "category": ",".join(PHQ_CATEGORY_TO_CAUSE),
            "within": within,
            "limit": 100, "sort": "rank",
        }
        r = requests.get(
            "https://api.predicthq.com/v1/events/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            timeout=10.0,
        )
        if not r.ok:
            print(f"  [warn] predicthq HTTP {r.status_code}: {r.text[:120]}")
            return []
        out: list[RawEvent] = []
        for ev in r.json().get("results", []):
            cat = ev.get("category", "")
            loc = ev.get("location") or [None, None]   # PHQ geo = [lon, lat]
            lon, lat = (loc + [None, None])[:2]
            entities = ev.get("entities") or []
            venue = (entities[0].get("name") if entities else None) or ev.get("title") or city
            out.append(RawEvent(
                title=ev.get("title", "event"),
                kind=cat,
                venue=venue,
                start=ev.get("start", ""),
                end=ev.get("end"),
                closure=cat in PHQ_CLOSURE_CATEGORIES,
                priority="High" if (ev.get("rank") or 0) >= 60 else "Low",
                lat=lat, lon=lon,
            ))
        print(f"  [predicthq] {len(out)} events for {city}")
        return out
    except Exception as e:
        print(f"  [warn] predicthq fetch failed: {e}")
        return []


# PredictHQ first (real data); public-listings + cache fallback behind it.
SOURCES = [fetch_predicthq, fetch_public_listings]


# --------------------------------------------------------------------------- #
# Geocoding (Nominatim, cached). Fails soft -> event without coords is dropped.
# --------------------------------------------------------------------------- #
def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def geocode(place: str, cache: dict) -> tuple[float, float] | None:
    if place in cache:
        v = cache[place]
        return (v[0], v[1]) if v else None
    url = ("https://nominatim.openstreetmap.org/search"
           f"?q={place}&format=json&limit=1")
    txt = _http_get(url)
    coords = None
    if txt:
        try:
            hits = json.loads(txt)
            if hits:
                coords = (float(hits[0]["lat"]), float(hits[0]["lon"]))
        except Exception:
            coords = None
    cache[place] = list(coords) if coords else None
    time.sleep(1.0)  # Nominatim asks <=1 req/s
    return coords


# --------------------------------------------------------------------------- #
# Normalization -> CSV rows
# --------------------------------------------------------------------------- #
def to_rows(events: list[RawEvent], geocode_cache: dict) -> list[dict]:
    rows = []
    for i, ev in enumerate(events):
        lat, lon = ev.lat, ev.lon
        if lat is None or lon is None:
            coords = geocode(ev.venue, geocode_cache)
            if coords is None:
                print(f"  [skip] could not geocode: {ev.venue!r}")
                continue
            lat, lon = coords
        kind = ev.kind.lower().strip()
        # PredictHQ category names first, then the free-text kind map, else public_event.
        cause = PHQ_CATEGORY_TO_CAUSE.get(kind) or KIND_TO_CAUSE.get(kind, "public_event")
        rows.append({
            "id": f"SCRAPE{i:04d}",
            "event_type": "planned",
            "event_cause": cause,
            "latitude": lat, "longitude": lon,
            "address": ev.venue,
            "requires_road_closure": "TRUE" if ev.closure else "FALSE",
            "start_datetime": ev.start,
            "end_datetime": ev.end or "",
            "status": "active",
            "priority": ev.priority,
            "corridor": "",
        })
    return rows


def write_csv(rows: list[dict], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- #
def load_cached_events() -> list[RawEvent]:
    """Bundled real-shaped sample - the fallback that keeps the demo alive."""
    data = _load_json(CACHE_FILE)
    return [RawEvent(**e) for e in data.get("events", [])]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="upcoming_events.csv")
    ap.add_argument("--city", default="Bengaluru")
    ap.add_argument("--offline", action="store_true",
                    help="skip the network, use the bundled cached sample")
    ap.add_argument("--run", action="store_true",
                    help="run predict.py on the scraped CSV afterwards")
    args = ap.parse_args()

    print("=" * 72)
    print("CrowdWise - scrape upcoming events -> deployment-plan input")
    print("=" * 72)

    events: list[RawEvent] = []
    if not args.offline:
        for fetch in SOURCES:
            print(f"[scrape] {fetch.__name__}({args.city}) ...")
            got = fetch(args.city)
            print(f"         {len(got)} events")
            events.extend(got)

    source = "live scrape"
    if not events:
        print("[fallback] no events scraped - using bundled cached sample "
              "(events_cache.json)")
        events = load_cached_events()
        source = "cached sample"

    if not events:
        raise SystemExit("No events available (live scrape empty and no cache). "
                         "Add events to events_cache.json.")

    geocode_cache = _load_json(GEOCODE_CACHE)
    rows = to_rows(events, geocode_cache)
    GEOCODE_CACHE.write_text(json.dumps(geocode_cache, indent=2), encoding="utf-8")

    if not rows:
        raise SystemExit("All events failed geocoding - nothing to write.")

    out = Path(args.out)
    write_csv(rows, out)
    print(f"\n[done] wrote {len(rows)} events to {out}  (source: {source})")
    for r in rows[:8]:
        print(f"   {r['event_cause']:14} {r['start_datetime'][:16]}  {r['address'][:46]}")

    if args.run:
        print("\n[run] python predict.py", out)
        subprocess.run([sys.executable, "predict.py", str(out)], check=False)
    else:
        print(f"\nNext: python predict.py {out}")


if __name__ == "__main__":
    main()
