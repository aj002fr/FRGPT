#!/usr/bin/env python3
"""
Align FOMC Q&A answer anchors to market microstructure windows.

Outputs one row per Powell answer with signed bps reactions, realized-vol spikes,
and direction-consistency labels across:
  - 0-30s
  - 30s-2m
  - 0-10m
"""

from __future__ import annotations

import argparse
import bisect
import csv
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_DATABASE, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER
from rules.treasury_price_conversion import points_to_bps
from src.core.fomc_qa_parser import extract_qa_blocks

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


logger = logging.getLogger("fomc_qa_microstructure_alignment")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

EASTERN_TZ = ZoneInfo("America/New_York")

DEFAULT_TRANSCRIPTS_DIR = PROJECT_ROOT / "data" / "fed" / "fomc conference" / "transcripts"
FALLBACK_TRANSCRIPTS_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "transcripts"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "qa_microstructure"
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"

WINDOW_SPECS = {
    "0_30": (0, 30),
    "30_120": (30, 120),
    "0_600": (0, 600),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Align FOMC Q&A answers to market windows.")
    parser.add_argument("--transcripts-dir", type=Path, default=DEFAULT_TRANSCRIPTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-transcripts", type=int, default=None)
    parser.add_argument("--resolution", choices=["auto", "1s", "1m"], default="auto")
    parser.add_argument("--primary-symbol", type=str, default="ZN")
    parser.add_argument("--fallback-symbol", type=str, default="TY")
    parser.add_argument("--anchor-fallback-time-et", type=str, default="14:30:00")
    return parser.parse_args()


def get_pg_connection():
    if not HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 is required to fetch market data")
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
    )


def list_transcript_files(transcripts_dir: Path, max_transcripts: Optional[int]) -> List[Path]:
    files = sorted(p for p in transcripts_dir.glob("*.json") if p.name != "index.json")
    if max_transcripts:
        files = files[:max_transcripts]
    return files


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_anchor_time_utc(event_date_str: str) -> datetime:
    dt_naive = datetime.fromisoformat(event_date_str)
    dt_eastern = dt_naive.replace(tzinfo=EASTERN_TZ)
    return dt_eastern.astimezone(timezone.utc)


def lookup_event_anchor(transcript_date: str, fallback_time_et: str) -> datetime:
    if ECONOMIC_EVENTS_DB.exists():
        conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT event_date
                FROM economic_events
                WHERE event_name = 'Fed Press Conference'
                  AND country LIKE '%United States%'
                  AND DATE(event_date) = ?
                ORDER BY event_date ASC
                LIMIT 1
                """,
                (transcript_date,),
            )
            row = cur.fetchone()
            if row and row[0]:
                return get_anchor_time_utc(row[0])
        finally:
            conn.close()

    fallback_dt = datetime.fromisoformat(f"{transcript_date}T{fallback_time_et}")
    return fallback_dt.replace(tzinfo=EASTERN_TZ).astimezone(timezone.utc)


def table_exists(conn, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT EXISTS(
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = %s
        )
        """,
        (table_name,),
    )
    return bool(cur.fetchone()[0])


def count_symbol_rows(conn, table_name: str, symbol_prefix: str, start_utc: datetime, end_utc: datetime) -> int:
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_name}
        WHERE symbol ILIKE %s
          AND ts_event >= %s
          AND ts_event <= %s
        """,
        (f"{symbol_prefix}%", start_utc, end_utc),
    )
    return int(cur.fetchone()[0])


def pick_table(conn, requested_resolution: str) -> Tuple[str, int]:
    one_s_exists = table_exists(conn, "ohlcv_1s")
    one_m_exists = table_exists(conn, "ohlcv_1m")

    if requested_resolution == "1s":
        if not one_s_exists:
            raise RuntimeError("Requested 1s resolution but ohlcv_1s does not exist")
        return "ohlcv_1s", 1
    if requested_resolution == "1m":
        if not one_m_exists:
            raise RuntimeError("Requested 1m resolution but ohlcv_1m does not exist")
        return "ohlcv_1m", 60

    # auto
    if one_s_exists:
        return "ohlcv_1s", 1
    if one_m_exists:
        return "ohlcv_1m", 60
    raise RuntimeError("Neither ohlcv_1s nor ohlcv_1m exists")


def pick_symbol_root(
    conn,
    table_name: str,
    primary_symbol: str,
    fallback_symbol: str,
    start_utc: datetime,
    end_utc: datetime,
) -> Optional[str]:
    if count_symbol_rows(conn, table_name, primary_symbol, start_utc, end_utc) > 0:
        return primary_symbol
    if count_symbol_rows(conn, table_name, fallback_symbol, start_utc, end_utc) > 0:
        return fallback_symbol
    return None


def fetch_bars(
    conn,
    table_name: str,
    symbol_root: str,
    start_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        f"""
        SELECT ts_event, close
        FROM {table_name}
        WHERE symbol ILIKE %s
          AND ts_event >= %s
          AND ts_event <= %s
          AND close IS NOT NULL
        ORDER BY ts_event ASC
        """,
        (f"{symbol_root}%", start_utc, end_utc),
    )
    rows = [dict(r) for r in cur.fetchall()]
    out = []
    for r in rows:
        ts = r["ts_event"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append({"ts_event": ts, "close": float(r["close"])})
    return out


def prepare_ts_close(bars: Sequence[Dict[str, Any]]) -> Tuple[List[datetime], List[float], List[float]]:
    ts = [b["ts_event"] for b in bars]
    closes = [float(b["close"]) for b in bars]
    ts_epoch = [t.timestamp() for t in ts]
    return ts, closes, ts_epoch


def close_at_or_before(ts_epoch: Sequence[float], closes: Sequence[float], target: datetime) -> Optional[float]:
    idx = bisect.bisect_right(ts_epoch, target.timestamp()) - 1
    if idx < 0:
        return None
    return float(closes[idx])


def bars_in_window(
    ts: Sequence[datetime],
    ts_epoch: Sequence[float],
    closes: Sequence[float],
    start: datetime,
    end: datetime,
) -> Tuple[List[datetime], List[float]]:
    start_idx = bisect.bisect_left(ts_epoch, start.timestamp())
    end_idx = bisect.bisect_right(ts_epoch, end.timestamp())
    return list(ts[start_idx:end_idx]), list(closes[start_idx:end_idx])


def build_series_for_window(
    ts: Sequence[datetime],
    closes: Sequence[float],
    ts_epoch: Sequence[float],
    start: datetime,
    end: datetime,
) -> List[float]:
    prev_close = close_at_or_before(ts_epoch, closes, start)
    _, win_closes = bars_in_window(ts, ts_epoch, closes, start, end)
    series: List[float] = []
    if prev_close is not None:
        series.append(prev_close)
    for v in win_closes:
        if not series or v != series[-1]:
            series.append(v)
    return series


def realized_vol_from_series(series: Sequence[float]) -> Optional[float]:
    if len(series) < 2:
        return None
    acc = 0.0
    for i in range(1, len(series)):
        prev = series[i - 1]
        cur = series[i]
        if prev <= 0 or cur <= 0:
            continue
        r = math.log(cur / prev)
        acc += r * r
    return math.sqrt(acc)


def _sample_candidates(candidates: List[datetime], cap: int = 120) -> List[datetime]:
    if len(candidates) <= cap:
        return candidates
    stride = max(1, math.ceil(len(candidates) / cap))
    return candidates[::stride]


def rolling_baseline_rv_values(
    ts: Sequence[datetime],
    closes: Sequence[float],
    ts_epoch: Sequence[float],
    baseline_start: datetime,
    baseline_end: datetime,
    window_seconds: int,
) -> List[float]:
    candidates = [t for t in ts if baseline_start <= t <= baseline_end - timedelta(seconds=window_seconds)]
    candidates = _sample_candidates(candidates, cap=120)
    values: List[float] = []
    for s in candidates:
        e = s + timedelta(seconds=window_seconds)
        series = build_series_for_window(ts, closes, ts_epoch, s, e)
        rv = realized_vol_from_series(series)
        if rv is not None:
            values.append(rv)
    return values


def median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values) / len(values))


def std(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = mean(values)
    if m is None:
        return None
    acc = sum((v - m) ** 2 for v in values)
    return float(math.sqrt(acc / (len(values) - 1)))


def sign(v: float, eps: float = 1e-12) -> int:
    if v > eps:
        return 1
    if v < -eps:
        return -1
    return 0


def direction_sign_label(v: float) -> str:
    s = sign(v)
    if s > 0:
        return "up"
    if s < 0:
        return "down"
    return "flat"


def classify_direction(
    ts: Sequence[datetime],
    closes: Sequence[float],
    ts_epoch: Sequence[float],
    start: datetime,
    end: datetime,
) -> Tuple[str, str, Optional[float]]:
    c_start = close_at_or_before(ts_epoch, closes, start)
    c_end = close_at_or_before(ts_epoch, closes, end)
    if c_start is None or c_end is None:
        return "mixed", "flat", None

    mid = start + (end - start) / 2
    c_mid = close_at_or_before(ts_epoch, closes, mid)
    if c_mid is None:
        return "mixed", direction_sign_label(c_end - c_start), None

    # Build path series for efficiency ratio.
    path_series = build_series_for_window(ts, closes, ts_epoch, start, end)
    if len(path_series) < 2:
        return "mixed", direction_sign_label(c_end - c_start), None

    m_net = c_end - c_start
    path_len = 0.0
    for i in range(1, len(path_series)):
        path_len += abs(path_series[i] - path_series[i - 1])
    er = abs(m_net) / max(path_len, 1e-12)

    m1 = c_mid - c_start
    m2 = c_end - c_mid

    if sign(m1) == sign(m2) == sign(m_net) and er >= 0.60:
        label = "trend"
    elif sign(m1) != sign(m2) and abs(m2) >= 0.50 * abs(m1) and er <= 0.40:
        label = "mean_revert"
    else:
        label = "mixed"

    return label, direction_sign_label(m_net), er


def boundary_degraded(start: datetime, end: datetime, resolution_seconds: int) -> bool:
    if resolution_seconds <= 1:
        return False
    s = int(start.timestamp())
    e = int(end.timestamp())
    if (s % resolution_seconds) != 0:
        return True
    if (e % resolution_seconds) != 0:
        return True
    if (e - s) < resolution_seconds:
        return True
    return False


def compute_answer_metrics(
    answer: Dict[str, Any],
    anchor_utc: datetime,
    ts: Sequence[datetime],
    closes: Sequence[float],
    ts_epoch: Sequence[float],
    resolution_seconds: int,
    symbol_used: str,
    resolution_used: str,
) -> Dict[str, Any]:
    t0 = anchor_utc + timedelta(seconds=float(answer["answer_start_s"]))
    row: Dict[str, Any] = {
        "answer_idx": answer["answer_idx"],
        "question_speaker": answer["question_speaker"],
        "question_speakers": "|".join(answer["question_speakers"]),
        "question_text": answer["question_text"],
        "answer_speaker": answer["answer_speaker"],
        "answer_text": answer["answer_text"],
        "question_start_s": answer["question_start_s"],
        "question_end_s": answer["question_end_s"],
        "answer_start_s": answer["answer_start_s"],
        "answer_end_s": answer["answer_end_s"],
        "anchor_utc": t0.isoformat(),
        "symbol_used": symbol_used,
        "resolution_used": resolution_used,
        "parse_confidence": answer["parse_confidence"],
    }

    for tag, (off_start, off_end) in WINDOW_SPECS.items():
        w_start = t0 + timedelta(seconds=off_start)
        w_end = t0 + timedelta(seconds=off_end)
        c_start = close_at_or_before(ts_epoch, closes, w_start)
        c_end = close_at_or_before(ts_epoch, closes, w_end)
        missing_price = c_start is None or c_end is None
        delta_bps = None if missing_price else float(points_to_bps(c_end - c_start))
        row[f"delta_bps_{tag}"] = delta_bps
        row[f"missing_price_{tag}"] = missing_price
        row[f"degraded_window_{tag}"] = boundary_degraded(w_start, w_end, resolution_seconds)

        # Realized vol spike
        rv_series = build_series_for_window(ts, closes, ts_epoch, w_start, w_end)
        rv_window = realized_vol_from_series(rv_series)
        baseline_start = t0 - timedelta(minutes=30)
        baseline_end = t0 - timedelta(minutes=1)
        baseline_vals = rolling_baseline_rv_values(
            ts=ts,
            closes=closes,
            ts_epoch=ts_epoch,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            window_seconds=off_end - off_start,
        )
        baseline_med = median(baseline_vals)
        baseline_mean = mean(baseline_vals)
        baseline_std = std(baseline_vals)

        row[f"rv_{tag}"] = rv_window
        row[f"rv_baseline_{tag}"] = baseline_med
        row[f"vol_spike_ratio_{tag}"] = (
            None
            if rv_window is None or baseline_med is None
            else float(rv_window / max(baseline_med, 1e-12))
        )
        row[f"vol_spike_z_{tag}"] = (
            None
            if rv_window is None or baseline_mean is None or baseline_std is None or baseline_std <= 0
            else float((rv_window - baseline_mean) / baseline_std)
        )

        # Direction consistency
        d_label, d_sign, er = classify_direction(ts, closes, ts_epoch, w_start, w_end)
        row[f"direction_label_{tag}"] = d_label
        row[f"direction_sign_{tag}"] = d_sign
        row[f"efficiency_ratio_{tag}"] = er

    return row


def write_json(path: Path, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def process_transcript(
    transcript_path: Path,
    conn,
    table_name: str,
    resolution_seconds: int,
    resolution_used: str,
    primary_symbol: str,
    fallback_symbol: str,
    fallback_time_et: str,
) -> Dict[str, Any]:
    data = load_json(transcript_path)
    date = data.get("date")
    video_id = data.get("video_id")
    segments = data.get("segments", [])
    duration_seconds = float(data.get("duration_seconds", 0.0) or 0.0)
    if not date or not video_id:
        raise ValueError(f"Missing date/video_id in {transcript_path}")

    anchor_utc = lookup_event_anchor(date, fallback_time_et)
    qa_blocks = extract_qa_blocks(segments)
    qa_rows = [b.to_dict() for b in qa_blocks]

    # Pull a transcript-level market range once (baseline + max window).
    fetch_start = anchor_utc - timedelta(hours=1)
    fetch_end = anchor_utc + timedelta(seconds=duration_seconds + 1200)
    symbol_root = pick_symbol_root(
        conn=conn,
        table_name=table_name,
        primary_symbol=primary_symbol,
        fallback_symbol=fallback_symbol,
        start_utc=fetch_start,
        end_utc=fetch_end,
    )
    if symbol_root is None:
        return {
            "date": date,
            "video_id": video_id,
            "anchor_utc": anchor_utc.isoformat(),
            "resolution_used": resolution_used,
            "symbol_used": None,
            "qa_count": len(qa_rows),
            "rows": [],
            "error": "No market rows found for primary/fallback symbols",
        }

    bars = fetch_bars(conn, table_name, symbol_root, fetch_start, fetch_end)
    ts, closes, ts_epoch = prepare_ts_close(bars)

    out_rows: List[Dict[str, Any]] = []
    for answer in qa_rows:
        out = compute_answer_metrics(
            answer=answer,
            anchor_utc=anchor_utc,
            ts=ts,
            closes=closes,
            ts_epoch=ts_epoch,
            resolution_seconds=resolution_seconds,
            symbol_used=symbol_root,
            resolution_used=resolution_used,
        )
        out.update(
            {
                "date": date,
                "video_id": video_id,
            }
        )
        out_rows.append(out)

    return {
        "date": date,
        "video_id": video_id,
        "anchor_utc": anchor_utc.isoformat(),
        "resolution_used": resolution_used,
        "symbol_used": symbol_root,
        "qa_count": len(qa_rows),
        "rows": out_rows,
    }


def resolve_transcripts_dir(path: Path) -> Path:
    if path.exists():
        return path
    if path == DEFAULT_TRANSCRIPTS_DIR and FALLBACK_TRANSCRIPTS_DIR.exists():
        return FALLBACK_TRANSCRIPTS_DIR
    raise FileNotFoundError(f"Transcripts directory not found: {path}")


def main() -> int:
    args = parse_args()
    transcripts_dir = resolve_transcripts_dir(args.transcripts_dir)
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    files = list_transcript_files(transcripts_dir, args.max_transcripts)
    if not files:
        logger.error("No transcript files found in %s", transcripts_dir)
        return 1
    if not HAS_PSYCOPG2:
        logger.error("psycopg2 not installed; cannot fetch market data")
        return 1

    all_rows: List[Dict[str, Any]] = []
    transcript_summaries: List[Dict[str, Any]] = []
    conn = get_pg_connection()
    try:
        table_name, resolution_seconds = pick_table(conn, args.resolution)
        resolution_used = "1s" if table_name == "ohlcv_1s" else "1m"
        logger.info("Using table=%s resolution=%s", table_name, resolution_used)

        for i, fp in enumerate(files, start=1):
            logger.info("Processing %s/%s: %s", i, len(files), fp.name)
            result = process_transcript(
                transcript_path=fp,
                conn=conn,
                table_name=table_name,
                resolution_seconds=resolution_seconds,
                resolution_used=resolution_used,
                primary_symbol=args.primary_symbol,
                fallback_symbol=args.fallback_symbol,
                fallback_time_et=args.anchor_fallback_time_et,
            )
            transcript_summaries.append(
                {
                    "date": result.get("date"),
                    "video_id": result.get("video_id"),
                    "anchor_utc": result.get("anchor_utc"),
                    "qa_count": result.get("qa_count"),
                    "symbol_used": result.get("symbol_used"),
                    "resolution_used": result.get("resolution_used"),
                    "error": result.get("error"),
                }
            )
            all_rows.extend(result.get("rows", []))

            per_file_out = output_dir / f"{result['date']}_{result['video_id']}.json"
            write_json(per_file_out, result)

    finally:
        conn.close()

    write_csv(output_dir / "answer_level_summary.csv", all_rows)
    write_json(output_dir / "run_summary.json", transcript_summaries)
    logger.info("Wrote %s answer rows to %s", len(all_rows), output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

