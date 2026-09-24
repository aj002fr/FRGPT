#!/usr/bin/env python3
"""
Standalone Pre-Event Dashboard (Embedded HTML)
----------------------------------------------
Creates a self-contained pre-event-only HTML dashboard (no server required),
similar to the standalone post-event dashboards (script-driven, not run out of
the unified dashboard server).

Implementation notes:
- Reuses the unified dashboard build pipeline as a library (data loading + tab HTML/JS),
  but writes a dedicated standalone artifact under workspace/scenarios/.
- Event release metadata (actual/consensus/consensus_std_dev) comes from
  workspace/economic_events.db (bbg_data table) via scripts/event_data_loader.py.
"""

import argparse
from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def build_standalone_preevent_dashboard(
    event_type: str,
    consensus: float | None,
    cons_std: float | None,
    fwd_be: float | None,
    force_refresh: bool,
    output_path: Path | None,
) -> Path:
    from scripts import live_unified_dashboard as lud

    event_type = (event_type or "").upper().strip()
    supported = lud.get_supported_events()
    if event_type not in supported:
        raise ValueError(f"Unknown event type '{event_type}'. Supported: {supported}")

    # Load cached dashboard data when possible (same semantics as build_live_unified_dashboard)
    dashboard_data = None
    if not force_refresh:
        dashboard_data = lud.load_cached_dashboard_data(event_type)

    if dashboard_data is None:
        event_data = lud.load_event_data(event_type)
        # If no data is available, still generate an empty template HTML so the
        # artifact exists and the UI can show a clear "0 events" state.
        if event_data is None:
            event_data = []
        dashboard_data = lud.build_dashboard_data(
            event_data,
            user_cons_std=cons_std,
            user_consensus=consensus,
            user_fwd_be=fwd_be,
            user_actual=None,
            event_type=event_type,
        )
        lud.save_dashboard_cache(event_type, dashboard_data)
    else:
        # Update user-provided overrides even when using cache
        if cons_std is not None:
            dashboard_data["user_cons_std"] = cons_std
        if consensus is not None:
            dashboard_data["user_consensus"] = consensus
        if fwd_be is not None:
            dashboard_data["user_fwd_be"] = fwd_be

    dashboard_data["event_preselected"] = True

    # Default output path (dedicated naming for standalone pre-event artifacts)
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = getattr(lud, "OUTPUT_DIR", PROJECT_ROOT / "workspace" / "scenarios")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"preevent_{event_type.lower()}_dashboard_{ts}.html"

    html_path = lud.generate_html_dashboard(
        dashboard_data,
        output_path=output_path,
        show_live=False,      # historical-only
        embed_data=True,      # self-contained data
        embed_plotly=True,    # inline Plotly for offline viewing
        preevent_only=True,   # hide other tabs
    )
    return Path(html_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a standalone pre-event-only HTML dashboard (embedded, no server).",
        epilog="Examples:\n"
               "  python scripts/preevent_standalone_dashboard.py --event NFP --fwd_be 6.5 --cons_std 26.1\n"
               "  python scripts/preevent_standalone_dashboard.py --event CPI --cons_std 0.05\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--event", type=str, required=True, help="Event type (e.g., NFP, CPI, PPI, JOLTS)")
    parser.add_argument("--consensus", type=float, default=None, help="Consensus override (optional)")
    parser.add_argument("--cons_std", type=float, default=None, help="Consensus std dev override (optional)")
    parser.add_argument("--fwd_be", type=float, default=None, help="Forward breakeven in bps (required for NFP)")
    parser.add_argument("--refresh", action="store_true", help="Force rebuild from scratch (bypass cache)")
    parser.add_argument("--out", type=str, default=None, help="Output HTML path (optional)")
    args = parser.parse_args()

    if args.event.upper().strip() == "NFP" and args.fwd_be is None:
        raise ValueError("--fwd_be is required for NFP (runtime value).")

    out_path = Path(args.out) if args.out else None
    html_path = build_standalone_preevent_dashboard(
        event_type=args.event,
        consensus=args.consensus,
        cons_std=args.cons_std,
        fwd_be=args.fwd_be,
        force_refresh=args.refresh,
        output_path=out_path,
    )

    print(f"Standalone pre-event HTML saved to: {html_path}")


if __name__ == "__main__":
    main()

