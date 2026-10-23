"""E002 target-free frozen fair-value formation features."""

from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

PREDICTORS = (
    "agg_cum_daily", "TY_agg_5m", "buysell_ratio", "size_imbalance_mean",
    "microprice_dev_mean", "realized_vol", "mom_5m", "trade_intensity",
    "spread_mean", "minutes_since_open",
)
ET = ZoneInfo("America/New_York")


def _finite(value):
    return value is not None and isinstance(value, (int, float))


def _solve(matrix, target):
    size = len(target)
    values = [list(matrix[index]) + [target[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(values[row][column]))
        values[column], values[pivot] = values[pivot], values[column]
        divisor = values[column][column]
        if abs(divisor) < 1e-12:
            continue
        values[column] = [item / divisor for item in values[column]]
        for row in range(size):
            if row != column:
                factor = values[row][column]
                values[row] = [
                    values[row][item] - factor * values[column][item]
                    for item in range(size + 1)
                ]
    return [values[index][-1] for index in range(size)]


def fit(rows, config):
    medians, means, scales = {}, {}, {}
    for name in PREDICTORS:
        observed = [float(row[name]) for row in rows if _finite(row.get(name))]
        if not observed:
            raise ValueError("required E002 predictor is entirely missing")
        medians[name] = median(observed)
        means[name] = sum(observed) / len(observed)
        variance = sum((value - means[name]) ** 2 for value in observed) / len(observed)
        scales[name] = variance ** 0.5 or 1.0
    dimension = len(PREDICTORS) + 1
    gram = [[0.0] * dimension for _ in range(dimension)]
    rhs = [0.0] * dimension
    for row in rows:
        if not _finite(row.get("price")) or not _finite(row.get("day_open_price")):
            continue
        vector = [1.0] + [
            ((float(row[name]) if _finite(row.get(name)) else medians[name]) - means[name])
            / scales[name] for name in PREDICTORS
        ]
        response = float(row["price"]) - float(row["day_open_price"])
        for left in range(dimension):
            rhs[left] += vector[left] * response
            for right in range(dimension):
                gram[left][right] += vector[left] * vector[right]
    ridge = float(config["ridge_lambda"])
    for index in range(1, dimension):
        gram[index][index] += ridge
    return {
        "predictors": list(PREDICTORS), "medians": medians, "means": means,
        "scales": scales, "coefficients": _solve(gram, rhs),
        "formation_lookback_minutes": int(config["formation_lookback_minutes"]),
        "ridge_lambda": ridge,
    }


def _fair_value(row, state):
    vector = [1.0]
    for name in state["predictors"]:
        value = float(row[name]) if _finite(row.get(name)) else state["medians"][name]
        vector.append((value - state["means"][name]) / state["scales"][name])
    stationary = sum(a * b for a, b in zip(vector, state["coefficients"]))
    return float(row["day_open_price"]) + stationary


def _session_date(timestamp):
    local = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(ET)
    return (local.date() + timedelta(days=1) if local.hour >= 18 else local.date()).isoformat()


def transform(rows, frozen_state):
    output, prices, fair_values = [], [], []
    previous_sign, gap_age = None, 0
    current_day = None
    lookback = frozen_state["formation_lookback_minutes"]
    for row in rows:
        day = _session_date(row["formation_ts"])
        if day != current_day:
            current_day = day
            prices, fair_values = [], []
            previous_sign, gap_age = None, 0
        price = float(row["price"])
        fair_value = _fair_value(row, frozen_state)
        gap = fair_value - price
        sign = 1 if gap > 0 else (-1 if gap < 0 else previous_sign)
        gap_age = gap_age + 1 if sign == previous_sign else 0
        previous_sign = sign
        prices.append(price)
        fair_values.append(fair_value)
        if len(prices) <= lookback:
            continue
        delta_price = price - prices[-lookback - 1]
        delta_fair = fair_value - fair_values[-lookback - 1]
        denominator = abs(delta_price) + abs(delta_fair) + 1e-12
        momentum = float(row["mom_5m"]) if _finite(row.get("mom_5m")) else 0.0
        output.append({
            "formation_ts": row["formation_ts"],
            "features": {
                "formation_gap": gap,
                "price_led_score": (abs(delta_price) - abs(delta_fair)) / denominator,
                "absolute_gap": abs(gap),
                "gap_age": float(gap_age),
                "recent_price_momentum": momentum,
            },
        })
    return output
