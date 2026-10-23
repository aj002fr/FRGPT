from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from scripts.macro_path_navigator.events import cycle_payload
from scripts.macro_path_navigator.headlines import load_headlines

RISK_UP_WORDS = (
    "attack", "strike", "missile", "retaliat", "violation", "escalat", "prepared",
    "operation", "sanction", "threat", "hormuz", "nuclear", "ceasefire violation",
)
RISK_DOWN_WORDS = (
    "talk", "meeting", "negotiat", "deal", "mou", "mediator", "diplomat",
    "ceasefire", "de-escalat", "agreement",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _headline_score(text: str) -> tuple[int, list[str]]:
    lower = text.lower()
    reasons: list[str] = []
    score = 0
    for word in RISK_UP_WORDS:
        if word in lower:
            score += 1
            reasons.append(f"risk-up keyword: {word}")
    for word in RISK_DOWN_WORDS:
        if word in lower:
            score -= 1
            reasons.append(f"risk-down keyword: {word}")
    return score, reasons[:4]


def _flow_context(runway: Dict[str, Any]) -> Dict[str, Any]:
    rows = (runway.get("flow") or {}).get("rows") or []
    recent = rows[-300:]
    buys = sum(float(row.get("size") or 0) for row in recent if str(row.get("side")).upper() == "B")
    sells = sum(float(row.get("size") or 0) for row in recent if str(row.get("side")).upper() == "A")
    total = buys + sells
    imbalance = (buys - sells) / total if total else None
    if imbalance is None:
        label = "flow unavailable"
    elif imbalance > 0.2:
        label = "buy-aggressive flow"
    elif imbalance < -0.2:
        label = "sell-aggressive flow"
    else:
        label = "balanced flow"
    return {"label": label, "imbalance": imbalance, "recent_rows": len(recent)}


def _event_watch_signals(events: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    future = []
    for row in events:
        try:
            ts = _parse_ts(row["ts"])
        except Exception:
            continue
        if ts >= now and ts <= now + timedelta(days=7):
            future.append((ts, row))
    for ts, row in sorted(future, key=lambda item: item[0])[:5]:
        hours = (ts - now).total_seconds() / 3600
        tier = row.get("tier")
        out.append({
            "kind": "event_watch",
            "severity": "watch",
            "title": f"Upcoming {row.get('theme', 'macro')} event: {row.get('name')}",
            "summary": f"{hours:.1f}h away. Surprise status: {row.get('surprise_status', 'n/a')}.",
            "confidence": "medium" if tier in (1, "1") else "low",
            "timestamp": row.get("ts"),
            "reasons": ["Scheduled in current cycle", "Forward band should widen around event window"],
            "assumption": "No actual release value yet; this is an event-risk watch, not a directional forecast.",
        })
    return out


def build_signals(runway: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now()
    start = (now - timedelta(hours=6)).isoformat()
    end = now.isoformat()
    headline_payload = load_headlines(start=start, end=end, limit=500)
    headlines = headline_payload.get("rows") or []
    flow = _flow_context(runway)

    scored = []
    for row in headlines:
        score, reasons = _headline_score(row.get("text") or "")
        if score:
            scored.append((abs(score), score, row, reasons))
    scored.sort(key=lambda item: (item[0], item[2].get("ts", "")), reverse=True)

    signals: List[Dict[str, Any]] = []
    for _, score, row, reasons in scored[:5]:
        direction = "geopolitical risk-up / possible flight-to-quality" if score > 0 else "diplomacy/de-escalation watch"
        severity = "risk_up" if score > 0 else "risk_down"
        flow_note = flow["label"]
        signals.append({
            "kind": "headline",
            "severity": severity,
            "title": direction,
            "summary": row.get("text"),
            "confidence": "medium" if abs(score) >= 2 else "low",
            "timestamp": row.get("ts"),
            "source": row.get("source"),
            "reasons": [*reasons, f"current flow: {flow_note}"],
            "assumption": "Keyword-based first pass; treat as a watch signal until price/flow confirms.",
        })

    cycle = cycle_payload()
    signals.extend(_event_watch_signals(cycle.get("events", []), now))
    counts = Counter(signal["kind"] for signal in signals)
    return {
        "generated_at": now.isoformat(),
        "signals": signals[:10],
        "flow_context": flow,
        "headline_window": {"start": start, "end": end, "high_signal_count": len(headlines)},
        "counts": dict(counts),
    }

