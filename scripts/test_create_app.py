#!/usr/bin/env python3
"""Test create_dashboard_app directly."""
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.live_unified_dashboard import create_dashboard_app

html_path = r'Y:\Aastha\market_data_puller\workspace\scenarios\live_unified_cpi_dashboard_20260120_124318.html'
print(f'HTML path: {html_path}')
print(f'Exists: {Path(html_path).exists()}')
print()
print('Creating dashboard app with enable_socketio=False...')

try:
    app = create_dashboard_app(html_path, 'CPI', enable_socketio=False)
    print('App created successfully!')
    print()
    print('Testing with test_client...')
    with app.test_client() as client:
        resp = client.get('/')
        print(f'Status: {resp.status_code}')
        if resp.status_code == 200:
            print(f'Response length: {len(resp.data)} bytes')
            print('SUCCESS!')
        else:
            print(f'Error response: {resp.data[:500]}')
except Exception as e:
    import traceback
    print(f'Error: {e}')
    print(traceback.format_exc())

