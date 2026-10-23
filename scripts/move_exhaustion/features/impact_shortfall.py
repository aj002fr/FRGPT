"""E07 state-dependent expected impact and shortfall features.

Train expected Δprice (ticks) on non-event bars only; never fit on
exhaustion labels. Shortfall = expected_directional - actual_directional.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _ts

STATE_COLS = (
    "agg_1m",
    "spread_mean",
    "displayed_depth_mean",
    "realized_vol",
    "trade_intensity",
    "minutes_since_open",
)


@dataclass
class ImpactModelBundle:
    name: str
    predict: Callable[[np.ndarray], np.ndarray]
    feature_names: list[str]
    train_rmse: float | None = None
    oos_rmse: float | None = None
    oos_mae: float | None = None
    n_train: int = 0
    n_oos: int = 0
    scale: float | None = None  # for fixed/rolling linear
    residual_std: float = 1.0


def build_bar_impact_frame(panel: pd.DataFrame, *, symbol: str = "TY") -> pd.DataFrame:
    """One row per bar with signed 1m price change (ticks) and state features."""
    tick = TICK_SIZE[symbol]
    out = panel.sort_values(["et_date", "bucket_ts"]).copy()
    px = pd.to_numeric(out["price"], errors="coerce")
    out["y_move_ticks"] = px.groupby(out["et_date"]).diff(1) / tick
    for c in STATE_COLS:
        if c not in out.columns:
            out[c] = np.nan
        else:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # Causal: state features are contemporaneous with the bar's flow; target is
    # same-bar price change (TBBO panel convention used elsewhere).
    return out


def mark_event_bars(
    bar_df: pd.DataFrame,
    events: pd.DataFrame,
    *,
    pad_minutes: int = 15,
) -> pd.Series:
    """True where bar falls inside an event window [start, start+pad]."""
    mask = pd.Series(False, index=bar_df.index)
    if events.empty:
        return mask
    ts = pd.to_datetime(bar_df["bucket_ts"], utc=True)
    for _, ev in events.iterrows():
        start = _ts(ev["event_start_time"])
        end = start + pd.Timedelta(minutes=pad_minutes)
        day = str(ev["et_date"])
        local = (bar_df["et_date"] == day) & (ts >= start) & (ts <= end)
        mask = mask | local
    return mask


def _xy(df: pd.DataFrame, cols: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    work = df.dropna(subset=cols + ["y_move_ticks"]).copy()
    x = work[cols].to_numpy(dtype=float)
    y = work["y_move_ticks"].to_numpy(dtype=float)
    ok = np.isfinite(x).all(axis=1) & np.isfinite(y)
    return x[ok], y[ok], work.index.to_numpy()[ok]


def fit_fixed_aggression(train: pd.DataFrame) -> ImpactModelBundle:
    """expected = alpha * agg_1m; alpha from median(y/flow) on same-sign bars."""
    flow = pd.to_numeric(train["agg_1m"], errors="coerce")
    y = pd.to_numeric(train["y_move_ticks"], errors="coerce")
    ratio = y / flow.replace(0.0, np.nan)
    same = ratio[np.sign(y) == np.sign(flow)].dropna()
    alpha = float(same.median()) if len(same) else 0.0
    if not np.isfinite(alpha):
        alpha = 0.0

    def predict(x: np.ndarray) -> np.ndarray:
        # x column 0 = agg_1m
        return alpha * x[:, 0]

    resid = y.to_numpy(dtype=float) - alpha * flow.to_numpy(dtype=float)
    resid = resid[np.isfinite(resid)]
    return ImpactModelBundle(
        name="fixed_aggression_scaling",
        predict=predict,
        feature_names=["agg_1m"],
        n_train=int(np.isfinite(resid).sum()),
        scale=alpha,
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def fit_rolling_linear(train: pd.DataFrame) -> ImpactModelBundle:
    """OLS through origin: y ~ alpha * agg_1m."""
    flow = pd.to_numeric(train["agg_1m"], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(train["y_move_ticks"], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(flow) & np.isfinite(y) & (np.abs(flow) > EPS)
    if ok.sum() < 20:
        return fit_fixed_aggression(train)
    alpha = float(np.dot(flow[ok], y[ok]) / np.dot(flow[ok], flow[ok]))

    def predict(x: np.ndarray) -> np.ndarray:
        return alpha * x[:, 0]

    resid = y[ok] - alpha * flow[ok]
    return ImpactModelBundle(
        name="rolling_linear_scaling",
        predict=predict,
        feature_names=["agg_1m"],
        n_train=int(ok.sum()),
        scale=alpha,
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def fit_ridge(train: pd.DataFrame, cols: list[str] | None = None) -> ImpactModelBundle:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    cols = list(cols or STATE_COLS)
    x, y, _ = _xy(train, cols)
    if len(y) < 50:
        raise ValueError("insufficient train rows for ridge")
    scaler = StandardScaler()
    xs = scaler.fit_transform(x)
    model = Ridge(alpha=1.0)
    model.fit(xs, y)
    pred = model.predict(xs)
    resid = y - pred

    def predict(x_raw: np.ndarray) -> np.ndarray:
        return model.predict(scaler.transform(x_raw))

    return ImpactModelBundle(
        name="liquidity_conditioned_ridge",
        predict=predict,
        feature_names=cols,
        n_train=len(y),
        train_rmse=float(np.sqrt(np.mean(resid**2))),
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def fit_huber(train: pd.DataFrame, cols: list[str] | None = None) -> ImpactModelBundle:
    from sklearn.linear_model import HuberRegressor
    from sklearn.preprocessing import StandardScaler

    cols = list(cols or STATE_COLS)
    x, y, _ = _xy(train, cols)
    if len(y) < 50:
        raise ValueError("insufficient train rows for huber")
    scaler = StandardScaler()
    xs = scaler.fit_transform(x)
    model = HuberRegressor(max_iter=500)
    model.fit(xs, y)
    pred = model.predict(xs)
    resid = y - pred

    def predict(x_raw: np.ndarray) -> np.ndarray:
        return model.predict(scaler.transform(x_raw))

    return ImpactModelBundle(
        name="liquidity_conditioned_huber",
        predict=predict,
        feature_names=cols,
        n_train=len(y),
        train_rmse=float(np.sqrt(np.mean(resid**2))),
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def fit_hist_gbm(train: pd.DataFrame, cols: list[str] | None = None) -> ImpactModelBundle:
    from sklearn.ensemble import HistGradientBoostingRegressor

    cols = list(cols or STATE_COLS)
    x, y, _ = _xy(train, cols)
    if len(y) < 100:
        raise ValueError("insufficient train rows for gbm")
    model = HistGradientBoostingRegressor(
        max_depth=3,
        max_iter=100,
        learning_rate=0.05,
        random_state=42,
    )
    model.fit(x, y)
    pred = model.predict(x)
    resid = y - pred

    def predict(x_raw: np.ndarray) -> np.ndarray:
        return model.predict(x_raw)

    return ImpactModelBundle(
        name="boosted_expected_impact",
        predict=predict,
        feature_names=cols,
        n_train=len(y),
        train_rmse=float(np.sqrt(np.mean(resid**2))),
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def fit_gam(train: pd.DataFrame, cols: list[str] | None = None) -> ImpactModelBundle | None:
    """Optional GAM if pygam is installed; else None."""
    try:
        from pygam import LinearGAM, s
    except ImportError:
        return None
    cols = list(cols or STATE_COLS)
    x, y, _ = _xy(train, cols)
    if len(y) < 100:
        return None
    # Limit to first 4 features for stability/speed
    use = cols[:4]
    x4 = x[:, :4]
    term = s(0)
    for i in range(1, x4.shape[1]):
        term = term + s(i)
    gam = LinearGAM(term).fit(x4, y)
    pred = gam.predict(x4)
    resid = y - pred

    def predict(x_raw: np.ndarray) -> np.ndarray:
        return gam.predict(x_raw[:, :4])

    return ImpactModelBundle(
        name="liquidity_conditioned_gam",
        predict=predict,
        feature_names=use,
        n_train=len(y),
        train_rmse=float(np.sqrt(np.mean(resid**2))),
        residual_std=float(np.std(resid)) if len(resid) else 1.0,
    )


def evaluate_regression(bundle: ImpactModelBundle, test: pd.DataFrame) -> ImpactModelBundle:
    cols = bundle.feature_names
    x, y, _ = _xy(test, cols)
    if len(y) < 20:
        bundle.oos_rmse = None
        bundle.oos_mae = None
        bundle.n_oos = len(y)
        return bundle
    pred = bundle.predict(x)
    err = y - pred
    bundle.oos_rmse = float(np.sqrt(np.mean(err**2)))
    bundle.oos_mae = float(np.mean(np.abs(err)))
    bundle.n_oos = len(y)
    return bundle


def fit_all_impact_models(train: pd.DataFrame, test: pd.DataFrame) -> list[ImpactModelBundle]:
    """Fit candidate expected-impact models; attach OOS regression metrics."""
    bundles: list[ImpactModelBundle] = []
    for factory in (fit_fixed_aggression, fit_rolling_linear, fit_ridge, fit_huber, fit_hist_gbm):
        try:
            b = factory(train)
            bundles.append(evaluate_regression(b, test))
        except Exception as exc:  # noqa: BLE001 — research harness records failures
            bundles.append(
                ImpactModelBundle(
                    name=getattr(factory, "__name__", "unknown"),
                    predict=lambda x: np.zeros(len(x)),
                    feature_names=["agg_1m"],
                    oos_rmse=None,
                    n_train=0,
                )
            )
            bundles[-1].name = f"{bundles[-1].name}_failed:{type(exc).__name__}"
    gam = fit_gam(train)
    if gam is not None:
        bundles.append(evaluate_regression(gam, test))
    return bundles


def select_best_by_oos_rmse(bundles: list[ImpactModelBundle]) -> ImpactModelBundle | None:
    scored = [b for b in bundles if b.oos_rmse is not None and np.isfinite(b.oos_rmse)]
    if not scored:
        return None
    return min(scored, key=lambda b: b.oos_rmse or 1e9)


def event_impact_shortfall(
    panel: pd.DataFrame,
    event: dict[str, Any],
    bundle: ImpactModelBundle,
    *,
    lookback_bars: int = 1,
    landmark_minutes: int = 5,
    symbol: str = "TY",
) -> dict[str, float | None]:
    """Causal shortfall at event start and cumulative to landmark."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    at_start = day[day["bucket_ts"] <= start]
    if len(at_start) < lookback_bars + 1:
        return {
            "expected_directional_progress": None,
            "actual_directional_progress": None,
            "impact_shortfall": None,
            "impact_shortfall_z": None,
            "cum_impact_shortfall": None,
            "residual_z_at_event": None,
        }

    row = at_start.iloc[-1]
    cols = bundle.feature_names
    x = np.array([[float(pd.to_numeric(row.get(c), errors="coerce")) for c in cols]], dtype=float)
    if not np.isfinite(x).all():
        # fill missing with column medians from nearby history
        hist = at_start.tail(50)
        for j, c in enumerate(cols):
            if not np.isfinite(x[0, j]):
                x[0, j] = float(pd.to_numeric(hist[c], errors="coerce").median())
    if not np.isfinite(x).all():
        return {
            "expected_directional_progress": None,
            "actual_directional_progress": None,
            "impact_shortfall": None,
            "impact_shortfall_z": None,
            "cum_impact_shortfall": None,
            "residual_z_at_event": float(pd.to_numeric(row.get("residual_z"), errors="coerce"))
            if pd.notna(row.get("residual_z"))
            else None,
        }

    pred_signed = float(bundle.predict(x)[0])
    # Actual same-bar / lookback move
    px = pd.to_numeric(at_start["price"], errors="coerce")
    actual_signed = float(px.iloc[-1] - px.iloc[-(lookback_bars + 1)]) / tick
    expected_dir = direction * pred_signed
    actual_dir = direction * actual_signed
    shortfall = expected_dir - actual_dir
    rstd = bundle.residual_std if bundle.residual_std and bundle.residual_std > EPS else 1.0

    # Cumulative shortfall over [start, start+landmark]
    obs = start + pd.Timedelta(minutes=landmark_minutes)
    window = day[(day["bucket_ts"] > start) & (day["bucket_ts"] <= obs)]
    cum = 0.0
    n_cum = 0
    if len(window):
        prev_px = float(px.iloc[-1])
        for _, r in window.iterrows():
            xr = np.array([[float(pd.to_numeric(r.get(c), errors="coerce")) for c in cols]], dtype=float)
            if not np.isfinite(xr).all():
                continue
            pred_s = float(bundle.predict(xr)[0])
            cur_px = float(pd.to_numeric(r["price"], errors="coerce"))
            act_s = (cur_px - prev_px) / tick
            prev_px = cur_px
            cum += direction * pred_s - direction * act_s
            n_cum += 1

    return {
        "expected_directional_progress": expected_dir,
        "actual_directional_progress": actual_dir,
        "impact_shortfall": float(shortfall),
        "impact_shortfall_z": float(shortfall / rstd),
        "cum_impact_shortfall": float(cum) if n_cum else None,
        "n_cum_bars": float(n_cum),
        "residual_z_at_event": float(pd.to_numeric(row.get("residual_z"), errors="coerce"))
        if pd.notna(row.get("residual_z"))
        else None,
        "impact_model": bundle.name,
    }
