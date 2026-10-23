from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from scripts.aggression_dashboard_v23.features import FEATURE_SET


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
MIN_ROWS_PER_DAY = 500
MIN_POINTS_FOR_Z = 30
PRIOR_OBSERVATIONS = 60
Z_METHOD = "prior_blend_v1"


class RidgeModelState:
    def __init__(self, matrix_path: Path = FEATURE_MATRIX) -> None:
        self.matrix_path = matrix_path
        self._lock = threading.Lock()
        self._fit_date: str | None = None
        self._model: Pipeline | None = None
        self._meta: Dict[str, Any] = {"ok": False, "reason": "not fit"}

    def ensure_fit(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            if self._fit_date == today and self._model is not None:
                return
        self.fit()

    def fit(self) -> None:
        df = pd.read_csv(self.matrix_path, parse_dates=["bucket_ts"])
        by_day = df.groupby("et_date").agg(
            rows=("price", "count"),
            price_min=("price", "min"),
            price_max=("price", "max"),
            target_std=("target_price_delta", "std"),
        )
        clean_dates = by_day[
            (by_day["rows"] >= MIN_ROWS_PER_DAY)
            & (by_day["price_min"] > 80)
            & (by_day["price_max"] < 150)
            & (by_day["target_std"] > 0.01)
            & (by_day["target_std"] < 1.0)
        ].index.tolist()
        train = df[df["et_date"].isin(clean_dates)].replace([np.inf, -np.inf], np.nan)
        train = train.dropna(subset=["target_price_delta", *FEATURE_SET]).copy()
        model = Pipeline([
            ("scale", StandardScaler()),
            ("model", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
        ])
        model.fit(train[FEATURE_SET].astype(float), train["target_price_delta"].astype(float))
        ridge = model.named_steps["model"]
        train_pred_delta = model.predict(train[FEATURE_SET].astype(float))
        train_residual = train["target_price_delta"].astype(float) - train_pred_delta
        residual_by_day = train.assign(_residual=train_residual).groupby("et_date")["_residual"]
        per_day_std = residual_by_day.std(ddof=1).replace([np.inf, -np.inf], np.nan).dropna()
        sigma_hist = float(per_day_std[per_day_std > 0].median()) if not per_day_std.empty else float(train_residual.std(ddof=1))
        if not np.isfinite(sigma_hist) or sigma_hist <= 0:
            sigma_hist = 1.0
        mu_hist = float(train_residual.mean()) if np.isfinite(train_residual.mean()) else 0.0
        with self._lock:
            self._model = model
            self._fit_date = datetime.now(timezone.utc).date().isoformat()
            self._meta = {
                "ok": True,
                "fit_date": self._fit_date,
                "train_rows": int(len(train)),
                "train_dates": clean_dates,
                "alpha": float(ridge.alpha_),
                "features": FEATURE_SET,
                "mu_hist": mu_hist,
                "sigma_hist": sigma_hist,
                "z_method": Z_METHOD,
                "z_prior_observations": PRIOR_OBSERVATIONS,
            }

    def predict(self, features: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Any]]:
        self.ensure_fit()
        with self._lock:
            model = self._model
            meta = dict(self._meta)
        if model is None or features.empty:
            return pd.DataFrame(), meta
        data = features.copy()
        data = data.dropna(subset=["price", "day_open_price", *FEATURE_SET]).copy()
        if data.empty:
            return pd.DataFrame(), meta
        pred_delta = model.predict(data[FEATURE_SET].astype(float))
        data["predicted_delta"] = pred_delta
        data["fair_value"] = data["day_open_price"] + data["predicted_delta"]
        data["residual"] = data["price"] - data["fair_value"]
        mu_hist = float(meta.get("mu_hist") or 0.0)
        sigma_hist = float(meta.get("sigma_hist") or 1.0)
        if not np.isfinite(sigma_hist) or sigma_hist <= 0:
            sigma_hist = 1.0
        expanding_mean = data["residual"].expanding(min_periods=MIN_POINTS_FOR_Z).mean()
        expanding_std = data["residual"].expanding(min_periods=MIN_POINTS_FOR_Z).std(ddof=1)
        count = data["residual"].expanding(min_periods=1).count()
        weight = PRIOR_OBSERVATIONS / (PRIOR_OBSERVATIONS + count)
        today_mean = expanding_mean.where(expanding_mean.notna(), mu_hist)
        today_std = expanding_std.where(expanding_std.notna(), sigma_hist).clip(lower=0.0)
        blended_mean = weight * mu_hist + (1.0 - weight) * today_mean
        blended_var = weight * (sigma_hist ** 2) + (1.0 - weight) * (today_std ** 2)
        blended_std = np.sqrt(blended_var).replace(0.0, np.nan)
        data["resid_mean"] = blended_mean
        data["resid_std"] = blended_std
        data["residual_z"] = (data["residual"] - blended_mean) / blended_std
        data["z_method"] = str(meta.get("z_method") or Z_METHOD)
        return data, meta

    def status(self) -> Dict[str, Any]:
        self.ensure_fit()
        with self._lock:
            return dict(self._meta)
