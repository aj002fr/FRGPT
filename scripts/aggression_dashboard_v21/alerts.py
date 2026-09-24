"""Display-only residual state helper for V2.1.

V2.1 does not write V2 residual alert episodes; its only persistence is the
isolated event paper store.
"""

from __future__ import annotations

from typing import Any


def residual_state(latest: dict[str, Any]) -> dict[str, Any]:
    z_raw = latest.get("residual_z")
    residual = float(latest.get("residual") or 0.0)
    z = float(z_raw) if z_raw is not None else 0.0
    abs_z = abs(z)
    state = "DISLOCATION" if abs_z >= 2.0 else "WATCH" if abs_z >= 1.5 else "NONE"
    side = "SHORT / fade rich" if residual > 0 else "LONG / fade cheap" if residual < 0 else "none"
    return {
        "state": state,
        "direction": side,
        "message": (
            f"{state}: residual z={z:.2f}; contextual V2 fade bias is {side}."
            if state != "NONE"
            else "No active residual dislocation. Event-flow paper tracking is independent."
        ),
    }


__all__ = ["residual_state"]
