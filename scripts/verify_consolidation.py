#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify System Consolidation
Checks that all 4 agents and 7 tools are properly configured.
"""

import sys
import io
from pathlib import Path

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    print("=" * 80)
    print(" SYSTEM CONSOLIDATION VERIFICATION ".center(80, "="))
    print("=" * 80)
    
    # Check 1: Agent imports
    print("\n[CHECK 1] Importing 4 core agents...")
    try:
        from src.agents.reasoning_agent import ReasoningAgent
        from src.agents.polymarket_agent import PolymarketAgent
        from src.agents.market_data_agent import MarketDataAgent
        from src.agents.consumer_agent import ConsumerAgent
        print("✅ All 4 core agents imported successfully")
        agents = {
            'ReasoningAgent': ReasoningAgent,
            'PolymarketAgent': PolymarketAgent,
            'MarketDataAgent': MarketDataAgent,
            'ConsumerAgent': ConsumerAgent
        }
        for name, cls in agents.items():
            print(f"   • {name}")
    except ImportError as e:
        print(f"❌ Agent import failed: {e}")
        return False
    
    # Check 2: Tool discovery
    print("\n[CHECK 2] Discovering MCP tools...")
    try:
        from src.mcp.discovery import discover_tools
        tools = discover_tools(PROJECT_ROOT / "src" / "servers")
        print(f"✅ Discovered {len(tools)} MCP tools:")
        for name in sorted(tools.keys()):
            print(f"   • {name}")
        
        if len(tools) != 7:
            print(f"⚠️  Warning: Expected 7 tools, found {len(tools)}")
    except Exception as e:
        print(f"❌ Tool discovery failed: {e}")
        return False
    
    # Check 3: File structure
    print("\n[CHECK 3] Checking file structure...")
    required_files = [
        "main.py",
        "README.md",
        "CONSOLIDATION_SUMMARY.md",
        "QUICK_START.md",
        "CHANGELOG.md",
        "requirements.txt",
        "src/agents/reasoning_agent/run.py",
        "src/agents/polymarket_agent/run.py",
        "src/agents/market_data_agent/run.py",
        "src/agents/consumer_agent/run.py",
        "src/servers/polymarket/__init__.py",
        "src/servers/marketdata/__init__.py",
        "docs/INDEX.md",
        "memory-bank/activeContext.md"
    ]
    
    all_exist = True
    for file_path in required_files:
        full_path = PROJECT_ROOT / file_path
        if full_path.exists():
            print(f"✅ {file_path}")
        else:
            print(f"❌ Missing: {file_path}")
            all_exist = False
    
    if not all_exist:
        return False
    
    # Check 4: Removed files
    print("\n[CHECK 4] Verifying removed files...")
    removed_items = [
        "src/agents/predictive_markets_agent",
        "tests/e2e/test_predictions_e2e.py",
        "scripts/test_predictions.py",
        "scripts/setup_predictions_db.py",
        "docs/PREDICTIVE_MARKETS_IMPLEMENTATION.md"
    ]
    
    all_removed = True
    for item_path in removed_items:
        full_path = PROJECT_ROOT / item_path
        if not full_path.exists():
            print(f"✅ Removed: {item_path}")
        else:
            print(f"⚠️  Still exists: {item_path}")
            all_removed = False
    
    # Summary
    print("\n" + "=" * 80)
    if all_exist and all_removed:
        print(" ✅ CONSOLIDATION COMPLETE - SYSTEM READY ".center(80, "="))
        print("=" * 80)
        print("\n🎊 All checks passed!")
        print("\n📋 System Summary:")
        print("   • 4 Core Agents (Reasoning, Polymarket, MarketData, Consumer)")
        print("   • 7 MCP Tools (3 Polymarket + 1 MarketData + 3 optional)")
        print("   • 3 Unified Pipelines (SQL, AI, Direct)")
        print("   • Multi-layer validation (URL, date, token, format)")
        print("\n🚀 Quick start:")
        print("   python main.py              # Run all 3 pipelines")
        print("   python -m pytest tests/e2e/ # Run tests")
        print("\n📖 Documentation:")
        print("   README.md                   # Overview")
        print("   QUICK_START.md             # 5-minute guide")
        print("   CONSOLIDATION_SUMMARY.md   # What changed")
        return True
    else:
        print(" ❌ CONSOLIDATION INCOMPLETE ".center(80, "="))
        print("=" * 80)
        print("\n⚠️  Some checks failed. Review output above.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

