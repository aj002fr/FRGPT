#!/usr/bin/env python3
"""
FOMC Press Conference Sentiment (Step 2)
========================================
Calibrate a 5-class sentiment mapping using TY 1-minute price action.

Inputs:
- Step 1 outputs: workspace/fed/fomc conference/sentiment/{date}_{video_id}.json

Outputs:
- Step 2 per-transcript JSON:
  workspace/fed/fomc conference/sentiment_step2/{date}_{video_id}.json
- Step 2 summary CSV:
  workspace/fed/fomc conference/sentiment_step2/summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).parent.parent
STEP1_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "sentiment"
STEP2_DIR = PROJECT_ROOT / "workspace" / "fed" / "fomc conference" / "sentiment_step2"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("FOMCSentimentStep2")


LABELS = ["very dovish", "dovish", "neutral", "hawkish", "very hawkish"]
LABEL_ORDER = {
    "very dovish": -2,
    "dovish": -1,
    "neutral": 0,
    "hawkish": 1,
    "very hawkish": 2,
}


@dataclass
class CalibrationResult:
    weight_chunk: float
    weight_running: float
    score_neutral: float
    score_very: float
    accuracy: float
    sample_count: int


def load_step1_results(
    max_transcripts: Optional[int] = None,
    random_sample: bool = False,
) -> List[Dict[str, Any]]:
    json_files = sorted(STEP1_DIR.glob("*.json"))
    json_files = [f for f in json_files if f.name != "index.json"]

    if random_sample and max_transcripts and max_transcripts < len(json_files):
        json_files = random.sample(json_files, max_transcripts)
        json_files = sorted(json_files)
    elif max_transcripts:
        json_files = json_files[:max_transcripts]

    results = []
    for fp in json_files:
        with open(fp, "r", encoding="utf-8") as f:
            results.append(json.load(f))

    logger.info(f"Loaded {len(results)} Step 1 transcripts")
    return results


def price_label_from_ticks(
    delta_ticks: Optional[float],
    neutral_ticks: float,
    very_ticks: float,
) -> Optional[str]:
    if delta_ticks is None:
        return None

    if delta_ticks <= -very_ticks:
        return "very hawkish"
    if delta_ticks < -neutral_ticks:
        return "hawkish"
    if delta_ticks > neutral_ticks and delta_ticks < very_ticks:
        return "dovish"
    if delta_ticks >= very_ticks:
        return "very dovish"
    return "neutral"


def label_from_score(
    score: Optional[float],
    neutral_threshold: float,
    very_threshold: float,
) -> Optional[str]:
    if score is None:
        return None

    if score >= very_threshold:
        return "very hawkish"
    if score > neutral_threshold:
        return "hawkish"
    if score <= -very_threshold:
        return "very dovish"
    if score < -neutral_threshold:
        return "dovish"
    return "neutral"


def combine_scores(
    chunk_score: Optional[float],
    running_score: Optional[float],
    weight_chunk: float,
    weight_running: float,
) -> Optional[float]:
    if chunk_score is None and running_score is None:
        return None
    if running_score is None:
        return chunk_score
    if chunk_score is None:
        return running_score
    return weight_chunk * chunk_score + weight_running * running_score


def collect_training_rows(
    results: List[Dict[str, Any]],
    price_window: str,
    neutral_ticks: float,
    very_ticks: float,
) -> List[Dict[str, Any]]:
    rows = []
    for result in results:
        for chunk in result.get("chunks", []):
            pw = chunk.get("price_windows", {}).get(price_window, {})
            delta_ticks = pw.get("delta_ticks")
            price_label = price_label_from_ticks(delta_ticks, neutral_ticks, very_ticks)
            if price_label is None:
                continue
            rows.append({
                "chunk_score": chunk.get("chunk_score"),
                "running_score": chunk.get("running_score"),
                "price_label": price_label,
            })
    return rows


def calibrate_mapping(
    rows: List[Dict[str, Any]],
    weight_grid: List[float],
    score_neutral_grid: List[float],
    score_very_grid: List[float],
) -> Optional[CalibrationResult]:
    best: Optional[CalibrationResult] = None

    for weight_chunk in weight_grid:
        weight_running = 1.0 - weight_chunk
        for score_neutral in score_neutral_grid:
            for score_very in score_very_grid:
                if score_very <= score_neutral:
                    continue

                matches = 0
                total = 0
                for row in rows:
                    combined = combine_scores(
                        row.get("chunk_score"),
                        row.get("running_score"),
                        weight_chunk,
                        weight_running,
                    )
                    pred = label_from_score(combined, score_neutral, score_very)
                    if pred is None:
                        continue
                    total += 1
                    if pred == row["price_label"]:
                        matches += 1

                if total == 0:
                    continue

                accuracy = matches / total
                if best is None or accuracy > best.accuracy:
                    best = CalibrationResult(
                        weight_chunk=weight_chunk,
                        weight_running=weight_running,
                        score_neutral=score_neutral,
                        score_very=score_very,
                        accuracy=accuracy,
                        sample_count=total,
                    )

    return best


def compute_alert_flags(
    chunks: List[Dict[str, Any]],
    min_minutes: int = 2,
) -> List[Dict[str, Any]]:
    last_label: Optional[str] = None
    last_alert_minute: Optional[int] = None

    for chunk in chunks:
        label = chunk.get("calibrated_label")
        start_s = chunk.get("start_s", 0.0)
        minute_idx = int(start_s // 60)

        alert = False
        reason = None

        if label in LABEL_ORDER:
            label_value = LABEL_ORDER[label]

            if label == "neutral":
                alert = False
            else:
                extreme = abs(label_value) == 2
                change = last_label is not None and label != last_label and label != "neutral"
                strengthening = False

                if last_label in LABEL_ORDER:
                    last_value = LABEL_ORDER[last_label]
                    strengthening = (
                        label_value != 0
                        and last_value != 0
                        and (label_value > 0) == (last_value > 0)
                        and abs(label_value) > abs(last_value)
                    )

                if extreme:
                    reason = "extreme"
                    alert = True
                elif change:
                    reason = "change"
                    alert = True
                elif strengthening:
                    reason = "strengthening"
                    alert = True

                if alert and last_alert_minute is not None:
                    if minute_idx - last_alert_minute < min_minutes:
                        alert = False
                        reason = None

        chunk["alert"] = alert
        chunk["alert_reason"] = reason

        if alert:
            last_alert_minute = minute_idx
        if label in LABEL_ORDER:
            last_label = label

    return chunks


def process_transcript(
    result: Dict[str, Any],
    price_window: str,
    calibration: CalibrationResult,
    neutral_ticks: float,
    very_ticks: float,
    min_alert_minutes: int,
) -> Dict[str, Any]:
    chunks_out = []

    for chunk in result.get("chunks", []):
        pw = chunk.get("price_windows", {}).get(price_window, {})
        delta_ticks = pw.get("delta_ticks")
        price_label = price_label_from_ticks(delta_ticks, neutral_ticks, very_ticks)

        combined = combine_scores(
            chunk.get("chunk_score"),
            chunk.get("running_score"),
            calibration.weight_chunk,
            calibration.weight_running,
        )
        calibrated_label = label_from_score(
            combined,
            calibration.score_neutral,
            calibration.score_very,
        )

        chunks_out.append({
            "idx": chunk.get("idx"),
            "start_s": chunk.get("start_s"),
            "end_s": chunk.get("end_s"),
            "text": chunk.get("text"),
            "chunk_score": chunk.get("chunk_score"),
            "running_score": chunk.get("running_score"),
            "combined_score": combined,
            "price_window": price_window,
            "price_delta_ticks": delta_ticks,
            "price_direction": pw.get("direction"),
            "price_label": price_label,
            "calibrated_label": calibrated_label,
        })

    chunks_out = compute_alert_flags(chunks_out, min_minutes=min_alert_minutes)

    # Transcript-level summary
    combined_scores = [c["combined_score"] for c in chunks_out if c["combined_score"] is not None]
    final_combined = sum(combined_scores) / len(combined_scores) if combined_scores else None
    final_label = label_from_score(
        final_combined,
        calibration.score_neutral,
        calibration.score_very,
    )

    match_count = 0
    match_total = 0
    for c in chunks_out:
        if c["price_label"] is None or c["calibrated_label"] is None:
            continue
        match_total += 1
        if c["price_label"] == c["calibrated_label"]:
            match_count += 1
    match_rate = match_count / match_total if match_total else None

    return {
        "date": result.get("date"),
        "video_id": result.get("video_id"),
        "anchor_utc": result.get("anchor_utc"),
        "duration_seconds": result.get("duration_seconds"),
        "chunk_count": len(chunks_out),
        "price_window": price_window,
        "calibration": {
            "weight_chunk": calibration.weight_chunk,
            "weight_running": calibration.weight_running,
            "score_neutral": calibration.score_neutral,
            "score_very": calibration.score_very,
            "accuracy": calibration.accuracy,
            "sample_count": calibration.sample_count,
        },
        "final_combined_score": final_combined,
        "final_calibrated_label": final_label,
        "calibrated_match_rate": match_rate,
        "chunks": chunks_out,
    }


def write_summary_csv(results: List[Dict[str, Any]], output_path: Path) -> None:
    rows = []
    for result in results:
        if "chunks" not in result:
            continue
        for chunk in result["chunks"]:
            rows.append({
                "date": result.get("date"),
                "video_id": result.get("video_id"),
                "chunk_idx": chunk.get("idx"),
                "start_s": chunk.get("start_s"),
                "end_s": chunk.get("end_s"),
                "price_window": chunk.get("price_window"),
                "chunk_score": chunk.get("chunk_score"),
                "running_score": chunk.get("running_score"),
                "combined_score": chunk.get("combined_score"),
                "price_delta_ticks": chunk.get("price_delta_ticks"),
                "price_direction": chunk.get("price_direction"),
                "price_label": chunk.get("price_label"),
                "calibrated_label": chunk.get("calibrated_label"),
                "alert": chunk.get("alert"),
                "alert_reason": chunk.get("alert_reason"),
            })

    if not rows:
        logger.warning("No rows for Step 2 summary CSV")
        return

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Step 2 summary CSV written to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FOMC sentiment mapping calibration (Step 2)")
    parser.add_argument("--max-transcripts", type=int, default=None, help="Limit transcripts")
    parser.add_argument("--random", action="store_true", help="Randomly sample transcripts")
    parser.add_argument("--price-window", type=str, default="same", choices=["same", "lag1m"], help="Price window for calibration")
    parser.add_argument("--ticks-neutral", type=float, default=1.0, help="Neutral price move threshold (ticks)")
    parser.add_argument("--ticks-very", type=float, default=3.0, help="Very hawkish/dovish threshold (ticks)")
    parser.add_argument("--min-alert-minutes", type=int, default=2, help="Minimum minutes between alerts")
    args = parser.parse_args()

    STEP2_DIR.mkdir(parents=True, exist_ok=True)

    results = load_step1_results(args.max_transcripts, random_sample=args.random)
    if not results:
        logger.error("No Step 1 transcripts found")
        return 1

    training_rows = collect_training_rows(
        results,
        price_window=args.price_window,
        neutral_ticks=args.ticks_neutral,
        very_ticks=args.ticks_very,
    )
    if not training_rows:
        logger.error("No training rows found for calibration")
        return 1

    weight_grid = [0.0, 0.25, 0.5, 0.75, 1.0]
    score_neutral_grid = [1.5, 2.0, 2.5]
    score_very_grid = [5.0, 6.0, 7.0, 8.0]

    calibration = calibrate_mapping(
        training_rows,
        weight_grid=weight_grid,
        score_neutral_grid=score_neutral_grid,
        score_very_grid=score_very_grid,
    )
    if calibration is None:
        logger.error("Calibration failed")
        return 1

    logger.info(
        "Calibration: w_chunk=%.2f, w_run=%.2f, score_neutral=%.2f, score_very=%.2f, accuracy=%.2f (n=%d)",
        calibration.weight_chunk,
        calibration.weight_running,
        calibration.score_neutral,
        calibration.score_very,
        calibration.accuracy,
        calibration.sample_count,
    )

    outputs = []
    for result in results:
        processed = process_transcript(
            result,
            price_window=args.price_window,
            calibration=calibration,
            neutral_ticks=args.ticks_neutral,
            very_ticks=args.ticks_very,
            min_alert_minutes=args.min_alert_minutes,
        )
        outputs.append(processed)

        date = processed.get("date", "unknown")
        video_id = processed.get("video_id", "unknown")
        out_path = STEP2_DIR / f"{date}_{video_id}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(processed, f, indent=2, ensure_ascii=False)
        logger.info("Written: %s", out_path)

    summary_path = STEP2_DIR / "summary.csv"
    write_summary_csv(outputs, summary_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

