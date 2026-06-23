"""In-memory server state.

The LightGBM booster is trained once at startup (~1-2s) and reused for every
request — train_gbm has no persistence, so without this the model would refit on
every call. Only `latest_view` mutates after warm-up (the scheduler swaps it);
that swap is guarded by `lock`. The model and history are read-only post-warm,
and LightGBM inference is thread-safe.
"""
from __future__ import annotations

from threading import Lock


class _State:
    model = None          # TrainedModel — the cached booster
    hist_df = None        # cleaned historical events (analog pool + training)
    hist_rep = None       # IngestReport for the history load
    latest_view = None    # most recent scheduled-refresh forecast (with lat/lon)
    warm_error = None     # set if warm-up failed, surfaced by /api/health
    lock = Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None and self.hist_df is not None


STATE = _State()
