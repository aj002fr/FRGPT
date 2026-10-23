#!/usr/bin/env python3
"""
Treasury Futures Price Conversion Utilities
============================================

For 10-Year US Treasury Bond Futures (TY or ZN):

Price Notation:
- Prices are quoted in points and 32nds (ticks)
- Format: PPP'TT where PPP = points, TT = ticks (32nds)
- Half-ticks use trailing 5: 112'015 = 112 + 1.5/32

Conversions:
- 1 point = 32 ticks (32nds)
- 1 tick = 1/4 bp (quarter basis point)
- 4 ticks = 1 bp
- 1 point = 8 bps

Examples:
- 112'00 = 112.0 decimal
- 112'08 = 112.25 decimal (8/32 = 0.25)
- 112'01 = 112.03125 decimal (1/32 = 0.03125)
- 112'16 = 112.5 decimal (16/32 = 0.5)

User Preference: Display in bond convention (ticks), not basis points.
"""

# Constants
TICKS_PER_POINT = 32
TICKS_PER_BP = 4
BPS_PER_POINT = 8  # 32 ticks / 4 ticks per bp


def decimal_to_ticks(decimal_price: float) -> float:
    """
    Convert decimal price to ticks (32nds).
    
    Example: 112.25 -> 3592 ticks (112 * 32 + 8)
    """
    return decimal_price * TICKS_PER_POINT


def ticks_to_decimal(ticks: float) -> float:
    """
    Convert ticks to decimal price.
    
    Example: 3592 ticks -> 112.25
    """
    return ticks / TICKS_PER_POINT


def decimal_to_bond_notation(decimal_price: float) -> str:
    """
    Convert decimal price to bond notation (PPP'TT or PPP'TT.5).
    
    Example: 112.25 -> "112'08"
    Example: 112.015625 -> "112'005"
    """
    points = int(decimal_price)
    remainder = decimal_price - points
    ticks = remainder * TICKS_PER_POINT
    
    # Check for half-tick
    if ticks % 1 == 0.5:
        whole_ticks = int(ticks)
        return f"{points}'{whole_ticks:02d}5"
    else:
        return f"{points}'{int(ticks):02d}"


def bond_notation_to_decimal(notation: str) -> float:
    """
    Convert bond notation to decimal price.
    
    Example: "112'08" -> 112.25
    Example: "112'005" -> 112.015625
    """
    parts = notation.replace("'", " ").split()
    points = int(parts[0])
    tick_str = parts[1]
    
    # Check for half-tick (trailing 5 after 2 digits)
    if len(tick_str) == 3 and tick_str.endswith('5'):
        ticks = int(tick_str[:2]) + 0.5
    else:
        ticks = int(tick_str)
    
    return points + (ticks / TICKS_PER_POINT)


def points_to_ticks(points: float) -> float:
    """
    Convert a price move in points to ticks.
    
    Example: 0.25 points -> 8 ticks
    """
    return points * TICKS_PER_POINT


def ticks_to_bps(ticks: float) -> float:
    """
    Convert ticks to basis points.
    
    Example: 4 ticks -> 1 bp
    """
    return ticks / TICKS_PER_BP


def bps_to_ticks(bps: float) -> float:
    """
    Convert basis points to ticks.
    
    Example: 1 bp -> 4 ticks
    """
    return bps * TICKS_PER_BP


def points_to_bps(points: float) -> float:
    """
    Convert a price move in points to basis points.
    
    Example: 1 point -> 8 bps
    """
    return points * BPS_PER_POINT


def bps_to_points(bps: float) -> float:
    """
    Convert basis points to points.
    
    Example: 8 bps -> 1 point
    """
    return bps / BPS_PER_POINT


# Quick reference table
CONVERSION_TABLE = """
+----------------+--------+--------+----------+
| Decimal Move   | Ticks  | BPS    | Notation |
+----------------+--------+--------+----------+
| 0.03125        | 1      | 0.25   | 0'01     |
| 0.0625         | 2      | 0.5    | 0'02     |
| 0.125          | 4      | 1.0    | 0'04     |
| 0.25           | 8      | 2.0    | 0'08     |
| 0.5            | 16     | 4.0    | 0'16     |
| 1.0            | 32     | 8.0    | 1'00     |
+----------------+--------+--------+----------+
"""


if __name__ == "__main__":
    print("Treasury Futures Price Conversion Reference")
    print("=" * 50)
    print(CONVERSION_TABLE)
    
    # Examples
    print("\nExamples:")
    print(f"  112.25 decimal = {decimal_to_bond_notation(112.25)}")
    print(f"  112'08 = {bond_notation_to_decimal('112 08')} decimal")
    print(f"  0.25 points = {points_to_ticks(0.25)} ticks = {points_to_bps(0.25)} bps")
    print(f"  8 ticks = {ticks_to_bps(8)} bps")

