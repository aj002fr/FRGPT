"""Mechanical integrity of the autoresearch loop (journal, cycles, tiers).

Enforced rules:
- journal is valid JSONL with sequential seq, valid actor/event vocabulary;
- every cycle folder has BRIEF.md and meta.json (RESULT/FEEDBACK required once
  status advances past RUNNING);
- EXPLORATORY cycles use discovery data only — their data_window_used must lie
  inside the discovery window and never reference validation/holdout dates;
- CONFIRMATORY cycles must cite an approved frozen contract;
- every cycle is journaled; holdout_opened is false everywhere.
"""

import json
import re
from datetime import date
from pathlib import Path

import pytest

from loop_journal import read_entries, validate

CYCLE_DIR_RE = re.compile(r"^C(\d{3,})_[a-z0-9_]+$")
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _cycles_root(pkg_root: Path) -> Path:
    return pkg_root / "research" / "loop" / "cycles"


def _iter_cycles(pkg_root: Path):
    root = _cycles_root(pkg_root)
    if not root.exists():
        return
    for folder in sorted(root.iterdir()):
        if folder.is_dir():
            yield folder


def test_journal_is_valid():
    problems = validate(read_entries())
    assert not problems, "\n".join(problems)


def test_journal_has_governance_genesis():
    entries = read_entries()
    assert entries, "journal must exist with at least the genesis governance entry"
    assert entries[0]["event"] == "governance"


def test_cycle_folders_well_formed(pkg_root):
    seen_nums = []
    for folder in _iter_cycles(pkg_root):
        assert CYCLE_DIR_RE.match(folder.name), f"bad cycle folder name: {folder.name}"
        seen_nums.append(int(folder.name.split("_")[0][1:]))
        assert (folder / "BRIEF.md").exists(), f"{folder.name}: missing BRIEF.md"
        assert (folder / "meta.json").exists(), f"{folder.name}: missing meta.json"
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        for key in ("cycle", "tier", "status", "holdout_opened", "advisor_decision"):
            assert key in meta, f"{folder.name}: meta.json missing '{key}'"
        assert meta["holdout_opened"] is False
        assert meta["tier"] in {"EXPLORATORY", "CONFIRMATORY"}
        if meta["status"] in {"RESULTED", "REVIEWED"}:
            assert (folder / "RESULT.md").exists(), f"{folder.name}: status {meta['status']} but no RESULT.md"
            # Provenance + quantitative trail (AGENT_ROSTER.md / LOOP_PROTOCOL.md §3)
            assert meta.get("worker_model"), (
                f"{folder.name}: completed cycle must record worker_model"
            )
            assert "headline_metrics" in meta, (
                f"{folder.name}: completed cycle must record headline_metrics "
                "(may be {} only for pure-infrastructure cycles, with a reason key)"
            )
        if meta["status"] == "REVIEWED":
            assert (folder / "FEEDBACK.md").exists(), f"{folder.name}: REVIEWED but no FEEDBACK.md"
            # Dual-advisor panel (AGENT_ROSTER.md): two independent opinions,
            # or a documented solo review (which can never promote).
            advisors = meta.get("advisor_models") or []
            assert isinstance(advisors, list) and len(advisors) >= 1, (
                f"{folder.name}: REVIEWED cycle must record advisor_models"
            )
            if len(advisors) < 2:
                assert meta.get("solo_review_reason"), (
                    f"{folder.name}: single-advisor review requires solo_review_reason"
                )
                assert meta.get("advisor_decision") != "PROMOTE_REQUEST", (
                    f"{folder.name}: solo reviews may never PROMOTE_REQUEST "
                    "(AGENT_ROSTER consolidation rules)"
                )
    assert seen_nums == sorted(seen_nums), "cycle numbers must be sequential/ordered"


def test_exploratory_cycles_use_discovery_data_only(pkg_root, splits_cfg):
    disc = splits_cfg["splits"]["discovery"]
    lo, hi = _d(disc["start"]), _d(disc["end"])
    val = splits_cfg["splits"]["validation"]
    hold = splits_cfg["splits"]["final_holdout"]
    forbidden_lo = _d(val["start"])
    forbidden_hi = _d(hold["end"])

    for folder in _iter_cycles(pkg_root):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta["tier"] != "EXPLORATORY":
            continue
        window = meta.get("data_window_used")
        assert window, f"{folder.name}: exploratory cycle must declare data_window_used"
        assert lo <= _d(window["start"]) and _d(window["end"]) <= hi, (
            f"{folder.name}: exploratory data window {window} outside discovery {disc}"
        )
        # Belt and braces: no validation/holdout date literals in worker outputs.
        for doc in ("RESULT.md", "meta.json"):
            path = folder / doc
            if not path.exists():
                continue
            for token in DATE_RE.findall(path.read_text(encoding="utf-8")):
                d = _d(token)
                assert not (forbidden_lo <= d <= forbidden_hi), (
                    f"{folder.name}/{doc} references validation/holdout date {token}"
                )


def test_confirmatory_cycles_cite_frozen_contract(pkg_root):
    for folder in _iter_cycles(pkg_root):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta["tier"] != "CONFIRMATORY":
            continue
        contract_ref = meta.get("contract")
        assert contract_ref, f"{folder.name}: confirmatory cycle must cite its frozen contract"
        contract_path = pkg_root / contract_ref
        assert contract_path.exists(), f"{folder.name}: contract not found: {contract_ref}"
        text = contract_path.read_text(encoding="utf-8")
        assert "**FROZEN**" in text and "RETIRED_TO_BACKLOG" not in text, (
            f"{folder.name}: cited contract is not an active FROZEN contract"
        )


def test_every_cycle_is_journaled(pkg_root):
    journaled_cycles = {e["cycle"] for e in read_entries() if e["cycle"]}
    for folder in _iter_cycles(pkg_root):
        cycle_id = folder.name.split("_")[0]
        assert cycle_id in journaled_cycles, f"{cycle_id} has a folder but no journal entries"


def test_retrospective_cadence(pkg_root):
    """LOOP_PROTOCOL §9: one retro per 5 closed cycles (closed = decision recorded)."""
    closed = 0
    for folder in _iter_cycles(pkg_root):
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        if meta.get("advisor_decision") in {"ITERATE", "PARKED", "PROMOTE_REQUEST", "ESCALATED"}:
            closed += 1
    retros_dir = pkg_root / "research" / "loop" / "retros"
    n_retros = len(list(retros_dir.glob("R*.md"))) if retros_dir.exists() else 0
    required = closed // 5
    assert n_retros >= required, (
        f"{closed} closed cycles require >= {required} retrospectives "
        f"(found {n_retros}); write research/loop/retros/R###.md per LOOP_PROTOCOL §9"
    )


def test_retros_are_journaled(pkg_root):
    retros_dir = pkg_root / "research" / "loop" / "retros"
    if not retros_dir.exists():
        return
    retro_events = [e for e in read_entries() if e["event"] == "retro"]
    n_retros = len(list(retros_dir.glob("R*.md")))
    assert len(retro_events) >= n_retros, (
        f"{n_retros} retro files but only {len(retro_events)} 'retro' journal entries"
    )


def test_retired_contracts_carry_no_evidentiary_status(pkg_root):
    """X2-X10 must be marked RETIRED_TO_BACKLOG; X0/X1 must remain FROZEN."""
    for folder in sorted((pkg_root / "experiments").glob("X*")):
        if not folder.is_dir():
            continue
        text = (folder / "CONTRACT.md").read_text(encoding="utf-8")
        xnum = int(re.match(r"X(\d+)", folder.name).group(1))
        if xnum in (0, 1):
            assert "**FROZEN**" in text, f"{folder.name} must remain FROZEN"
            assert "RETIRED_TO_BACKLOG" not in text
        else:
            assert "RETIRED_TO_BACKLOG" in text, f"{folder.name} must be RETIRED_TO_BACKLOG"
