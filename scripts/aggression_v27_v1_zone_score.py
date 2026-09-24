"""Score frozen V1-banner zones on current-rule V2.7 trades. Analysis only."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v27_live" / "current_rule_week_replay"
WINDOWS_JSON = REPLAY_DIR / "v1_banner_windows.json"
FULL_WINDOWS_JSON = REPLAY_DIR / "v1_banner_windows_full.json"

ENTRY_STATES = ("AGGRESS", "IMPLIED_PASSIVE", "BALANCED", "QUIET")
STABILITY = ("SINGLE", "MIXED")
MIN_N = 3
BANNER_SHORT = {
    "AGGRESS_DOMINANT": "AGGRESS",
    "IMPLIED_PASSIVE_DOMINANT": "IMPLIED_PASSIVE",
    "BALANCED": "BALANCED",
    "QUIET": "QUIET",
    "AGGRESS": "AGGRESS",
    "IMPLIED_PASSIVE": "IMPLIED_PASSIVE",
}


def short_banner(state: Any) -> str | None:
    if state is None:
        return None
    text = str(state).strip()
    if text in BANNER_SHORT:
        return BANNER_SHORT[text]
    if text in ENTRY_STATES:
        return text
    return None


def window_stability(states: list[Any] | None) -> str:
    labels = [str(state).strip() for state in (states or []) if state]
    if len(set(labels)) <= 1:
        return "SINGLE"
    return "MIXED"


def pair_trades(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pair each EXIT with the preceding ENTRY for the same episode direction/time."""
    alerts = list(payload.get("alerts") or [])
    trades: list[dict[str, Any]] = []
    open_alert: dict[str, Any] | None = None
    for alert in alerts:
        kind = str(alert.get("kind") or "").upper()
        if kind == "ENTRY":
            open_alert = alert
            continue
        if kind != "EXIT" or open_alert is None:
            continue
        entry_banner = short_banner(open_alert.get("v1_banner_at_alert"))
        trades.append({
            "opened_ts": open_alert.get("alert_ts_utc") or open_alert.get("alert_ts_et"),
            "closed_ts": alert.get("alert_ts_utc") or alert.get("alert_ts_et"),
            "opened_et": open_alert.get("alert_ts_et"),
            "closed_et": alert.get("alert_ts_et"),
            "direction": alert.get("direction") or open_alert.get("direction"),
            "exit_reason": alert.get("exit_reason"),
            "ticks": float(alert["dir_pnl_trade_ticks"]) if alert.get("dir_pnl_trade_ticks") is not None else None,
            "minutes_held": alert.get("minutes_held"),
            "E": entry_banner,
            "E_raw": open_alert.get("v1_banner_at_alert"),
            "S": window_stability(open_alert.get("v1_window")),
            "X": str(alert.get("exit_reason") or ""),
            "entry_path": open_alert.get("v1_window_path"),
            "zoned": entry_banner in ENTRY_STATES,
            "tape": open_alert.get("tape") or alert.get("tape"),
        })
        open_alert = None
    return [trade for trade in trades if trade.get("ticks") is not None]


def _empty_stats() -> dict[str, Any]:
    return {
        "n_closed": 0,
        "ticks": 0.0,
        "avg_ticks": None,
        "n_win": 0,
        "n_lose": 0,
        "n_flat": 0,
        "hit_rate": None,
        "median_hold": None,
        "nominated": False,
    }


def _add(stats: dict[str, Any], trade: dict[str, Any]) -> None:
    ticks = float(trade["ticks"])
    stats["n_closed"] += 1
    stats["ticks"] += ticks
    if ticks > 0:
        stats["n_win"] += 1
    elif ticks < 0:
        stats["n_lose"] += 1
    else:
        stats["n_flat"] += 1
    holds = stats.setdefault("_holds", [])
    if trade.get("minutes_held") is not None:
        holds.append(float(trade["minutes_held"]))


def _finalize(stats: dict[str, Any], *, allow_nominate: bool = True) -> dict[str, Any]:
    n = int(stats["n_closed"])
    holds = stats.pop("_holds", [])
    stats["ticks"] = float(stats["ticks"])
    stats["avg_ticks"] = (stats["ticks"] / n) if n else None
    stats["hit_rate"] = (stats["n_win"] / n) if n else None
    stats["median_hold"] = float(sorted(holds)[len(holds) // 2]) if holds else None
    stats["nominated"] = bool(
        allow_nominate
        and n >= MIN_N
        and stats["ticks"] > 0
        and (stats["hit_rate"] or 0.0) > 0.5
    )
    return stats


def score_zones(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_e = {state: _empty_stats() for state in ENTRY_STATES}
    by_es = {f"{state}_{stab}": _empty_stats() for state in ENTRY_STATES for stab in STABILITY}
    by_x = defaultdict(_empty_stats)
    unzoned = _empty_stats()
    for trade in trades:
        _add(by_x[trade["X"] or "UNKNOWN"], trade)
        if trade.get("E") in ENTRY_STATES:
            _add(by_e[trade["E"]], trade)
            _add(by_es[f"{trade['E']}_{trade['S']}"], trade)
        else:
            _add(unzoned, trade)
    e_rows = {key: _finalize(value) for key, value in by_e.items()}
    es_rows = {key: _finalize(value) for key, value in by_es.items()}
    x_rows = {key: _finalize(value, allow_nominate=False) for key, value in by_x.items()}
    unzoned = _finalize(unzoned, allow_nominate=False)
    nominated = [
        {"cell": key, "family": "E", **value}
        for key, value in e_rows.items()
        if value["nominated"]
    ] + [
        {"cell": key, "family": "ExS", **value}
        for key, value in es_rows.items()
        if value["nominated"]
    ]
    nominated.sort(key=lambda row: (-float(row["ticks"]), -int(row["n_closed"])))
    return {
        "n_trades": len(trades),
        "n_zoned": sum(1 for trade in trades if trade.get("E") in ENTRY_STATES),
        "E": e_rows,
        "ExS": es_rows,
        "X_diagnostic": x_rows,
        "unzoned": unzoned,
        "nominated": nominated[:2],
        "nomination_rule": "n_closed>=3 and ticks>0 and hit_rate>0.5; top 2 by ticks",
        "ship_gate": False,
        "not_a_live_gate": True,
        "coverage_note": "full available B1_W10 + live week; gap Jun 6–Aug 30 has no local nowcast",
    }


def load_payload(path: Path | None = None) -> dict[str, Any]:
    target = path or (FULL_WINDOWS_JSON if FULL_WINDOWS_JSON.exists() else WINDOWS_JSON)
    return json.loads(target.read_text(encoding="utf-8"))


def tape_splits(trades: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get("tape") or "unknown")].append(trade)
    out: dict[str, Any] = {}
    for tape, rows in groups.items():
        scored = score_zones(rows)
        out[tape] = {
            "n_trades": scored["n_trades"],
            "nominated": scored["nominated"],
            "E": {
                key: {"n_closed": val["n_closed"], "ticks": val["ticks"], "hit_rate": val["hit_rate"]}
                for key, val in scored["E"].items()
            },
            "ExS": {
                key: {
                    "n_closed": val["n_closed"],
                    "ticks": val["ticks"],
                    "hit_rate": val["hit_rate"],
                    "nominated": val["nominated"],
                }
                for key, val in scored["ExS"].items()
                if val["n_closed"]
            },
        }
    return out


def write_outputs(summary: dict[str, Any], trades: list[dict[str, Any]], out_dir: Path = REPLAY_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for family, block in (("E", summary["E"]), ("ExS", summary["ExS"])):
        for cell, stats in block.items():
            rows.append({"family": family, "cell": cell, **{k: v for k, v in stats.items() if k != "_holds"}})
    lines = ["family,cell,n_closed,ticks,avg_ticks,n_win,n_lose,n_flat,hit_rate,median_hold,nominated"]
    for row in rows:
        lines.append(
            f"{row['family']},{row['cell']},{row['n_closed']},{row['ticks']},"
            f"{row['avg_ticks']},{row['n_win']},{row['n_lose']},{row['n_flat']},"
            f"{row['hit_rate']},{row['median_hold']},{row['nominated']}"
        )
    (out_dir / "zone_scores.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = dict(summary)
    payload["by_tape"] = tape_splits(trades)
    payload["trades"] = trades
    (out_dir / "zone_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "zone_readout.md").write_text(_markdown(payload, trades), encoding="utf-8")


def _markdown(summary: dict[str, Any], trades: list[dict[str, Any]]) -> str:
    noms = summary.get("nominated") or []
    if not noms:
        nom_block = "No cell met n_closed >= 3, ticks > 0, and hit rate > 50%. **No zone is nominated.**"
    else:
        bits = [f"- `{row['cell']}` ({row['family']}): n={row['n_closed']}, ticks={row['ticks']:+.1f}, hit={100 * row['hit_rate']:.0f}%" for row in noms]
        nom_block = "Nominated (at most two, highest total ticks):\n\n" + "\n".join(bits)
    losing = []
    for family, block in (("E", summary["E"]), ("ExS", summary["ExS"])):
        for cell, stats in block.items():
            if stats["n_closed"] and stats["ticks"] < 0:
                losing.append(f"- `{cell}` ({family}): n={stats['n_closed']}, ticks={stats['ticks']:+.1f}")
    lose_block = "\n".join(losing) if losing else "- None with n>0 and negative ticks."
    near = []
    for family, block in (("E", summary["E"]), ("ExS", summary["ExS"])):
        for cell, stats in block.items():
            if (
                stats["n_closed"]
                and stats["n_closed"] < MIN_N
                and stats["ticks"] > 0
                and (stats["hit_rate"] or 0.0) > 0.5
            ):
                near.append(
                    f"- `{cell}` ({family}): n={stats['n_closed']} (<{MIN_N}), "
                    f"ticks={stats['ticks']:+.1f}, hit={100 * stats['hit_rate']:.0f}%"
                )
    near_block = ("\n\nNear-miss (profitable but n < 3):\n\n" + "\n".join(near)) if near else ""
    failed_hit = []
    for family, block in (("E", summary["E"]), ("ExS", summary["ExS"])):
        for cell, stats in block.items():
            if stats["n_closed"] >= MIN_N and stats["ticks"] > 0 and not stats["nominated"]:
                hit = "—" if stats["hit_rate"] is None else f"{100 * stats['hit_rate']:.0f}%"
                failed_hit.append(
                    f"- `{cell}` ({family}): n={stats['n_closed']}, ticks={stats['ticks']:+.1f}, hit={hit}"
                )
    fail_hit_block = (
        ("\n\nPositive ticks but hit rate not > 50%:\n\n" + "\n".join(failed_hit)) if failed_hit else ""
    )
    unz = summary.get("unzoned") or {}
    unz_line = (
        f"Unzoned (banner not in frozen E, e.g. WARMING): n={unz.get('n_closed', 0)}, "
        f"ticks={float(unz.get('ticks') or 0):+.1f}. Not a nomination cell."
    )
    def _cell_table(title: str, block: dict[str, Any]) -> list[str]:
        lines = [f"| {title} | n | ticks | W/L/F | hit | nominated |", "| --- | ---: | ---: | ---: | ---: | --- |"]
        for cell, stats in block.items():
            hit = "—" if stats["hit_rate"] is None else f"{100 * stats['hit_rate']:.0f}%"
            lines.append(
                f"| {cell} | {stats['n_closed']} | {stats['ticks']:+.1f} | "
                f"{stats['n_win']}/{stats['n_lose']}/{stats['n_flat']} | {hit} | {stats['nominated']} |"
            )
        return lines

    e_lines = _cell_table("E", summary["E"])
    es_lines = _cell_table("E×S", summary["ExS"])
    return (
        "# V2.7 × V1 zone readout (analysis only)\n\n"
        f"Current-rule V2.7 paper last-print trades: **{summary['n_trades']}** "
        f"({summary.get('coverage_note') or 'full available tape'}). "
        "Zones frozen before scoring: V1 banner at ENTRY (`E`) and ENTRY±5m stability (`S` = SINGLE vs MIXED). "
        "Exit type is diagnostic only.\n\n"
        f"{nom_block}{near_block}{fail_hit_block}\n\n"
        "## Losing cells\n\n"
        f"{lose_block}\n\n"
        f"{unz_line}\n\n"
        "This is **not** a live gate and **not** after-cost. `ship_gate` false. "
        "Do not change 5063 or 5050 from this tape.\n\n"
        "## E cells\n\n"
        + "\n".join(e_lines)
        + "\n\n## E×S cells\n\n"
        + "\n".join(es_lines)
        + "\n\n## Tape split (same frozen cells)\n\n"
        + _tape_md(summary)
        + "\n\n## Exit type (diagnostic only, not a nomination key)\n\n"
        + "\n".join(
            [f"- `{cell}`: n={stats['n_closed']}, ticks={stats['ticks']:+.1f}, hit="
             f"{'—' if stats['hit_rate'] is None else f'{100 * stats['hit_rate']:.0f}%'}"
             for cell, stats in (summary.get("X_diagnostic") or {}).items()]
            or ["- No exits."]
        )
        + "\n"
    )


def _tape_md(summary: dict[str, Any]) -> str:
    blocks = summary.get("by_tape") or {}
    if not blocks:
        return "- No tape labels."
    lines = []
    for tape, block in blocks.items():
        noms = block.get("nominated") or []
        nom_txt = ", ".join(f"`{row['cell']}` ({row['ticks']:+.1f}, n={row['n_closed']})" for row in noms) or "none"
        lines.append(f"- **{tape}**: n={block['n_trades']}; nominated: {nom_txt}")
        for cell, stats in (block.get("E") or {}).items():
            if not stats.get("n_closed"):
                continue
            hit = "—" if stats.get("hit_rate") is None else f"{100 * stats['hit_rate']:.0f}%"
            lines.append(f"  - E `{cell}`: n={stats['n_closed']}, ticks={stats['ticks']:+.1f}, hit={hit}")
    return "\n".join(lines)


def main() -> None:
    payload = load_payload()
    trades = pair_trades(payload)
    summary = score_zones(trades)
    write_outputs(summary, trades)
    print(json.dumps({"n_trades": summary["n_trades"], "nominated": summary["nominated"]}, indent=2))


if __name__ == "__main__":
    main()
