"""CrowdWise web app — FastAPI backend serving the map + upload + ledger SPA.

Endpoints
  GET  /api/health           — readiness + warm status
  GET  /api/events/current   — latest scheduled-refresh forecast (with lat/lon)
  POST /api/predict          — upload a CSV, get a forecast (with lat/lon)
  POST /api/scrape           — manually trigger a scrape+forecast refresh
  GET  /  and static assets  — the single-page frontend
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.cache import STATE
from app import pipeline, scheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm the model + seed the first forecast, then start the refresh loop
    try:
        pipeline.warm()
        print("[startup] model warmed and ready")
    except Exception as e:
        print(f"[startup] WARM FAILED: {e}")
    if config.ENABLE_SCHEDULER and STATE.ready:
        scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="CrowdWise — Event-Driven Congestion", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {
        "ready": STATE.ready,
        "warm_error": STATE.warm_error,
        "has_forecast": STATE.latest_view is not None,
        "city": config.CITY,
        "refresh_minutes": config.REFRESH_MINUTES,
        "predicthq": bool(config.PREDICTHQ_TOKEN),
    }


@app.get("/api/events/current")
def events_current():
    if not STATE.ready:
        raise HTTPException(status_code=503, detail="warming up")
    if STATE.latest_view is None:
        raise HTTPException(status_code=503, detail="no forecast yet — try POST /api/scrape")
    return STATE.latest_view


@app.post("/api/predict")
async def predict(file: UploadFile = File(...)):
    if not STATE.ready:
        raise HTTPException(status_code=503, detail="warming up")
    raw = await file.read()
    try:
        view = pipeline.predict_from_csv(raw)
    except ValueError as e:
        # missing required columns or zero usable rows
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return view


@app.post("/api/scrape")
def scrape():
    if not STATE.ready:
        raise HTTPException(status_code=503, detail="warming up")
    try:
        view = pipeline.scrape_to_view()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"scrape failed: {e}")
    with STATE.lock:
        STATE.latest_view = view
    return view


@app.post("/api/retrain")
def retrain():
    """Refit the model on the original log + events seen since (that have passed).

    This is the 'improves with each event cycle' loop — it grows the training
    history, it does NOT learn from acted-upon outcomes (that needs shadow-mode
    evaluation; see DESIGN.md on the lost-ground-truth problem).
    """
    if not STATE.ready:
        raise HTTPException(status_code=503, detail="warming up")
    try:
        return pipeline.retrain()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"retrain failed: {e}")


# Frontend last, so /api/* routes win.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
