"""The Phase 0 baseline model: LightGBM + historical-analog blend.

Per the brief, *this pairing is the product*, not a warm-up for a GNN:
  * LightGBM (GBM) over the engineered features — robust to mixed types,
    handles missingness natively (no imputation), trains in seconds, gives
    readable feature importances.
  * Historical analogs — provide a second estimate AND the explanation surface.

Blend rule (confidence-weighted, transparent): when analog support is dense and
its spread is tight, trust the analog estimate more; when analogs are sparse or
disagree, the GBM carries the prediction. The blend weight is a function the
operator can read, not a learned gate.

The target may be right-censored (real duration). We train the GBM only on
uncensored rows — a censored value is a lower bound, not a label — which is the
honest thing to do without a full survival objective in Phase 0. This is
documented as a limitation, not hidden.
"""
from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from .analogs import AnalogResult, retrieve_analogs
from .config import Config
from .features import feature_columns
from .targets import TargetFrame


@dataclass
class Prediction:
    gbm: np.ndarray         # GBM-only prediction
    analog: np.ndarray      # analog-only estimate (nan where no analogs)
    blended: np.ndarray     # confidence-weighted blend (the served prediction)
    analog_weight: np.ndarray  # weight given to the analog estimate, in [0,1]
    analog_support: np.ndarray
    analog_spread: np.ndarray
    analog_ids: list[list[str]]


@dataclass
class TrainedModel:
    booster: lgb.Booster
    feature_cols: list[str]
    cfg: Config
    target_name: str

    def predict(
        self,
        df: pd.DataFrame,
        feats: pd.DataFrame,
        y: pd.Series,
        observed: pd.Series,
        *,
        query_mask: pd.Series,
        history_mask: pd.Series,
    ) -> Prediction:
        X = feats[self.feature_cols]
        gbm = self.booster.predict(X)
        gbm = np.asarray(gbm, dtype=float)

        ar: AnalogResult = retrieve_analogs(
            df, y, observed, self.cfg,
            query_mask=query_mask, history_mask=history_mask,
        )

        w = _analog_weight(ar, self.cfg)
        analog = ar.estimate
        # where no analog, weight is already 0 and estimate nan -> use gbm only
        analog_safe = np.where(np.isnan(analog), gbm, analog)
        blended = w * analog_safe + (1 - w) * gbm
        blended = np.clip(blended, 0.0, None)

        return Prediction(
            gbm=gbm, analog=analog, blended=blended, analog_weight=w,
            analog_support=ar.support, analog_spread=ar.spread,
            analog_ids=ar.analog_ids,
        )


def _analog_weight(ar: AnalogResult, cfg: Config) -> np.ndarray:
    """Confidence in the analog estimate, in [0, analog_weight_cap].

      * support term: rises toward 1 as support reaches analog_k.
      * agreement term: falls SHARPLY as spread grows relative to the estimate.
        On this data analog spread is large (long-tailed durations), so the
        agreement term must be strict — otherwise noisy analogs drag the blend
        below GBM-only. The weight is capped so the blend can never be much
        worse than the GBM: analogs refine, they do not override.
    All transparent; no learned parameters.
    """
    support = ar.support.astype(float)
    support_term = np.clip(support / max(cfg.analog_k, 1), 0.0, 1.0)

    est = np.where(np.isnan(ar.estimate), 0.0, ar.estimate)
    spread = np.where(np.isnan(ar.spread), np.inf, ar.spread)
    rel_spread = spread / np.maximum(est, 1.0)
    # squared rel_spread => analogs must AGREE tightly to earn weight
    agreement_term = 1.0 / (1.0 + rel_spread ** 2)

    w = support_term * agreement_term
    w[support < cfg.analog_min_support] *= 0.5
    w[np.isnan(ar.estimate)] = 0.0
    return np.clip(w, 0.0, cfg.analog_weight_cap)


def train_gbm(
    df: pd.DataFrame,
    feats: pd.DataFrame,
    tf: TargetFrame,
    cfg: Config,
    *,
    train_mask: pd.Series,
) -> TrainedModel:
    """Train LightGBM on uncensored rows within train_mask."""
    cols = feature_columns(feats)
    fit = train_mask.to_numpy() & (tf.observed.to_numpy() == 1)
    X = feats.loc[fit, cols]
    y = tf.y.loc[fit]

    cat_cols = [c for c in cols if str(feats[c].dtype) == "category"]
    dtrain = lgb.Dataset(X, label=y, categorical_feature=cat_cols, free_raw_data=False)

    params = {
        "objective": "regression_l1",  # MAE: robust to the long-tail durations
        "metric": "l1",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "min_data_in_leaf": 30,
        "verbose": -1,
        "seed": cfg.random_seed,
    }
    booster = lgb.train(params, dtrain, num_boost_round=300)
    return TrainedModel(booster=booster, feature_cols=cols, cfg=cfg, target_name=tf.name)


def feature_importance(model: TrainedModel, top: int = 12) -> pd.Series:
    imp = pd.Series(
        model.booster.feature_importance(importance_type="gain"),
        index=model.feature_cols,
    ).sort_values(ascending=False)
    return imp.head(top)
