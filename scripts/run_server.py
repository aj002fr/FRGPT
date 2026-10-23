#!/usr/bin/env python3
"""
Production-ready Dashboard Server Runner
-----------------------------------------
Runs the dashboard with multiple threads for concurrent request handling.

Usage:
    python scripts/run_server.py --event CPI --html path/to/dashboard.html
    python scripts/run_server.py --event CPI --html path/to/dashboard.html --workers 4
"""

import sys
import os
import socket
from typing import List
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.live_unified_dashboard import (
    create_dashboard_app,
    build_live_unified_dashboard,
    LIVE_SERVER_PORT,
)
from scripts.event_data_loader import get_supported_events


def _get_local_ip() -> str | None:
    """Best-effort local IP detection for display purposes."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return None


def _format_server_urls(host: str, port: int) -> List[str]:
    """Return a list of human-friendly URLs for the given bind host/port."""
    if host in {"0.0.0.0", "::"}:
        urls = [f"http://localhost:{port}/"]
        local_ip = _get_local_ip()
        if local_ip:
            urls.append(f"http://{local_ip}:{port}/")
        return urls
    return [f"http://{host}:{port}/"]


def create_app(event_type: str = "CPI", html_path: str = None, enable_socketio: bool = False):
    """
    Application factory for WSGI servers (Waitress).
    
    Args:
        event_type: "NFP" or "CPI"
        html_path: Path to the pre-generated HTML dashboard
        enable_socketio: Whether to enable WebSocket (default: False for WSGI)
    
    Returns:
        Flask app instance (or tuple with socketio if enabled)
    """
    if html_path is None:
        # Build dashboard if no path provided
        result = build_live_unified_dashboard(event_type=event_type)
        if not result["success"]:
            raise RuntimeError(f"Failed to build dashboard: {result.get('error')}")
        html_path = result["html_path"]
    
    result = create_dashboard_app(html_path, event_type, enable_socketio=enable_socketio)
    
    # Handle both return types
    if isinstance(result, tuple):
        app, socketio = result
        return app  # WSGI servers need just the app
    return result


def run_with_waitress(app, host: str, port: int, threads: int = 8):
    """Run with Waitress (WSGI server)."""
    try:
        from waitress import serve
        print(f"\n[Server] Starting Waitress with {threads} threads...")
        for url in _format_server_urls(host, port):
            print(f"[Server] Dashboard: {url}")
            print(f"[Server] Live API:  {url}api/live")
        print("[Server] Press Ctrl+C to stop.\n")
        serve(app, host=host, port=port, threads=threads)
    except ImportError:
        print("[Server] Waitress not installed. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "waitress", "-q"])
        from waitress import serve
        serve(app, host=host, port=port, threads=threads)


def main():
    import argparse
    
    supported_events = [str(e).upper() for e in (get_supported_events() or ["NFP", "CPI"])]
    parser = argparse.ArgumentParser(
        description="Run the dashboard server with multiple workers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/run_server.py --event CPI
    python scripts/run_server.py --event NFP --workers 8
    python scripts/run_server.py --event PPI
    python scripts/run_server.py --event JOLTS
    python scripts/run_server.py --event CPI --html workspace/scenarios/my_dashboard.html
        """
    )
    parser.add_argument(
        "--event",
        type=str,
        default="CPI",
        choices=supported_events,
        help=f"Event type (default: CPI). Supported: {', '.join(supported_events)}",
    )
    parser.add_argument("--html", type=str, default=None,
                        help="Path to pre-generated HTML dashboard (builds new if not provided)")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=LIVE_SERVER_PORT,
                        help=f"Port to bind to (default: {LIVE_SERVER_PORT})")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of worker processes (default: 4)")
    parser.add_argument("--threads", type=int, default=2,
                        help="Threads per worker (default: 2)")
    parser.add_argument("--refresh", action="store_true",
                        help="Force rebuild dashboard from scratch")
    
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  MULTI-USER DASHBOARD SERVER")
    print(f"{'='*60}")
    print(f"Event Type: {args.event}")
    print(f"Workers: {args.workers} x {args.threads} threads = {args.workers * args.threads} concurrent")
    print(f"Bind: {args.host}:{args.port}")
    print(f"{'='*60}")
    
    # Build or use existing dashboard
    if args.html and Path(args.html).exists() and not args.refresh:
        html_path = args.html
        print(f"Using existing dashboard: {html_path}")
    else:
        print(f"Building {args.event} dashboard...")
        result = build_live_unified_dashboard(
            event_type=args.event,
            force_refresh=args.refresh
        )
        if not result["success"]:
            print(f"Error: {result.get('error')}")
            sys.exit(1)
        html_path = result["html_path"]
        print(f"Dashboard ready: {html_path}")
    
    # Create app (disable socketio for WSGI servers - they don't support it)
    app = create_dashboard_app(html_path, args.event, enable_socketio=False)
    
    run_with_waitress(app, args.host, args.port, threads=args.workers * args.threads)


if __name__ == "__main__":
    main()

