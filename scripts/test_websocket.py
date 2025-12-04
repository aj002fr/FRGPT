"""
Test script for Trading Economics WebSocket streaming.

This script verifies that the EventDataPullerAgent can:
1. Start the WebSocket stream
2. Monitor its status
3. Stop the stream
"""

import sys
import time
import json
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from src.agents.eventdata_puller_agent.run import EventDataPullerAgent
from src.bus.file_bus import read_json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_websocket_flow():
    """Run the WebSocket test flow."""
    
    print("\n" + "="*60)
    print("Testing WebSocket Integration")
    print("="*60)
    
    # Check dependencies
    try:
        import websockets
        print("[OK] websockets library installed")
    except ImportError:
        print("[ERROR] websockets library not found!")
        print("Please run: pip install websockets")
        return

    agent = EventDataPullerAgent()
    
    # 1. Start Stream
    print("\n[1] Starting WebSocket Stream...")
    try:
        start_path = agent.run(
            action="stream_start",
            country="united states",
            importance="high"
        )
        result = read_json(start_path)
        print(f"Result: {json.dumps(result['data'][0], indent=2)}")
        
        if result['data'][0].get('status') == 'error':
            print("Failed to start stream (check API key or connection)")
            return
            
    except Exception as e:
        print(f"Error starting stream: {e}")
        return

    # 2. Monitor for a few seconds
    print("\n[2] Monitoring stream for 10 seconds...")
    for i in range(5):
        time.sleep(2)
        status_path = agent.run(action="stream_status")
        status = read_json(status_path)
        data = status['data'][0]
        
        # Check connection status
        is_running = data.get('is_running', False)
        is_connected = data.get('is_connected', False)
        stats = data.get('statistics', {})
        msgs = stats.get('messages_received', 0)
        
        print(f"   T+{i*2}s: Running={is_running}, Connected={is_connected}, Msgs={msgs}")
        
        if not is_running:
            print("   Stream stopped unexpectedly!")
            break

    # 3. Stop Stream
    print("\n[3] Stopping WebSocket Stream...")
    stop_path = agent.run(action="stream_stop")
    stop_result = read_json(stop_path)
    print(f"Result: {json.dumps(stop_result['data'][0], indent=2)}")
    
    print("\n" + "="*60)
    print("Test Complete")
    print("="*60)

if __name__ == "__main__":
    test_websocket_flow()

