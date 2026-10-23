"""Generate / verify SHA-256 pins for the Move Exhaustion V2 frozen surface.

Pinned objects:
  1. Every ``config/frozen/*.yaml`` — full file content.
  2. Every ``experiments/X*/CONTRACT.md`` — only the section between
     '## Preregistered scientific content' and the next '## ' heading, so that
     appending run-log references does not break the pin.
  3. ``research/PROGRAM.md`` — content up to (excluding) '## 10. Change log',
     so that dated addenda may be appended to §10 without breaking the pin.

Text is normalized to '\n' line endings before hashing (editor/git churn safety).

Usage:
    py -3 scripts/move_exhaustion_v2/tools/freeze_manifest.py            # verify
    py -3 scripts/move_exhaustion_v2/tools/freeze_manifest.py --write    # (re)generate

Regenerating the manifest is itself a governance event: it is only legitimate
after a user-approved, dated addendum in research/PROGRAM.md §10.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PKG_ROOT / "config" / "frozen" / "MANIFEST.sha256"

CONTRACT_SECTION_HEADING = "## Preregistered scientific content"
PROGRAM_CUTOFF_HEADING = "## 10. Change log"


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _contract_section(path: Path) -> str:
    """Extract the frozen scientific section of a contract file."""
    lines = _norm(path.read_text(encoding="utf-8")).split("\n")
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None and line.startswith(CONTRACT_SECTION_HEADING):
            start = i
        elif start is not None and i > start and line.startswith("## "):
            end = i
            break
    if start is None:
        raise ValueError(f"{path}: missing '{CONTRACT_SECTION_HEADING}' section")
    return "\n".join(lines[start:end]).rstrip("\n")


def _program_frozen_part(path: Path) -> str:
    text = _norm(path.read_text(encoding="utf-8"))
    idx = text.find(PROGRAM_CUTOFF_HEADING)
    if idx < 0:
        raise ValueError(f"{path}: missing '{PROGRAM_CUTOFF_HEADING}' heading")
    return text[:idx].rstrip("\n")


def collect_entries() -> dict[str, str]:
    """Return {logical_key: sha256} for every pinned object."""
    entries: dict[str, str] = {}

    frozen_dir = PKG_ROOT / "config" / "frozen"
    for yml in sorted(frozen_dir.glob("*.yaml")):
        key = f"config/frozen/{yml.name}"
        entries[key] = _sha256(_norm(yml.read_text(encoding="utf-8")).rstrip("\n"))

    for contract in sorted((PKG_ROOT / "experiments").glob("X*/CONTRACT.md")):
        key = f"experiments/{contract.parent.name}/CONTRACT.md#preregistered"
        entries[key] = _sha256(_contract_section(contract))

    program = PKG_ROOT / "research" / "PROGRAM.md"
    entries["research/PROGRAM.md#sections0-9"] = _sha256(_program_frozen_part(program))

    return entries


def read_manifest() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"manifest not found: {MANIFEST_PATH}")
    out: dict[str, str] = {}
    for line in _norm(MANIFEST_PATH.read_text(encoding="utf-8")).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, key = line.split(None, 1)
        out[key.strip()] = digest.strip()
    return out


def write_manifest() -> None:
    entries = collect_entries()
    lines = [
        "# Move Exhaustion V2 — frozen-surface SHA-256 manifest",
        "# Regeneration requires a user-approved dated addendum in research/PROGRAM.md §10.",
        "# Verify: py -3 -m pytest scripts/move_exhaustion_v2/tests/test_frozen_config_integrity.py",
    ]
    lines += [f"{digest}  {key}" for key, digest in sorted(entries.items())]
    MANIFEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH} ({len(entries)} entries)")


def verify() -> list[str]:
    """Return a list of human-readable violations (empty = clean)."""
    problems: list[str] = []
    expected = read_manifest()
    actual = collect_entries()
    for key, digest in expected.items():
        if key not in actual:
            problems.append(f"MISSING pinned object: {key}")
        elif actual[key] != digest:
            problems.append(f"HASH MISMATCH (frozen content changed): {key}")
    for key in actual:
        if key not in expected:
            problems.append(f"UNPINNED frozen object (regenerate manifest via governance): {key}")
    return problems


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_manifest()
    else:
        issues = verify()
        if issues:
            print("FROZEN-SURFACE INTEGRITY VIOLATIONS:")
            for p in issues:
                print(f"  - {p}")
            sys.exit(1)
        print("frozen surface intact")
