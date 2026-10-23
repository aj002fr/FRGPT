#!/usr/bin/env python3
"""
Open the shared Bloomberg workbook, force a refresh, and save it.

Run this on the machine that has Bloomberg Terminal / Excel add-in access.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

try:
    import xlwings as xw
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise ImportError("xlwings is required. Install with: pip install xlwings") from exc


DEFAULT_WORKBOOK = Path(r"\\100.95.155.81\FRGMSharedSpace\Meini\bbg_event_fields.xlsx")
SUMMARY_SHEET = "Summary"
END_DATE_CELL = "B2"
CALC_STATE_DONE = 0
END_DATE_FORMULA = '=TEXT(TODAY(),"YYYY-MM-DD")'
BDH_DATE_ARG_PATTERN = re.compile(
    r'(BDH\(".*?","[^"]*",)\s*"[^"]*"\s*,\s*"[^"]*"',
    re.IGNORECASE,
)


def _cell_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _normalize_bdh_formula(formula: str) -> str:
    if "BDH(" not in formula.upper():
        return formula
    return BDH_DATE_ARG_PATTERN.sub(r"\1$B$1,$B$2", formula, count=1)


def _normalize_path(path_value: str | Path) -> str:
    return str(path_value).replace("/", "\\").strip().lower()


def _attach_to_open_workbook(workbook_path: Path):
    """Attach to the already-open workbook if Excel has it loaded."""
    workbook_name = workbook_path.name
    normalized_target = _normalize_path(workbook_path)

    try:
        workbook = xw.books[workbook_name]
    except KeyError:
        return None
    except Exception as exc:
        print(f"[WARNING] Could not attach to open workbook by name: {exc}")
        return None

    try:
        open_fullname = _normalize_path(workbook.fullname)
    except Exception:
        open_fullname = ""

    if not open_fullname or open_fullname == normalized_target or workbook.name.lower() == workbook_name.lower():
        return workbook

    return None


def _normalize_workbook_formulas(workbook) -> int:
    """Ensure B2 is dynamic and BDH formulas reference $B$1/$B$2."""
    updated_cells = 0
    for sheet in workbook.sheets:
        sheet.range(END_DATE_CELL).formula = END_DATE_FORMULA
        updated_cells += 1

        last_cell = sheet.used_range.last_cell
        for row in range(1, last_cell.row + 1):
            for col in range(1, last_cell.column + 1):
                cell = sheet.cells(row, col)
                formula = cell.formula
                if not isinstance(formula, str) or "BDH(" not in formula.upper():
                    continue
                normalized = _normalize_bdh_formula(formula)
                if normalized != formula:
                    cell.formula = normalized
                    updated_cells += 1
    return updated_cells


def refresh_workbook(workbook_path: Path, timeout_seconds: int, poll_seconds: int) -> int:
    if not workbook_path.exists():
        print(f"[ERROR] Workbook not found: {workbook_path}")
        return 1

    opened_new_app = False
    opened_new_workbook = False
    app = None
    workbook = None
    try:
        workbook = _attach_to_open_workbook(workbook_path)
        if workbook is not None:
            print(f"[INFO] Attached to already-open workbook: {workbook_path.name}")
            app = workbook.app
        else:
            opened_new_app = True
            app = xw.App(visible=False, add_book=False)
            app.display_alerts = False
            app.screen_updating = False

            print(f"[INFO] Opening workbook in a new Excel instance: {workbook_path}")
            workbook = app.books.open(str(workbook_path), update_links=False, read_only=False)
            opened_new_workbook = True

        summary_sheet = workbook.sheets[SUMMARY_SHEET]
        before_value = _cell_to_text(summary_sheet.range(END_DATE_CELL).value)
        print(f"[INFO] {SUMMARY_SHEET}!{END_DATE_CELL} before refresh: {before_value}")

        updated_cells = _normalize_workbook_formulas(workbook)
        print(f"[INFO] Normalized workbook formulas/cells: {updated_cells}")

        try:
            workbook.api.RefreshAll()
        except Exception:
            pass
        workbook.app.api.CalculateFullRebuild()
        try:
            workbook.app.api.CalculateUntilAsyncQueriesDone()
        except Exception:
            # Not all Excel installations expose this helper.
            pass

        deadline = time.time() + timeout_seconds
        last_value = before_value
        while time.time() < deadline:
            calc_state = workbook.app.api.CalculationState
            last_value = _cell_to_text(summary_sheet.range(END_DATE_CELL).value)
            print(
                f"[INFO] Waiting for refresh... calculation_state={calc_state}, "
                f"{SUMMARY_SHEET}!{END_DATE_CELL}={last_value}"
            )
            if calc_state == CALC_STATE_DONE and last_value:
                break
            time.sleep(poll_seconds)
        else:
            print("[ERROR] Timed out waiting for Excel/Bloomberg refresh to complete.")
            return 1

        workbook.save()
        print(f"[INFO] Workbook saved: {workbook_path}")
        print(f"[INFO] {SUMMARY_SHEET}!{END_DATE_CELL} after refresh: {last_value}")
        return 0
    finally:
        if workbook is not None and opened_new_workbook:
            try:
                workbook.close()
            except Exception:
                pass
        if app is not None and opened_new_app:
            try:
                app.quit()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the shared Bloomberg Excel workbook.")
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help="Path to the shared Bloomberg workbook",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Max seconds to wait for Excel/Bloomberg refresh",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=5,
        help="Poll interval in seconds while waiting for refresh",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout < 1:
        print("[ERROR] --timeout must be >= 1")
        return 1
    if args.poll < 1:
        print("[ERROR] --poll must be >= 1")
        return 1
    return refresh_workbook(
        workbook_path=args.workbook,
        timeout_seconds=args.timeout,
        poll_seconds=args.poll,
    )


if __name__ == "__main__":
    raise SystemExit(main())
