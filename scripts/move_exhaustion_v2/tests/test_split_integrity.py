"""Mechanical integrity of the chronological split design."""

from datetime import date


def _d(s: str) -> date:
    return date.fromisoformat(s)


def test_splits_are_chronological_and_disjoint(splits_cfg):
    disc = splits_cfg["splits"]["discovery"]
    val = splits_cfg["splits"]["validation"]
    hold = splits_cfg["splits"]["final_holdout"]
    for w in (disc, val, hold):
        assert _d(w["start"]) <= _d(w["end"])
    assert _d(disc["end"]) < _d(val["start"]), "discovery must end before validation starts"
    assert _d(val["end"]) < _d(hold["start"]), "validation must end before holdout starts"


def test_holdout_is_locked_and_unopened(splits_cfg):
    assert splits_cfg["splits"]["final_holdout"]["locked"] is True
    assert splits_cfg["holdout_opened"] is False


def test_embargo_floor(splits_cfg):
    assert splits_cfg["embargo_trading_days"] >= 5
    assert splits_cfg["embargo_policy"].startswith("symmetric:")
    assert splits_cfg["embargo_explicit_user_approval_utc"] == "2026-08-12"


def test_grouping_rules(splits_cfg):
    g = splits_cfg["grouping"]
    assert g["same_episode_same_fold"] is True
    assert g["same_date_same_fold"] is True
    assert g["inference_clustering"] == "date"


def test_prior_program_holdout_dates_outside_all_splits(splits_cfg):
    """The old program's 10 locked holdout dates must not fall inside any V2 split."""
    excluded = [_d(x) for x in splits_cfg["prior_program_holdout_dates_excluded_entirely"]]
    assert len(excluded) == 10
    for w in splits_cfg["splits"].values():
        lo, hi = _d(w["start"]), _d(w["end"])
        inside = [d for d in excluded if lo <= d <= hi]
        assert not inside, f"prior-program holdout dates inside split window {w}: {inside}"


def test_label_horizon_far_below_embargo(splits_cfg, labels_cfg):
    """Assumption A6: max label horizon (minutes) must be tiny vs the embargo (days)."""
    max_h_minutes = max(labels_cfg["grid"]["H_minutes"])
    embargo_minutes = splits_cfg["embargo_trading_days"] * 24 * 60
    assert max_h_minutes * 10 < embargo_minutes


def test_era_slices_cover_all_split_years(splits_cfg):
    eras = splits_cfg["era_slices"]
    lo = min(_d(e["start"]) for e in eras)
    hi = max(_d(e["end"]) for e in eras)
    assert lo <= _d(splits_cfg["splits"]["discovery"]["start"])
    assert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])
ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])

ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])


ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])



ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])
])



ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])
"])
])



ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])
d"])
"])
])



ert hi >= _d(splits_cfg["splits"]["final_holdout"]["end"])
