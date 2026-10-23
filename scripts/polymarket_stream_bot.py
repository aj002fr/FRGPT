#!/usr/bin/env python3
"""
Polymarket market tracking workflow for Telegram bot.

Features:
- /pm_track <query> with inline multi-select confirmation
- /pm_list and /pm_untrack management commands
- 30s background polling loop writing to polymarket_markets.db
- Alerting with approve/reject callbacks and optional forwarding
"""

from __future__ import annotations

import asyncio
import hashlib
import html
from io import BytesIO
import logging
import os
import random
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, ContextTypes

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts.telegram_bot_config import ALLOWED_CHAT_IDS
from src.core.polymarket_stream_store import (
    count_active_subscriptions_for_condition,
    create_alert_label,
    create_pending_selection,
    deactivate_subscription,
    delete_pending_selection,
    get_alert_label,
    get_alert_state,
    get_pending_selection,
    get_query_message_state,
    init_db,
    list_all_active_subscriptions,
    list_subscriptions,
    mark_alert_forwarded,
    set_alert_label,
    set_alert_message_id,
    set_query_message_delivery,
    toggle_pending_selected_index,
    update_alert_state,
    upsert_query_message_state,
    upsert_subscription,
)
from src.servers.polymarket.poll_midpoint import (
    ensure_polling_tables,
    fetch_midpoint,
    get_default_db_path,
    insert_timeseries_row,
    mark_market_inactive,
    query_recent_timeseries,
    upsert_tracked_market,
    utc_now_iso,
)
from src.servers.polymarket.search_markets import (
    call_polymarket_search_api,
    parse_public_search_response,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")

PM_TRACK_MAX_MARKETS = int(os.getenv("POLYMARKET_TRACK_MAX_MARKETS", "8"))
POLL_INTERVAL_S = int(os.getenv("POLYMARKET_POLL_INTERVAL_S", "30"))
SIG_CHANGE_MIN_ABS_PP = float(os.getenv("POLYMARKET_MIN_ABS_PP", "3.0"))
SIG_CHANGE_REL_FACTOR = float(os.getenv("POLYMARKET_REL_FACTOR", "0.08"))
TREND_STREAK_POLLS = int(os.getenv("POLYMARKET_TREND_STREAK_POLLS", "10"))
TREND_TOTAL_MOVE_THRESHOLD = float(os.getenv("POLYMARKET_TREND_TOTAL_MOVE_THRESHOLD", "0.03"))
CHART_LOOKBACK_HOURS = 24
CHART_MAX_POINTS = 240
# Routing policy requested by user:
# - First Polymarket alerts always go to PM_ALERT_CHAT_ID.
# - Delivery uses chart snapshots with compact captions.
PM_ALERT_CHAT_ID = -5157838101
PM_PUBLIC_CHAT_ID = os.getenv("POLYMARKET_PUBLIC_CHAT_ID")


def _load_macro_chat_ids() -> List[int]:
    raw = os.getenv("POLYMARKET_CHAT_ID") or os.getenv("POLYMARKET_CHAT_IDS") or ""
    if not raw.strip():
        return ALLOWED_CHAT_IDS
    return [int(cid.strip()) for cid in raw.split(",") if cid.strip()]


PM_CHAT_IDS = _load_macro_chat_ids()


@dataclass
class EvalState:
    last_mid: Optional[float]
    last_alert_mid: Optional[float]
    last_alert_ts: Optional[datetime]
    streak_dir: Optional[int]
    streak_len: int
    streak_start_mid: Optional[float]


def is_authorized(chat_id: int) -> bool:
    if not PM_CHAT_IDS or PM_CHAT_IDS == [0]:
        return False
    return chat_id in PM_CHAT_IDS


def _escape(text: str) -> str:
    return html.escape(text or "")


def _extract_yes_no_tokens(market: Dict) -> Tuple[Optional[str], Optional[str]]:
    token_ids = market.get("clob_token_ids") or []
    yes_token = token_ids[0] if len(token_ids) > 0 else None
    no_token = token_ids[1] if len(token_ids) > 1 else None
    return yes_token, no_token


def _search_markets(query: str, limit: int = PM_TRACK_MAX_MARKETS) -> List[Dict]:
    response = call_polymarket_search_api(query, limit_per_type=max(limit, 5))
    markets = parse_public_search_response(response, query)
    out: List[Dict] = []
    for m in markets:
        yes_token, _ = _extract_yes_no_tokens(m)
        if not m.get("market_id") or not yes_token:
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def _query_group_id_from_text(query_text: str) -> str:
    normalized = re.sub(r"\s+", " ", (query_text or "").strip().lower())
    if not normalized:
        return "query:default"
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"query:{digest}"


def _build_market_lines(markets: List[Dict], selected: Set[int]) -> List[str]:
    lines: List[str] = []
    for i, market in enumerate(markets, start=1):
        yes_price = market.get("prices", {}).get("Yes")
        if yes_price is None:
            # Fall back to first outcome if "Yes" key is not available.
            price_vals = market.get("prices", {})
            yes_price = next(iter(price_vals.values()), None) if isinstance(price_vals, dict) else None
        price_txt = ""
        if yes_price is not None:
            try:
                price_txt = f" | Yes: {float(yes_price) * 100:.1f}%"
            except (TypeError, ValueError):
                price_txt = ""
        mark = "✅ " if (i - 1) in selected else ""
        title = _escape(str(market.get("title") or "Untitled market"))
        lines.append(f"{mark}<b>{i}.</b> {title}{price_txt}")
    return lines


def _build_selection_keyboard(selection_id: str, markets_len: int, selected: Set[int]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for idx in range(markets_len):
        checked = "✅" if idx in selected else "☐"
        btn = InlineKeyboardButton(
            text=f"{checked} #{idx + 1}",
            callback_data=f"pm_pick:{selection_id}:{idx}",
        )
        row.append(btn)
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton("Done", callback_data=f"pm_done:{selection_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"pm_cancel:{selection_id}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _build_pm_alert_keyboard(alert_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"pm_approve:{alert_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"pm_reject:{alert_id}"),
            ]
        ]
    )


def _selection_message_text(selection: Dict) -> str:
    markets = selection.get("markets", [])
    selected = {int(i) for i in selection.get("selected_indices", [])}
    lines = [
        "<b>Polymarket matches</b>",
        f"Query: <i>{_escape(selection.get('user_query', ''))}</i>",
        "Select one or more markets, then tap <b>Done</b>:",
        "",
        *_build_market_lines(markets, selected),
    ]
    if selected:
        picked = ", ".join(str(i + 1) for i in sorted(selected))
        lines.append("")
        lines.append(f"Selected: {picked}")
    return "\n".join(lines)


def _iso_to_dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _eval_state_from_row(state_row: Dict) -> EvalState:
    return EvalState(
        last_mid=state_row.get("last_mid"),
        last_alert_mid=state_row.get("last_alert_mid"),
        last_alert_ts=_iso_to_dt(state_row.get("last_alert_ts")),
        streak_dir=state_row.get("streak_dir"),
        streak_len=int(state_row.get("streak_len") or 0),
        streak_start_mid=state_row.get("streak_start_mid"),
    )


def _direction(new_mid: float, prev_mid: float) -> Optional[int]:
    if new_mid > prev_mid:
        return 1
    if new_mid < prev_mid:
        return -1
    return None


def _format_user_label(user) -> str:
    if user is None:
        return "unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name or "unknown"


async def _send_with_retry(
    send_coro_factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await send_coro_factory()
        except (TimedOut, NetworkError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("Telegram send timed out; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("Telegram send retry loop exhausted without an exception")


def _short_market_label(title: str, max_len: int = 34) -> str:
    clean = re.sub(r"\s+", " ", (title or "").strip())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3].rstrip() + "..."


def _significant_change_threshold_pp(mid: float) -> float:
    return max(SIG_CHANGE_MIN_ABS_PP, abs(mid) * SIG_CHANGE_REL_FACTOR * 100.0)


def _reason_label(reason: str) -> str:
    return {
        "significant_change": "Sig change",
        "trend": "Trend",
    }.get(reason, reason.replace("_", " ").title())


def _change_verb(delta_pp: float) -> str:
    magnitude = abs(delta_pp)
    if delta_pp >= 0:
        if magnitude >= 10.0:
            return "surged"
        if magnitude >= 5.0:
            return "jumped"
        return "edged up"
    if magnitude >= 10.0:
        return "plunged"
    if magnitude >= 5.0:
        return "fell"
    return "edged down"


def _format_market_move(market: Dict[str, object], *, include_current: bool = True) -> str:
    title = _escape(str(market.get("title") or "Untitled market"))
    delta_pp = market.get("delta_pp")
    mid = market.get("mid")
    if delta_pp is None:
        return f'"{title}" has no prior baseline yet'

    delta_val = float(delta_pp)
    move_text = f'"{title}" {_change_verb(delta_val)} {delta_val:+.1f}pp'
    if include_current and mid is not None:
        move_text += f" to {float(mid) * 100:.1f}%"
    return move_text


def _build_change_summary(reason: str, markets: List[Dict[str, object]]) -> str:
    markets_with_delta = [m for m in markets if m.get("delta_pp") is not None]
    if not markets_with_delta:
        return "What changed: Initial snapshot - no prior data yet."

    if reason == "significant_change":
        movers = [m for m in markets_with_delta if m.get("reason") == "significant_change"] or markets_with_delta
        movers = sorted(movers, key=lambda item: abs(float(item.get("delta_pp") or 0.0)), reverse=True)
        if len(movers) == 1:
            return f"What changed: {_format_market_move(movers[0])}"
        first = _format_market_move(movers[0])
        second = _format_market_move(movers[1], include_current=False)
        return f"What changed: {len(movers)} markets moved sharply. Biggest: {first}. Next: {second}."

    if reason == "trend":
        movers = [m for m in markets_with_delta if m.get("reason") == "trend"] or markets_with_delta
        movers = sorted(movers, key=lambda item: abs(float(item.get("delta_pp") or 0.0)), reverse=True)
        dirs = {1 if float(m.get("delta_pp") or 0.0) > 0 else -1 for m in movers if float(m.get("delta_pp") or 0.0) != 0.0}
        if dirs == {1}:
            trend_text = "trending higher"
        elif dirs == {-1}:
            trend_text = "trending lower"
        else:
            trend_text = "trending in mixed directions"
        if len(movers) == 1:
            return f"What changed: {_format_market_move(movers[0])} and is {trend_text}."
        highlights = ", ".join(_format_market_move(m, include_current=False) for m in movers[:2])
        return f"What changed: {len(movers)} markets are {trend_text} - {highlights}."

    largest = max(markets_with_delta, key=lambda item: abs(float(item.get("delta_pp") or 0.0)))
    return f"What changed: No significant moves. Largest: {_format_market_move(largest)}."


def _evaluate_alert_conditions(mid: float, state: EvalState, now: datetime) -> Tuple[Optional[str], EvalState]:
    reason: Optional[str] = None
    updated = EvalState(
        last_mid=mid,
        last_alert_mid=state.last_alert_mid,
        last_alert_ts=state.last_alert_ts,
        streak_dir=state.streak_dir,
        streak_len=state.streak_len,
        streak_start_mid=state.streak_start_mid,
    )

    if state.last_mid is None:
        updated.streak_dir = None
        updated.streak_len = 0
        updated.streak_start_mid = mid
    else:
        move_dir = _direction(mid, state.last_mid)
        if move_dir is None:
            updated.streak_dir = state.streak_dir
            updated.streak_len = state.streak_len
            updated.streak_start_mid = state.streak_start_mid
        elif state.streak_dir == move_dir and state.streak_len > 0:
            updated.streak_dir = move_dir
            updated.streak_len = state.streak_len + 1
            updated.streak_start_mid = state.streak_start_mid if state.streak_start_mid is not None else state.last_mid
        else:
            updated.streak_dir = move_dir
            updated.streak_len = 1
            updated.streak_start_mid = state.last_mid

    if updated.last_alert_mid is None:
        updated.last_alert_mid = mid
        updated.last_alert_ts = now
        return reason, updated

    if abs(mid - updated.last_alert_mid) * 100.0 >= _significant_change_threshold_pp(mid):
        reason = "significant_change"
    elif (
        updated.streak_len >= TREND_STREAK_POLLS
        and updated.streak_start_mid is not None
        and abs(mid - updated.streak_start_mid) >= TREND_TOTAL_MOVE_THRESHOLD
    ):
        reason = "trend"

    if reason:
        updated.last_alert_mid = mid
        updated.last_alert_ts = now
        updated.streak_dir = None
        updated.streak_len = 0
        updated.streak_start_mid = mid

    return reason, updated


def _format_group_update_text(
    *,
    query_text: str,
    reason: str,
    markets: List[Dict[str, object]],
) -> str:
    sorted_markets = sorted(
        markets,
        key=lambda item: abs(float(item.get("delta_pp") or 0.0)),
        reverse=True,
    )
    movers = [m for m in sorted_markets if m.get("reason") == reason] or sorted_markets
    lines = ["<b>Polymarket Odds Update</b>"]
    for market in movers:
        title = _escape(str(market.get("title") or "Untitled market"))
        mid = market.get("mid")
        prior_mid = market.get("prior_alert_mid")
        delta_pp = market.get("delta_pp")
        mid_txt = "n/a" if mid is None else f"{float(mid) * 100:.1f}%"
        lines.append("")
        lines.append(title)
        if prior_mid is not None and delta_pp is not None:
            prior_txt = f"{float(prior_mid) * 100:.1f}%"
            lines.append(f"  Odds: {mid_txt} &lt;- {prior_txt} ({float(delta_pp):+,.1f}pp)")
        else:
            delta_txt = "" if delta_pp is None else f" ({float(delta_pp):+,.1f}pp)"
            lines.append(f"  Odds: {mid_txt}{delta_txt}")
    return "\n".join(lines)


def _render_sparkline_chart(
    *,
    query_text: str,
    reason: str,
    markets: List[Dict[str, object]],
    histories: Dict[str, List[Dict[str, object]]],
) -> Optional[BytesIO]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.ticker import PercentFormatter
    except Exception:
        logger.exception("Matplotlib unavailable for Polymarket chart rendering")
        return None

    mover_markets = [market for market in markets if market.get("reason") == reason] or list(markets)
    plotted = []
    for market in mover_markets:
        token_id = str(market.get("yes_token_id") or "")
        history = histories.get(token_id) or []
        if history:
            plotted.append((market, history))
    if not plotted:
        return None

    fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
    colors = list(plt.get_cmap("tab10").colors)

    for idx, (market, history) in enumerate(plotted):
        color = colors[idx % len(colors)]
        points = []
        for row in history:
            try:
                ts = datetime.fromisoformat(str(row.get("observed_ts")))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                points.append((ts, float(row.get("mid")) * 100.0))
            except Exception:
                continue
        if not points:
            continue

        xs = [ts for ts, _ in points]
        ys = [val for _, val in points]
        label = str(market.get("title") or "Untitled market")
        ax.plot(xs, ys, linewidth=2.2, color=color, label=label, alpha=0.95)

        current_mid = market.get("mid")
        if current_mid is not None:
            current_ts = _iso_to_dt(str(market.get("observed_ts")))
            if current_ts is None:
                current_ts = xs[-1]
            current_y = float(current_mid) * 100.0
            ax.scatter([current_ts], [current_y], color=color, s=28, zorder=5)
            if market.get("reason") == reason:
                delta_pp = float(market.get("delta_pp") or 0.0)
                ax.annotate(
                    f"{current_y:.1f}% ({delta_pp:+.1f}pp)",
                    (current_ts, current_y),
                    xytext=(8, 10),
                    textcoords="offset points",
                    color=color,
                    fontsize=8,
                    weight="bold",
                )

        if market.get("reason") == reason:
            baseline_mid = market.get("prior_alert_mid")
            baseline_ts = _iso_to_dt(str(market.get("prior_alert_ts")))
            if baseline_mid is not None and baseline_ts is not None:
                ax.scatter(
                    [baseline_ts],
                    [float(baseline_mid) * 100.0],
                    facecolors="white",
                    edgecolors=color,
                    linewidths=1.6,
                    s=44,
                    zorder=6,
                )

    ax.set_title(f"Polymarket | {query_text or 'Tracked markets'} | {_reason_label(reason)}", fontsize=12, pad=14)
    ax.set_ylabel("Yes odds")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M ET", tz=ZoneInfo("America/New_York")))
    ax.grid(True, axis="y", alpha=0.25)
    ax.grid(True, axis="x", alpha=0.12)
    ax.legend(
        bbox_to_anchor=(0.5, 1.01),
        loc="lower center",
        ncol=2,
        fontsize=8,
        frameon=False,
        borderaxespad=0.0,
    )
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout(rect=[0, 0, 1, 0.9])

    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    buffer.name = "polymarket_watch.png"
    return buffer


def _load_recent_histories(markets: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    histories: Dict[str, List[Dict[str, object]]] = {}
    conn = sqlite3.connect(get_default_db_path())
    conn.row_factory = sqlite3.Row
    try:
        for market in markets:
            token_id = str(market.get("yes_token_id") or "")
            if not token_id:
                continue
            histories[token_id] = query_recent_timeseries(
                conn,
                token_id,
                hours=CHART_LOOKBACK_HOURS,
                max_points=CHART_MAX_POINTS,
            )
    finally:
        conn.close()
    return histories


async def cmd_pm_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not is_authorized(update.effective_chat.id):
        return
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text("Usage: /pm_track <natural language market query>")
        return

    await update.message.reply_text("Searching Polymarket markets...")
    try:
        markets = _search_markets(query, limit=PM_TRACK_MAX_MARKETS)
    except Exception as exc:
        logger.error("Polymarket search failed: %s", exc)
        await update.message.reply_text("Search failed. Please try again.")
        return

    if not markets:
        await update.message.reply_text("No active markets found for that query.")
        return

    selection_id = create_pending_selection(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id if update.effective_user else 0,
        user_query=query,
        markets=markets,
    )
    selection = get_pending_selection(selection_id)
    if not selection:
        await update.message.reply_text("Could not create selection session. Try again.")
        return

    await update.message.reply_text(
        _selection_message_text(selection),
        parse_mode="HTML",
        reply_markup=_build_selection_keyboard(selection_id, len(markets), set()),
    )


async def cmd_pm_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not is_authorized(update.effective_chat.id):
        return
    # Global Polymarket watchlist is routed to a fixed alert chat.
    subs = list_subscriptions(PM_ALERT_CHAT_ID, active_only=True)
    if not subs:
        await update.message.reply_text("No active Polymarket subscriptions in this chat.")
        return
    lines = ["<b>Tracked Polymarket markets</b>"]
    for i, sub in enumerate(subs, start=1):
        title = _escape(sub.title or sub.market_slug or sub.condition_id)
        lines.append(f"{i}. {title}")
    lines.append("")
    lines.append("Use /pm_untrack &lt;index&gt; to stop tracking.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_pm_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not is_authorized(update.effective_chat.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /pm_untrack <index>\nUse /pm_list first.")
        return
    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Index must be a number.")
        return
    # Global Polymarket watchlist is routed to a fixed alert chat.
    subs = list_subscriptions(PM_ALERT_CHAT_ID, active_only=True)
    if idx < 1 or idx > len(subs):
        await update.message.reply_text("Invalid index. Use /pm_list to check available indices.")
        return
    sub = subs[idx - 1]
    removed = deactivate_subscription(PM_ALERT_CHAT_ID, sub.id)
    if not removed:
        await update.message.reply_text("Could not untrack that market.")
        return

    remaining = count_active_subscriptions_for_condition(
        sub.condition_id,
        exclude_subscription_id=sub.id,
    )
    if remaining == 0:
        conn = sqlite3.connect(get_default_db_path())
        try:
            mark_market_inactive(conn, sub.condition_id)
        finally:
            conn.close()

    await update.message.reply_text(f"Stopped tracking: {sub.title or sub.market_slug or sub.condition_id}")


async def handle_pm_selection_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    if not is_authorized(query.message.chat.id):
        await query.answer("Not authorized", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 2:
        await query.answer("Invalid action", show_alert=True)
        return
    action = parts[0]
    selection_id = parts[1]

    selection = get_pending_selection(selection_id)
    if not selection:
        await query.answer("Selection expired", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    markets = selection.get("markets", [])
    selected = {int(i) for i in selection.get("selected_indices", [])}

    if action == "pm_pick":
        if len(parts) != 3:
            await query.answer("Invalid action", show_alert=True)
            return
        try:
            idx = int(parts[2])
        except ValueError:
            await query.answer("Invalid index", show_alert=True)
            return
        if idx < 0 or idx >= len(markets):
            await query.answer("Invalid index", show_alert=True)
            return
        toggled = toggle_pending_selected_index(selection_id, idx)
        if toggled is None:
            await query.answer("Could not update selection", show_alert=True)
            return
        selected = set(toggled)
        selection["selected_indices"] = sorted(selected)
        await query.message.edit_text(
            _selection_message_text(selection),
            parse_mode="HTML",
            reply_markup=_build_selection_keyboard(selection_id, len(markets), selected),
        )
        await query.answer("Updated")
        return

    if action == "pm_done":
        if not selected:
            await query.answer("Select at least one market.", show_alert=True)
            return
        conn = sqlite3.connect(get_default_db_path())
        try:
            ensure_polling_tables(conn)
            added_titles: List[str] = []
            query_group_id = _query_group_id_from_text(selection.get("user_query", ""))
            for idx in sorted(selected):
                if idx < 0 or idx >= len(markets):
                    continue
                market = markets[idx]
                yes_token, no_token = _extract_yes_no_tokens(market)
                condition_id = market.get("market_id")
                if not yes_token or not condition_id:
                    continue
                upsert_tracked_market(
                    conn=conn,
                    condition_id=str(condition_id),
                    market_slug=market.get("slug"),
                    title=market.get("title"),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                )
                upsert_subscription(
                    chat_id=PM_ALERT_CHAT_ID,
                    query_group_id=query_group_id,
                    condition_id=str(condition_id),
                    market_slug=market.get("slug"),
                    title=market.get("title"),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                )
                added_titles.append(str(market.get("title") or condition_id))
            upsert_query_message_state(
                query_group_id=query_group_id,
                chat_id=PM_ALERT_CHAT_ID,
                query_text=selection.get("user_query"),
            )
        finally:
            conn.close()
        delete_pending_selection(selection_id)
        if not added_titles:
            await query.message.edit_text("No valid markets were selected.")
            await query.answer("No markets added")
            return
        lines = ["Started tracking these markets:"]
        lines.extend(f"- {t}" for t in added_titles)
        await query.message.edit_text("\n".join(lines))
        await query.answer("Tracking enabled")
        return

    if action == "pm_cancel":
        delete_pending_selection(selection_id)
        await query.message.edit_text("Selection canceled.")
        await query.answer("Canceled")
        return

    await query.answer("Unknown action", show_alert=True)


async def handle_pm_alert_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    if not is_authorized(query.message.chat.id):
        await query.answer("Not authorized", show_alert=True)
        return
    if ":" not in query.data:
        await query.answer("Invalid action", show_alert=True)
        return

    action, alert_id = query.data.split(":", 1)
    if action not in ("pm_approve", "pm_reject"):
        await query.answer("Invalid action", show_alert=True)
        return

    record = get_alert_label(alert_id)
    if not record:
        await query.answer("Alert not found", show_alert=True)
        return

    label = "approved" if action == "pm_approve" else "rejected"
    retrying_unforwarded_approval = (
        record.get("label") == "approved"
        and not record.get("forwarded_message_id")
        and label == "approved"
        and bool(PM_PUBLIC_CHAT_ID)
    )
    if record.get("label") and not retrying_unforwarded_approval:
        await query.answer("Already labeled", show_alert=True)
        return

    await query.answer("Working...")

    original_text = str(
        record.get("payload_text")
        or query.message.caption_html
        or query.message.text_html
        or query.message.caption
        or query.message.text
        or ""
    )

    forwarded_id = None
    if label == "approved" and PM_PUBLIC_CHAT_ID:
        try:
            if query.message.photo:
                forwarded = await _send_with_retry(
                    lambda: context.bot.copy_message(
                        chat_id=int(PM_PUBLIC_CHAT_ID),
                        from_chat_id=query.message.chat.id,
                        message_id=query.message.message_id,
                        caption=original_text,
                        parse_mode="HTML",
                    )
                )
            else:
                forwarded = await _send_with_retry(
                    lambda: context.bot.send_message(
                        chat_id=int(PM_PUBLIC_CHAT_ID),
                        text=original_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                )
            forwarded_id = forwarded.message_id
        except Exception:
            logger.exception("Failed to forward Polymarket alert to public chat")
            fail_text = (
                original_text
                + "\n\n<b>Forward failed (Telegram timeout) - click Approve again to retry.</b>"
            )
            try:
                if query.message.photo:
                    await query.message.edit_caption(
                        caption=fail_text,
                        parse_mode="HTML",
                        reply_markup=query.message.reply_markup,
                    )
                else:
                    await query.message.edit_text(
                        fail_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=query.message.reply_markup,
                    )
            except Exception:
                logger.exception("Failed to edit original Polymarket alert after forward failure")
            return

    if retrying_unforwarded_approval:
        if forwarded_id:
            mark_alert_forwarded(alert_id=alert_id, forwarded_message_id=forwarded_id)
    else:
        saved = set_alert_label(
            alert_id=alert_id,
            label=label,
            labeled_by=query.from_user.id if query.from_user else None,
            forwarded_message_id=forwarded_id,
        )
        if not saved:
            logger.warning("Polymarket alert label changed before callback commit for alert_id=%s", alert_id)
            if forwarded_id:
                mark_alert_forwarded(alert_id=alert_id, forwarded_message_id=forwarded_id)
            return

    user_label = _escape(_format_user_label(query.from_user))
    status_line = f"\n\n<b>{label.title()} by {user_label}</b>"
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=original_text + status_line,
                parse_mode="HTML",
                reply_markup=None,
            )
        else:
            await query.message.edit_text(
                original_text + status_line,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=None,
            )
    except Exception:
        logger.exception("Failed to edit original Polymarket alert message")


async def _poll_once(app: Application) -> None:
    subs = list_all_active_subscriptions()
    if not subs:
        return
    loop = asyncio.get_running_loop()
    conn = sqlite3.connect(get_default_db_path())
    conn.row_factory = sqlite3.Row
    try:
        ensure_polling_tables(conn)
        now = datetime.now(timezone.utc)
        group_rows: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        group_reasons: Dict[str, List[str]] = defaultdict(list)
        group_query_text: Dict[str, str] = {}
        for sub in subs:
            # If market already marked inactive/resolved in DB, stop this subscription.
            tracked_row = conn.execute(
                """
                SELECT active
                FROM tracked_markets
                WHERE condition_id = ?
                LIMIT 1
                """,
                (sub.condition_id,),
            ).fetchone()
            if tracked_row is not None and int(tracked_row[0]) == 0:
                deactivate_subscription(sub.chat_id, sub.id)
                continue

            # Keep query-grouped subscriptions together; do not globally merge legacy rows.
            group_id = sub.query_group_id or f"legacy:{sub.id}"
            mid, status = await loop.run_in_executor(None, fetch_midpoint, sub.yes_token_id)
            if mid is None:
                # Apply modest jitter/backoff effect by skipping on transient errors.
                if status in {429, 500, 502, 503, 504} or status is None:
                    continue
                if status == 404:
                    # Token likely resolved/inactive on CLOB. Stop polling this subscription.
                    deactivate_subscription(sub.chat_id, sub.id)
                    remaining = count_active_subscriptions_for_condition(
                        sub.condition_id,
                        exclude_subscription_id=sub.id,
                    )
                    if remaining == 0:
                        mark_market_inactive(conn, sub.condition_id)
                    continue
                continue

            insert_timeseries_row(
                conn=conn,
                token_id=sub.yes_token_id,
                condition_id=sub.condition_id,
                market_slug=sub.market_slug,
                observed_ts=utc_now_iso(),
                mid=mid,
                best_bid=None,
                best_ask=None,
                source="telegram_pm_poll",
                raw_payload={"mid": mid, "subscription_id": sub.id},
            )

            state_row = get_alert_state(sub.id)
            eval_state = _eval_state_from_row(state_row)
            prior_alert_mid = eval_state.last_alert_mid
            prior_alert_ts = eval_state.last_alert_ts
            reason, updated = _evaluate_alert_conditions(mid, eval_state, now)
            delta_pp = None if prior_alert_mid is None else (mid - float(prior_alert_mid)) * 100.0

            update_alert_state(
                subscription_id=sub.id,
                last_mid=updated.last_mid,
                last_alert_mid=updated.last_alert_mid,
                last_alert_ts=updated.last_alert_ts.isoformat() if updated.last_alert_ts else None,
                streak_dir=updated.streak_dir,
                streak_len=updated.streak_len,
                streak_start_mid=updated.streak_start_mid,
            )

            group_rows[group_id].append(
                {
                    "subscription_id": sub.id,
                    "title": sub.title or sub.market_slug or sub.condition_id,
                    "slug": sub.market_slug,
                    "yes_token_id": sub.yes_token_id,
                    "mid": mid,
                    "delta_pp": delta_pp,
                    "reason": reason,
                    "observed_ts": now.isoformat(),
                    "prior_alert_mid": prior_alert_mid,
                    "prior_alert_ts": prior_alert_ts.isoformat() if prior_alert_ts else None,
                }
            )
            if reason:
                group_reasons[group_id].append(reason)

            if group_id not in group_query_text:
                state = get_query_message_state(group_id)
                if state and state.get("query_text"):
                    group_query_text[group_id] = str(state["query_text"])
                else:
                    group_query_text[group_id] = f"Tracked markets ({group_id})"
                    upsert_query_message_state(
                        query_group_id=group_id,
                        chat_id=PM_ALERT_CHAT_ID,
                        query_text=group_query_text[group_id],
                    )

            await asyncio.sleep(0.1 + random.uniform(0.0, 0.1))

        for group_id, markets in group_rows.items():
            state = get_query_message_state(group_id)
            if not state:
                continue
            reason = None
            if group_reasons.get(group_id):
                if "significant_change" in group_reasons[group_id]:
                    reason = "significant_change"
                else:
                    reason = "trend"
            if not reason:
                continue

            query_text = group_query_text.get(group_id, state.get("query_text") or "Tracked markets")
            text = _format_group_update_text(query_text=query_text, reason=reason, markets=markets)
            histories = await loop.run_in_executor(None, _load_recent_histories, markets)
            chart = await loop.run_in_executor(
                None,
                lambda: _render_sparkline_chart(
                    query_text=query_text,
                    reason=reason,
                    markets=markets,
                    histories=histories,
                ),
            )
            anchor_market = next((m for m in markets if m.get("reason") == reason), markets[0])
            alert_id = create_alert_label(
                subscription_id=int(anchor_market.get("subscription_id") or 0),
                chat_id=int(state["chat_id"]),
                telegram_message_id=None,
                payload_text=text,
            )
            if chart is not None:
                msg = await app.bot.send_photo(
                    chat_id=int(state["chat_id"]),
                    photo=chart,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=_build_pm_alert_keyboard(alert_id),
                )
            else:
                msg = await app.bot.send_message(
                    chat_id=int(state["chat_id"]),
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=_build_pm_alert_keyboard(alert_id),
                )
            set_alert_message_id(alert_id, msg.message_id)
            set_query_message_delivery(
                query_group_id=group_id,
                telegram_message_id=msg.message_id,
                reason=reason,
                notified_ts=now.isoformat(),
            )
    finally:
        conn.close()


async def _poll_loop(app: Application) -> None:
    logger.info("Polymarket poll loop started (interval=%ss)", POLL_INTERVAL_S)
    while True:
        try:
            await _poll_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Polymarket poll loop cycle failed")
        await asyncio.sleep(POLL_INTERVAL_S)


async def start_polling_task(app: Application) -> None:
    init_db()
    if app.bot_data.get("pm_poll_task"):
        return
    app.bot_data["pm_poll_task"] = app.create_task(_poll_loop(app))
    logger.info("Polymarket polling background task created")


async def stop_polling_task(app: Application) -> None:
    task = app.bot_data.get("pm_poll_task")
    if not task:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    app.bot_data.pop("pm_poll_task", None)
    logger.info("Polymarket polling background task stopped")
