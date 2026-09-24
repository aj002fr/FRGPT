"""Mechanical holdout-access checks over produced artifacts.

Scans workspace/analysis/move_exhaustion_v2/ (if it exists yet). Every
manifest.json must declare holdout_opened: false, and no manifest may list
dates inside the holdout window or the prior program's excluded dates
(exception: X0's split_manifest.json, whose job is to define the windows).
"""

import json
import re
from datetime import date
from pathlib import Path

DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _iter_manifests(analysis_root: Path):
    if not analysis_root.exists():
        return
    for path in analysis_root.rglob("manifest.json"):
        yield path


def test_all_manifests_declare_holdout_unopened(analysis_root):
    for path in _iter_manifests(analysis_root):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload.get("holdout_opened") is False, (
            f"{path} must declare holdout_opened: false"
        )


def test_no_experiment_manifest_lists_holdout_dates(analysis_root, splits_cfg):
    hold = splits_cfg["splits"]["final_holdout"]
    lo, hi = _d(hold["start"]), _d(hold["end"])
    prior_excluded = {_d(x) for x in splits_cfg["prior_program_holdout_dates_excluded_entirely"]}

    for path in _iter_manifests(analysis_root):
        if path.name == "split_manifest.json" or path.parent.name.startswith("X0"):
            continue  # X0 defines the windows; it may name them
        text = path.read_text(encoding="utf-8")
        offenders = []
        for token in DATE_RE.findall(text):
            d = _d(token)
            if lo <= d <= hi or d in prior_excluded:
                offenders.append(token)
        assert not offenders, f"{path} references locked/excluded dates: {sorted(set(offenders))}"


def test_eligible_dates_exclude_prior_program_holdout(analysis_root, splits_cfg):
    eligible_path = analysis_root / "eligible_dates.json"
    if not eligible_path.exists():
        return  # X0 not run yet
    eligible = {(_d(x)) for x in json.loads(eligible_path.read_text(encoding="utf-8"))["dates"]}
    prior_excluded = {_d(x) for x in splits_cfg["prior_program_holdout_dates_excluded_entirely"]}
    overlap = eligible & prior_excluded
    assert not overlap, f"eligible_dates.json contains prior-program holdout dates: {sorted(overlap)}"
