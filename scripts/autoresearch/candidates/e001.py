"""E001 target-free causal residual oscillation features."""

from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

PREDICTORS = ("agg_cum_daily", "TY_agg_5m", "FV_agg_5m", "TU_agg_5m", "US_agg_5m")
ET = ZoneInfo("America/New_York")
TICK = 1 / 64


def _finite(value):
    return value is not None and isinstance(value, (int, float))


def _solve(matrix, target):
    size = len(target)
    augmented = [list(matrix[i]) + [target[i]] for i in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        denominator = augmented[column][column]
        if abs(denominator) < 1e-12:
            continue
        augmented[column] = [value / denominator for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][item] - factor * augmented[column][item]
                for item in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def fit(rows, config):
    medians = {}
    means = {}
    scales = {}
    for name in PREDICTORS:
        values = [float(row[name]) for row in rows if _finite(row.get(name))]
        if not values:
            raise ValueError("required E001 predictor is entirely missing")
        medians[name] = median(values)
        means[name] = sum(values) / len(values)
        variance = sum((value - means[name]) ** 2 for value in values) / len(values)
        scales[name] = variance ** 0.5 or 1.0
    dimension = len(PREDICTORS) + 1
    gram = [[0.0] * dimension for _ in range(dimension)]
    rhs = [0.0] * dimension
    for row in rows:
        if not _finite(row.get("price")) or not _finite(row.get("day_open_price")):
            continue
        vector = [1.0] + [
            (float(row[name]) if _finite(row.get(name)) else medians[name]) - means[name]
            for name in PREDICTORS
        ]
        vector[1:] = [value / scales[name] for value, name in zip(vector[1:], PREDICTORS)]
        response = float(row["price"]) - float(row["day_open_price"])
        for left in range(dimension):
            rhs[left] += vector[left] * response
            for right in range(dimension):
                gram[left][right] += vector[left] * vector[right]
    for index in range(1, dimension):
        gram[index][index] += 1e-6
    return {
        "predictors": list(PREDICTORS), "medians": medians, "means": means,
        "scales": scales, "coefficients": _solve(gram, rhs),
        "window_minutes": int(config["window_minutes"]),
        "deadband_ticks": float(config["deadband_ticks"]),
        "dwell_minutes": int(config["dwell_minutes"]),
    }


def _session_date(timestamp):
    local = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(ET)
    return (local.date() + timedelta(days=1) if local.hour >= 18 else local.date()).isoformat()


def transform(rows, frozen_state):
    output = []
    current_day = None
    residuals = []
    flips = []
    sign = None
    proposed = None
    proposed_count = 0
    sign_age = 0
    deadband = frozen_state["deadband_ticks"] * TICK
    for row in rows:
        day = _session_date(row["formation_ts"])
        if day != current_day:
            current_day, residuals, flips = day, [], []
            sign, proposed, proposed_count, sign_age = None, None, 0, 0
        vector = [1.0]
        for name in frozen_state["predictors"]:
            value = float(row[name]) if _finite(row.get(name)) else frozen_state["medians"][name]
            vector.append((value - frozen_state["means"][name]) / frozen_state["scales"][name])
        fitted = sum(a * b for a, b in zip(vector, frozen_state["coefficients"]))
        residual = float(row["price"]) - float(row["day_open_price"]) - fitted
        residuals.append(residual)
        observed = 1 if residual >= deadband else (-1 if residual <= -deadband else sign)
        if sign is None and observed is not None:
            sign, sign_age = observed, 0
        elif observed is not None and observed != sign:
            if observed == proposed:
                proposed_count += 1
            else:
                proposed, proposed_count = observed, 1
            if proposed_count >= frozen_state["dwell_minutes"]:
                sign, sign_age = observed, 0
                flips.append(len(residuals) - 1)
                proposed, proposed_count = None, 0
        else:
            proposed, proposed_count = None, 0
            sign_age += 1
        if len(residuals) <= 60:
            continue
        window_start = len(residuals) - frozen_state["window_minutes"]
        flip_count = sum(index >= window_start for index in flips)
        momentum = float(row["mom_5m"]) if _finite(row.get("mom_5m")) else 0.0
        output.append({
            "formation_ts": row["formation_ts"],
            "features": {
                "flip_count": float(flip_count),
                "absolute_residual": abs(residual),
                "residual_age": float(sign_age),
                "residual_change": residual - residuals[-2],
                "recent_price_momentum": momentum,
            },
        })
    return output
