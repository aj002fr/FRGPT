from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard.core import classify_aggressor, load_dv01_table


EASTERN = ZoneInfo("America/New_York")
SYMBOLS = ("FV", "TU", "TY", "US")
PRICE_SYMBOL = "TY"
LARGE_TRADE_SIZE = 100
FEATURE_SET = [
    "agg_cum_daily",
    "agg_5m",
    "agg_30m",
    "buysell_ratio",
    "trade_intensity",
    "avg_trade_size",
    "large_trade_share",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
    "spread_mean",
    "size_imbalance_mean",
    "displayed_depth_mean",
    "trade_size_vs_depth",
    "depth_adjusted_signed_flow",
    "minutes_since_open",
]


def build_live_features(tbbo: pd.DataFrame, bucket: str = "1min") -> pd.DataFrame:
    if tbbo.empty:
        return pd.DataFrame()
    dv01 = load_dv01_table()
    frames = []
    for symbol in SYMBOLS:
        raw = tbbo[tbbo["dashboard_symbol"] == symbol].copy()
        if raw.empty:
            continue
        raw["ts"] = pd.to_datetime(raw["ts"], utc=True)
        raw = raw[(raw["price"] > 80) & (raw["price"] < 150) & (raw["size"] > 0)]
        raw = raw[(raw["bid_size"] > 0) & (raw["ask_size"] > 0) & (raw["ask_price"] >= raw["bid_price"])]
        if raw.empty:
            continue
        raw["sign"] = raw["side"].apply(classify_aggressor)
        unit = float(dv01.get(symbol, 1.0))
        raw["signed_flow"] = raw["sign"] * raw["size"] * unit
        raw["buy_flow"] = (raw["sign"] == 1).astype(float) * raw["size"] * unit
        raw["sell_flow"] = (raw["sign"] == -1).astype(float) * raw["size"] * unit
        raw["large_volume"] = np.where(raw["size"] >= LARGE_TRADE_SIZE, raw["size"], 0)
        raw["spread"] = raw["ask_price"] - raw["bid_price"]
        depth = raw["bid_size"] + raw["ask_size"]
        raw["size_imbalance"] = (raw["bid_size"] - raw["ask_size"]) / depth
        raw["mid"] = (raw["bid_price"] + raw["ask_price"]) / 2.0
        raw["microprice"] = (raw["ask_price"] * raw["bid_size"] + raw["bid_price"] * raw["ask_size"]) / depth
        raw["microprice_dev"] = raw["microprice"] - raw["mid"]
        raw["displayed_depth"] = depth
        raw["trade_size_vs_depth"] = raw["size"] / depth
        raw["depth_adjusted_signed_flow"] = raw["signed_flow"] / depth
        raw.set_index("ts", inplace=True)
        grouped = pd.DataFrame({
            f"{symbol}_signed_flow": raw["signed_flow"].resample(bucket).sum().fillna(0.0),
            f"{symbol}_buy_flow": raw["buy_flow"].resample(bucket).sum().fillna(0.0),
            f"{symbol}_sell_flow": raw["sell_flow"].resample(bucket).sum().fillna(0.0),
            f"{symbol}_contracts": raw["size"].resample(bucket).sum().fillna(0.0),
            f"{symbol}_trade_count": raw["size"].resample(bucket).count().fillna(0.0),
            f"{symbol}_avg_trade_size": raw["size"].resample(bucket).mean().fillna(0.0),
            f"{symbol}_large_volume": raw["large_volume"].resample(bucket).sum().fillna(0.0),
        })
        if symbol == PRICE_SYMBOL:
            grouped["price"] = raw["price"].resample(bucket).last().ffill()
            quote = raw[["spread", "size_imbalance", "displayed_depth", "trade_size_vs_depth", "depth_adjusted_signed_flow"]].resample(bucket).mean()
            grouped = grouped.join(quote.add_suffix("_mean"))
            grouped["trade_size_vs_depth"] = grouped["trade_size_vs_depth_mean"].fillna(0.0)
            grouped["depth_adjusted_signed_flow"] = grouped["depth_adjusted_signed_flow_mean"].fillna(0.0)
        frames.append(grouped)
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, axis=1).sort_index()
    signed_cols = [f"{s}_signed_flow" for s in SYMBOLS]
    buy_cols = [f"{s}_buy_flow" for s in SYMBOLS]
    sell_cols = [f"{s}_sell_flow" for s in SYMBOLS]
    count_cols = [f"{s}_trade_count" for s in SYMBOLS]
    contract_cols = [f"{s}_contracts" for s in SYMBOLS]
    avg_cols = [f"{s}_avg_trade_size" for s in SYMBOLS]
    large_cols = [f"{s}_large_volume" for s in SYMBOLS]
    for col in signed_cols + buy_cols + sell_cols + count_cols + contract_cols + avg_cols + large_cols:
        if col in data:
            data[col] = data[col].fillna(0.0)
    for col in ["price", "spread_mean", "size_imbalance_mean", "displayed_depth_mean"]:
        if col in data:
            data[col] = data[col].ffill()
    data = data[data["price"].notna()].copy()
    if data.empty:
        return data
    data["agg_1m"] = data.reindex(columns=signed_cols, fill_value=0.0).sum(axis=1)
    data["agg_5m"] = data["agg_1m"].rolling(5, min_periods=1).sum()
    data["agg_30m"] = data["agg_1m"].rolling(30, min_periods=1).sum()
    data["agg_cum_daily"] = data["agg_1m"].cumsum()
    data["agg_cum_daily"] = data["agg_cum_daily"] - data["agg_cum_daily"].iloc[0]
    buy = data.reindex(columns=buy_cols, fill_value=0.0).sum(axis=1)
    sell = data.reindex(columns=sell_cols, fill_value=0.0).sum(axis=1)
    data["buysell_ratio"] = (100.0 * data["agg_1m"] / (buy + sell).replace(0.0, np.nan)).ewm(span=10, ignore_na=True, min_periods=1).mean().fillna(0.0)
    data["trade_intensity"] = data.reindex(columns=count_cols, fill_value=0.0).sum(axis=1)
    data["vol_per_min"] = data.reindex(columns=contract_cols, fill_value=0.0).sum(axis=1)
    avg_num = data.reindex(columns=avg_cols, fill_value=0.0).multiply(data.reindex(columns=count_cols, fill_value=0.0).to_numpy()).sum(axis=1)
    data["avg_trade_size"] = (avg_num / data["trade_intensity"].replace(0.0, np.nan)).fillna(0.0)
    large = data.reindex(columns=large_cols, fill_value=0.0).sum(axis=1)
    data["large_trade_share"] = (large / data["vol_per_min"].replace(0.0, np.nan)).fillna(0.0)
    for symbol in SYMBOLS:
        data[f"{symbol}_agg_5m"] = data.get(f"{symbol}_signed_flow", pd.Series(0.0, index=data.index)).rolling(5, min_periods=1).sum()
    data["realized_vol"] = data["price"].diff().rolling(30, min_periods=5).std()
    data["mom_5m"] = data["price"] - data["price"].shift(5)
    et_idx = pd.DatetimeIndex(data.index).tz_convert(EASTERN)
    data["day_open_price"] = data["price"].iloc[0]
    minutes = et_idx.hour * 60 + et_idx.minute - 60
    data["minutes_since_open"] = minutes
    for col in FEATURE_SET:
        if col not in data:
            data[col] = 0.0
    return data
