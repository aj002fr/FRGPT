#!/usr/bin/env python3
"""On-demand Databento Live TBBO backup for aggression dashboards."""

from __future__ import annotations

import logging
import numbers
import os
import re
import threading
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_ENV_PATH = Path(r"Z:\Aarsh\Data\AccessAPI\.env")
DEFAULT_DATASET = "GLBX.MDP3"
DEFAULT_SYMBOLS = ("ZN.v.0", "ZB.v.0", "ZT.v.0", "ZF.v.0")
DEFAULT_STYPE_IN = "continuous"
DEFAULT_RECONNECT_DELAY_SEC = 2.0
DEFAULT_STOP_JOIN_SEC = 3.0
START_WAIT_STOPPING_SEC = 15.0
MAX_REPLAY_AGE = timedelta(hours=23, minutes=55)

_CME_TO_DASHBOARD = {"ZF": "FV", "ZT": "TU", "ZN": "TY", "ZB": "US"}
_UNDEF_PRICE_THRESHOLD = 9_000_000_000_000_000_000
DEFAULT_DEDUPE_CAPACITY = 200_000


def _read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def resolve_databento_api_key(env_path: Path = DEFAULT_ENV_PATH) -> str:
    """Resolve the key without importing AccessAPI code or exposing secrets."""
    key = os.getenv("DATABENTO_API_KEY", "").strip().strip("\"'")
    if not key:
        from scripts.databento_live_state import get_databento_env

        key = str(get_databento_env().get("api_key") or "").strip().strip("\"'")
    if not key:
        key = _read_env_file(env_path).get("DATABENTO_API_KEY", "").strip()
    if not key:
        raise RuntimeError(f"DATABENTO_API_KEY not found in environment, config/keys.env, or {env_path}")
    return key


def _utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, date):
            result = datetime.combine(value, datetime.min.time())
        elif isinstance(value, numbers.Integral):
            result = datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def _timestamp_iso(value: Any) -> Optional[str]:
    if isinstance(value, numbers.Integral):
        nanoseconds = int(value)
        seconds, remainder = divmod(nanoseconds, 1_000_000_000)
        try:
            base = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
        return f"{base:%Y-%m-%dT%H:%M:%S}.{remainder:09d}+00:00"
    parsed = _utc_datetime(value)
    return parsed.isoformat() if parsed else None


def _timestamp_ns(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, numbers.Integral):
        raw = int(value)
        return raw if abs(raw) >= 1_000_000_000_000 else raw * 1_000_000_000
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        seconds = int(parsed.replace(microsecond=0).timestamp())
        return seconds * 1_000_000_000 + parsed.microsecond * 1_000
    text = str(value).strip()
    if text.isdigit():
        return _timestamp_ns(int(text))
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})",
        text,
    )
    if not match:
        parsed = _utc_datetime(text)
        return _timestamp_ns(parsed) if parsed else None
    base_text, fraction, offset = match.groups()
    parsed = datetime.fromisoformat(f"{base_text}{'+00:00' if offset == 'Z' else offset}")
    seconds = int(parsed.astimezone(timezone.utc).timestamp())
    nanos = int((fraction or "").ljust(9, "0"))
    return seconds * 1_000_000_000 + nanos


def _char_field(record: Any, name: str, default: str = "") -> str:
    value = getattr(record, name, None)
    if value is None:
        return default
    value = getattr(value, "value", value)
    text = str(value)
    return text[:1].upper() if text else default


def _safe_price(value: Any) -> Optional[float]:
    try:
        raw = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if abs(raw) >= _UNDEF_PRICE_THRESHOLD:
        return None
    price = raw / 1_000_000_000 if abs(raw) > 1_000_000 else raw
    return price if price > 0 else None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _dashboard_symbol(*candidates: str) -> Optional[str]:
    for candidate in candidates:
        token = str(candidate or "").upper().split(".")[0]
        for cme_root, dashboard_symbol in _CME_TO_DASHBOARD.items():
            if token.startswith(cme_root):
                return dashboard_symbol
    return None


def _canonical_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return format(float(text), ".12g")
    except (TypeError, ValueError):
        return text.upper()


class TbboEventDeduplicator:
    """Bounded exact event IDs for reconnect and source handoff overlap."""

    def __init__(self, capacity: int = DEFAULT_DEDUPE_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._recent_order: deque[tuple[Any, ...]] = deque()
        self._recent: set[tuple[Any, ...]] = set()
        self._recv_highwater: Dict[str, int] = {}
        self._recv_boundary_ids: Dict[str, set[tuple[Any, ...]]] = {}

    def accept(
        self,
        symbol: str,
        data: Dict[str, Any],
        event_ts: datetime,
    ) -> bool:
        instrument = str(data.get("instrument_id") or data.get("symbol") or symbol)
        sequence = _safe_int(data.get("sequence"))
        if sequence is not None:
            event_marker = str(data.get("ts_event") or "").strip()
            if event_marker.endswith("Z"):
                event_marker = f"{event_marker[:-1]}+00:00"
            if not event_marker:
                event_marker = event_ts.astimezone(timezone.utc).isoformat(
                    timespec="microseconds"
                )
            identity = (
                "sequence",
                instrument,
                event_marker,
                sequence,
                *(
                    _canonical_value(data.get(field))
                    for field in (
                        "ts_recv", "action", "side", "price", "size",
                        "bid_price", "ask_price", "bid_size", "ask_size",
                        "flags", "ts_in_delta", "depth",
                    )
                ),
            )
        else:
            identity = self._fallback_identity(symbol, data)
        recv_ns = _timestamp_ns(data.get("ts_recv"))
        if recv_ns is not None:
            recv_highwater = self._recv_highwater.get(instrument)
            if recv_highwater is not None and recv_ns < recv_highwater:
                return False
            if (
                recv_highwater is not None
                and recv_ns == recv_highwater
                and identity in self._recv_boundary_ids.get(instrument, set())
            ):
                return False
        if identity in self._recent:
            return False
        self._recent.add(identity)
        self._recent_order.append(identity)
        while len(self._recent_order) > self.capacity:
            self._recent.discard(self._recent_order.popleft())
        if recv_ns is not None:
            if recv_ns > self._recv_highwater.get(instrument, -1):
                self._recv_highwater[instrument] = recv_ns
                self._recv_boundary_ids[instrument] = {identity}
            else:
                self._recv_boundary_ids.setdefault(instrument, set()).add(identity)
        return True

    @staticmethod
    def _fallback_identity(
        symbol: str,
        data: Dict[str, Any],
    ) -> tuple[Any, ...]:
        fields = (
            "ts_event", "action", "side", "price", "size",
            "bid_price", "ask_price", "bid_size", "ask_size",
        )
        return (
            "fields",
            str(data.get("instrument_id") or data.get("symbol") or symbol),
            *(_canonical_value(data.get(field)) for field in fields),
        )


class DatabentoLiveBackup:
    """Run a direct TBBO subscription only while failover is active."""

    def __init__(
        self,
        on_record: Callable[[Dict[str, Any]], None],
        *,
        api_key: Optional[str] = None,
        dataset: str = DEFAULT_DATASET,
        symbols: Iterable[str] = DEFAULT_SYMBOLS,
        stype_in: str = DEFAULT_STYPE_IN,
        reconnect_delay_sec: float = DEFAULT_RECONNECT_DELAY_SEC,
    ) -> None:
        self.on_record = on_record
        self.api_key = api_key
        self.dataset = dataset
        self.symbols = tuple(symbols)
        self.stype_in = stype_in
        self.reconnect_delay_sec = reconnect_delay_sec
        self._lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._running = False
        self._connected = False
        self._error: Optional[str] = None
        self._last_record_ts: Optional[datetime] = None
        self._replay_from: Optional[datetime] = None
        self._symbol_map: Dict[int, tuple[str, str]] = {}

    def start(self, replay_from: Optional[datetime] = None) -> bool:
        with self._lifecycle:
            return self._start_locked(replay_from)

    def _start_locked(self, replay_from: Optional[datetime] = None) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            with self._lock:
                already_running = self._running
            if already_running:
                return True
            if thread is not threading.current_thread():
                thread.join(timeout=START_WAIT_STOPPING_SEC)
            if thread.is_alive():
                with self._lock:
                    self._error = "previous Databento backup thread is still stopping"
                return False
        with self._lock:
            self._running = True
            self._connected = False
            self._error = None
            self._replay_from = self._bounded_replay_start(replay_from)
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="databento-live-backup",
                daemon=True,
            )
            self._thread.start()
        return True

    def stop(self, join_timeout_sec: float = DEFAULT_STOP_JOIN_SEC) -> None:
        with self._lifecycle:
            self._stop_locked(join_timeout_sec)

    def _stop_locked(self, join_timeout_sec: float) -> None:
        with self._lock:
            self._running = False
            self._connected = False
            client = self._client
        self._wake.set()
        if client is not None:
            try:
                client.terminate()
            except Exception:
                logger.debug("Databento backup terminate failed", exc_info=True)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=join_timeout_sec)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "connected": self._connected,
                "error": self._error,
                "last_record_ts": (
                    self._last_record_ts.isoformat() if self._last_record_ts else None
                ),
                "replay_from": self._replay_from.isoformat() if self._replay_from else None,
                "dataset": self.dataset,
                "symbols": list(self.symbols),
            }

    @staticmethod
    def _bounded_replay_start(value: Optional[datetime]) -> datetime:
        now = datetime.now(timezone.utc)
        parsed = _utc_datetime(value)
        if parsed is None:
            return now
        return min(now, max(parsed, now - MAX_REPLAY_AGE))

    def _is_running(self) -> bool:
        with self._lock:
            return self._running

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._connected = False
            self._error = message

    def _thread_main(self) -> None:
        while self._is_running():
            try:
                self._run_once()
            except Exception as exc:
                if self._is_running():
                    logger.exception("Databento live backup stream failed")
                    self._set_error(str(exc))
            finally:
                with self._lock:
                    client = self._client
                    self._client = None
                    self._connected = False
                if client is not None:
                    try:
                        client.terminate()
                    except Exception:
                        logger.debug("Databento backup cleanup failed", exc_info=True)
            if self._is_running():
                self._wake.wait(self.reconnect_delay_sec)

    def _run_once(self) -> None:
        try:
            import databento as db
        except ImportError as exc:
            raise RuntimeError("databento package is required for live backup") from exc

        key = self.api_key or resolve_databento_api_key()
        with self._lock:
            if not self._running:
                return
            client = db.Live(key=key)
            self._client = client
            replay_from = self._bounded_replay_start(self._replay_from)
            self._replay_from = replay_from
            client.subscribe(
                dataset=self.dataset,
                schema="tbbo",
                symbols=list(self.symbols),
                stype_in=self.stype_in,
                start=replay_from,
            )
            self._connected = True
            self._error = None
        logger.warning("Databento live backup started from %s", replay_from.isoformat())
        for record in client:
            if not self._is_running():
                break
            normalized = self._normalize_record(record)
            if normalized is None:
                continue
            self.on_record(normalized)
            event_ts = _utc_datetime(normalized.get("ts_event"))
            with self._lock:
                self._last_record_ts = datetime.now(timezone.utc)
                if event_ts is not None and (
                    self._replay_from is None or event_ts > self._replay_from
                ):
                    self._replay_from = event_ts
                self._connected = self._running
                if self._running:
                    self._error = None

    def _normalize_record(self, record: Any) -> Optional[Dict[str, Any]]:
        instrument_id = _safe_int(getattr(record, "instrument_id", None))
        if hasattr(record, "stype_in_symbol"):
            if instrument_id is not None:
                continuous = str(getattr(record, "stype_in_symbol", "") or "")
                raw = str(getattr(record, "stype_out_symbol", "") or continuous)
                self._symbol_map[instrument_id] = (raw, continuous)
            return None
        if not hasattr(record, "price") or instrument_id is None:
            return None

        raw_symbol, continuous_symbol = self._symbol_map.get(instrument_id, ("", ""))
        symbol = _dashboard_symbol(raw_symbol, continuous_symbol)
        ts_event_raw = getattr(record, "ts_event", None)
        ts_event = _utc_datetime(ts_event_raw)
        ts_event_iso = _timestamp_iso(ts_event_raw)
        price = _safe_price(getattr(record, "price", None))
        if symbol is None or ts_event is None or ts_event_iso is None or price is None:
            return None

        level = None
        levels = getattr(record, "levels", None)
        if levels:
            level = levels[0]
        bid_price = _safe_price(
            getattr(level, "bid_px", None)
            if level is not None
            else getattr(record, "bid_px_00", None)
        )
        ask_price = _safe_price(
            getattr(level, "ask_px", None)
            if level is not None
            else getattr(record, "ask_px_00", None)
        )
        bid_size = _safe_int(
            getattr(level, "bid_sz", None)
            if level is not None
            else getattr(record, "bid_sz_00", None)
        )
        ask_size = _safe_int(
            getattr(level, "ask_sz", None)
            if level is not None
            else getattr(record, "ask_sz_00", None)
        )
        return {
            "ts_event": ts_event_iso,
            "ts_recv": _timestamp_iso(getattr(record, "ts_recv", None)) or ts_event_iso,
            "symbol": raw_symbol,
            "continuous_symbol": continuous_symbol,
            "bloomberg_symbol": symbol,
            "action": _char_field(record, "action", "T"),
            "side": _char_field(record, "side", "N"),
            "price": price,
            "size": _safe_int(getattr(record, "size", None)),
            "bid_price": bid_price,
            "ask_price": ask_price,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "instrument_id": instrument_id,
            "sequence": _safe_int(getattr(record, "sequence", None)),
            "publisher_id": _safe_int(getattr(record, "publisher_id", None)),
            "rtype": _safe_int(getattr(record, "rtype", None)),
            "flags": _safe_int(getattr(record, "flags", None)),
            "ts_in_delta": _safe_int(getattr(record, "ts_in_delta", None)),
            "depth": _safe_int(getattr(record, "depth", None)),
        }


__all__ = [
    "DatabentoLiveBackup",
    "DEFAULT_ENV_PATH",
    "DEFAULT_SYMBOLS",
    "resolve_databento_api_key",
]
