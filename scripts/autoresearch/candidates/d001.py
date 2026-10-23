"""D001 target-free dominance proxy and volatility context features."""

from statistics import median

FEATURES = ("buysell_ratio", "large_trade_share", "trade_intensity", "realized_vol")


def _observed(rows, name):
    values = [
        float(row[name]) for row in rows
        if row.get(name) is not None and isinstance(row.get(name), (int, float))
    ]
    if not values:
        raise ValueError("required D001 feature is entirely missing")
    return sorted(values)


def _quantile(values, probability):
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def fit(rows, config):
    del config
    observed = {name: _observed(rows, name) for name in FEATURES}
    return {
        "medians": {name: median(values) for name, values in observed.items()},
        "boundaries": {
            "buysell_ratio": [
                _quantile(observed["buysell_ratio"], 0.1),
                _quantile(observed["buysell_ratio"], 0.9),
            ],
            **{
                name: [_quantile(observed[name], 1 / 3), _quantile(observed[name], 2 / 3)]
                for name in ("large_trade_share", "trade_intensity", "realized_vol")
            },
        },
    }


def transform(rows, frozen_state):
    output = []
    for row in rows:
        values = {
            name: (
                float(row[name])
                if row.get(name) is not None and isinstance(row.get(name), (int, float))
                else frozen_state["medians"][name]
            )
            for name in FEATURES
        }
        output.append({
            "formation_ts": row["formation_ts"],
            "features": {
                "buysell_ratio": values["buysell_ratio"],
                "large_trade_share": values["large_trade_share"],
                "trade_intensity": values["trade_intensity"],
                "realized_vol_context": values["realized_vol"],
            },
        })
    return output
