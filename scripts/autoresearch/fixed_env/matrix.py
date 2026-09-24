"""Checksummed adapter separating candidate formation rows from evaluator data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .data_access import contained_path
from .preregister import digest_file

ET = ZoneInfo("America/New_York")
SPLIT_KEYS = {
    "discovery": "discovery_dates",
    "validation": "validation_dates",
    "quarantine": "historical_quarantine_dates",
}


@dataclass(frozen=True)
class MatrixSlice:
    split: str
    dates: tuple[str, ...]
    candidate_rows: Sequence[Mapping[str, Any]]
    evaluator_frame: pd.DataFrame


class CandidateRows(Sequence[Mapping[str, Any]]):
    """Lazy target-free projection that avoids one Python dict per matrix row."""

    def __init__(self, frame: pd.DataFrame, columns: Sequence[str]) -> None:
        self.frame = frame
        self.columns = tuple(columns)

    def __len__(self) -> int:
        return len(self.frame)

    def __getitem__(self, index: int | slice) -> Any:
        if isinstance(index, slice):
            return [
                self[item] for item in range(*index.indices(len(self)))
            ]
        timestamp = self.frame.index[index]
        values = self.frame.iloc[index]
        row: dict[str, Any] = {
            "formation_ts": timestamp.isoformat().replace("+00:00", "Z"),
        }
        for column in self.columns:
            if column != "formation_ts":
                value = values[column]
                row[column] = None if pd.isna(value) else float(value)
        return row

    def select(self, timestamps: pd.Index) -> "CandidateRows":
        return CandidateRows(self.frame.loc[timestamps], self.columns)


class FrozenMatrix:
    def __init__(
        self,
        version_dir: str | Path,
        split_manifest: str | Path,
        event_manifest: str | Path,
        *,
        expected_matrix_hash: str,
        expected_manifest_hash: str | None = None,
        roll_manifest: str | Path | None = None,
        expected_roll_hash: str | None = None,
    ) -> None:
        self.version_dir = Path(version_dir).resolve()
        self.data_root = self.version_dir.parents[1]
        self.version_manifest_path = self.version_dir / "manifest.json"
        self.matrix_path = self.version_dir / "tbbo_feature_matrix.parquet"
        if expected_manifest_hash and digest_file(self.version_manifest_path) != expected_manifest_hash:
            raise ValueError("publication manifest hash mismatch")
        publication = json.loads(self.version_manifest_path.read_text(encoding="utf-8"))
        if publication.get("matrix_sha256") != expected_matrix_hash:
            raise ValueError("publication matrix binding mismatch")
        if digest_file(self.matrix_path) != expected_matrix_hash:
            raise ValueError("published matrix checksum mismatch")
        self.publication = publication
        self.split_manifest = json.loads(Path(split_manifest).read_text(encoding="utf-8"))
        self.event_manifest = json.loads(Path(event_manifest).read_text(encoding="utf-8"))
        self.roll_dates: set[str] | None = None
        if roll_manifest is not None:
            roll_path = Path(roll_manifest)
            if expected_roll_hash and digest_file(roll_path) != expected_roll_hash:
                raise ValueError("roll manifest hash mismatch")
            roll_payload = json.loads(roll_path.read_text(encoding="utf-8"))
            self.roll_dates = set(roll_payload["roll_dates"])
        self._date_entries = {
            item["trading_date"]: item for item in publication["date_manifests"]
            if item.get("checksums")
        }

    def assigned_dates(self, split: str) -> tuple[str, ...]:
        if split not in SPLIT_KEYS:
            raise ValueError("only frozen historical splits can be loaded")
        values = tuple(self.split_manifest[SPLIT_KEYS[split]])
        if len(values) != len(set(values)):
            raise ValueError("split manifest contains duplicate dates")
        return values

    def load(
        self,
        split: str,
        allowed_inputs: Sequence[str],
        *,
        dates: Sequence[str] | None = None,
        evaluator_inputs: Sequence[str] = (),
    ) -> MatrixSlice:
        assigned = self.assigned_dates(split)
        selected = tuple(dates or assigned)
        if not selected or not set(selected).issubset(assigned):
            raise PermissionError("requested dates are not assigned to split")
        frames = [self._load_date(day) for day in selected]
        frame = pd.concat(frames).sort_index(kind="stable")
        if not frame.index.is_unique or frame.index.tz is None:
            raise ValueError("matrix timestamps must be unique UTC values")
        candidate = self._candidate_rows(frame, allowed_inputs)
        evaluator = self._evaluator_frame(frame, selected, evaluator_inputs)
        return MatrixSlice(split, selected, candidate, evaluator)

    def _load_date(self, day: str) -> pd.DataFrame:
        entry = self._date_entries.get(day)
        if not entry:
            raise ValueError(f"date absent from publication: {day}")
        manifest_path = contained_path(entry["manifest"], [self.data_root])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        feature_path = contained_path(manifest["artifacts"]["features"], [self.data_root])
        expected = entry["checksums"]["features"]
        if manifest["checksums"]["features"] != expected or digest_file(feature_path) != expected:
            raise ValueError(f"feature shard checksum mismatch: {day}")
        frame = pd.read_parquet(feature_path)
        frame.index = pd.to_datetime(frame.index, utc=True).as_unit("ns")
        if set(frame["et_date"].astype(str)) != {day}:
            raise ValueError(f"feature shard date mismatch: {day}")
        return frame

    @staticmethod
    def _candidate_rows(
        frame: pd.DataFrame, allowed_inputs: Sequence[str]
    ) -> CandidateRows:
        columns = tuple(allowed_inputs)
        missing = set(columns) - (set(frame.columns) | {"formation_ts"})
        if missing:
            raise ValueError(f"candidate columns missing from matrix: {sorted(missing)}")
        projected = [column for column in columns if column != "formation_ts"]
        return CandidateRows(frame[projected].copy(), columns)

    def _evaluator_frame(
        self,
        frame: pd.DataFrame,
        selected: Sequence[str],
        evaluator_inputs: Sequence[str],
    ) -> pd.DataFrame:
        columns = tuple(dict.fromkeys((
            "price", "et_date", "minutes_since_open", "session_sin", "session_cos",
            *evaluator_inputs,
        )))
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"evaluator columns missing from matrix: {sorted(missing)}")
        result = frame[list(columns)].copy()
        local = result.index.tz_convert(ET)
        minute = local.hour * 60 + local.minute
        result["session"] = np.select(
            [(minute >= 1080) | (minute < 180), minute < 480, minute <= 900],
            ["ASIA", "LONDON", "RATES_RTH"], default="LATE",
        )
        event_mask = np.zeros(len(result), dtype=bool)
        before = int(self.event_manifest["blackout_minutes"]["before"])
        after = int(self.event_manifest["blackout_minutes"]["after"])
        for event in self.event_manifest["events"]:
            timestamp = pd.Timestamp(event["event_ts_utc"])
            event_mask |= (
                (result.index >= timestamp - pd.Timedelta(minutes=before))
                & (result.index <= timestamp + pd.Timedelta(minutes=after))
            )
        result["event_blackout"] = event_mask
        if self.roll_dates is not None:
            rolls = {day: day in self.roll_dates for day in selected}
        else:
            published_dates = sorted(self._date_entries)
            positions = {day: index for index, day in enumerate(published_dates)}
            rolls = {}
            for day in selected:
                position = positions[day]
                rolls[day] = (
                    position > 0
                    and self._ty_front(day) != self._ty_front(published_dates[position - 1])
                )
        result["roll_flag"] = result["et_date"].astype(str).map(rolls).fillna(False)
        if not np.isfinite(result["price"].to_numpy(dtype=float)).all():
            raise ValueError("evaluator prices must be finite")
        return result

    def _ty_front(self, day: str) -> str:
        manifest = json.loads(Path(self._date_entries[day]["manifest"]).read_text(encoding="utf-8"))
        tables = manifest["front_selection"]["prior_volume_table"]
        ty = next(item for item in tables if item["root"] == "TY")
        return str(ty["candidates"][0]["contract"])
