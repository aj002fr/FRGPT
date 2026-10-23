"""
Bloomberg Excel Watcher

Monitors a running Excel workbook for NFP (or other economic event) actual data 
from Bloomberg RTD/BDP formulas. When actual data appears, automatically triggers 
the post-event dashboard generation and sends it via Telegram.

Requirements:
- Excel must be open with the Bloomberg workbook
- Bloomberg Terminal must be connected (for RTD to update)
- Run this script before the data release (e.g., 8:25 AM for 8:30 AM NFP)

Usage:
    python scripts/bbg_excel_watcher.py
    python scripts/bbg_excel_watcher.py --config config/bbg_cells.json
    python scripts/bbg_excel_watcher.py --workbook "NFP_Data.xlsx" --poll 5
    python scripts/bbg_excel_watcher.py --reset  # Clear triggered state
    
Safeguards:
- Duplicate prevention: Only triggers once per calendar day (state persisted to file)
- Value validation: Ignores N/A, #VALUE!, blank cells
- Connection resilience: Retries if Excel disconnects temporarily
- Error isolation: Dashboard/Telegram errors don't crash the watcher
"""

import argparse
import json
import logging
import time
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, Any

try:
    import xlwings as xw
except ImportError:
    print("ERROR: xlwings not installed. Run: pip install xlwings>=0.30.0")
    sys.exit(1)

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.postevent_boxplot_dashboard import build_postevent_dashboard

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / 'workspace' / 'bbg_watcher.log')
    ]
)
logger = logging.getLogger(__name__)


class BBGExcelWatcher:
    """Watches Excel workbook for Bloomberg data updates and triggers dashboard generation."""
    
    def __init__(
        self,
        workbook_name: str = "NFP_Data.xlsx",
        actual_cell: str = "B5",
        consensus_cell: str = "B4",
        cons_std_cell: str = "B6",
        sheet_name: Optional[str] = None,
        poll_interval: int = 5,
        telegram_chat_id: Optional[str] = None,
        state_file: Optional[Path] = None
    ):
        self.workbook_name = workbook_name
        self.actual_cell = actual_cell
        self.consensus_cell = consensus_cell
        self.cons_std_cell = cons_std_cell
        self.sheet_name = sheet_name
        self.poll_interval = poll_interval
        self.telegram_chat_id = telegram_chat_id
        self.state_file = state_file or PROJECT_ROOT / "workspace" / ".bbg_watcher_state.json"
        
        self._last_actual = None
        self._triggered_dates: set = set()
        self._load_state()
    
    def _load_state(self):
        """Load previously triggered dates to avoid duplicate triggers."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self._triggered_dates = set(state.get('triggered_dates', []))
                    
                    # Cleanup: remove dates older than 30 days
                    today = date.today()
                    old_count = len(self._triggered_dates)
                    self._triggered_dates = {
                        d for d in self._triggered_dates
                        if (today - date.fromisoformat(d)).days <= 30
                    }
                    if len(self._triggered_dates) < old_count:
                        logger.info(f"Cleaned up {old_count - len(self._triggered_dates)} old trigger records")
                        self._save_state()
                    
                    logger.info(f"Loaded state: {len(self._triggered_dates)} triggered dates in last 30 days")
            except Exception as e:
                logger.warning(f"Could not load state file: {e}")
    
    def _save_state(self):
        """Save triggered dates to prevent duplicate triggers on restart."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({
                    'triggered_dates': list(self._triggered_dates),
                    'last_updated': datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save state file: {e}")
    
    def _connect_to_excel(self) -> Optional[xw.Sheet]:
        """Connect to the running Excel instance and return the target sheet."""
        try:
            # Try to get the workbook by name
            wb = xw.books[self.workbook_name]
            
            # Get the sheet
            if self.sheet_name:
                sheet = wb.sheets[self.sheet_name]
            else:
                sheet = wb.sheets[0]  # Default to first sheet
            
            return sheet
        except KeyError:
            return None  # Workbook not open
        except Exception as e:
            print(f"Excel connection error: {e}")
            return None
    
    def _read_cells(self, sheet: xw.Sheet) -> Dict[str, Any]:
        """Read the actual, consensus, and cons_std values from Excel."""
        logger.info(f"Actual: {sheet.range(self.actual_cell).value}, Consensus: {sheet.range(self.consensus_cell).value}, Cons Std: {sheet.range(self.cons_std_cell).value}")
        return {
            'actual': sheet.range(self.actual_cell).value,
            'consensus': sheet.range(self.consensus_cell).value,
            'cons_std': sheet.range(self.cons_std_cell).value
        }

        
    
    def _is_valid_actual(self, value: Any) -> bool:
        """Check if the actual value is a valid number (not None, not N/A, not error)."""
        if value is None:
            return False
        if isinstance(value, str):
            # Check for Bloomberg error values
            if value.upper() in ['N/A', '#N/A', '#VALUE!', '#REF!', '#NAME?', '']:
                return False
            # Try to parse as number
            try:
                float(value)
                return True
            except ValueError:
                return False
        return isinstance(value, (int, float))
    
    def _trigger_dashboard(self, actual: float, consensus: float, cons_std: float) -> Optional[Path]:
        """Generate the post-event dashboard and return the file path."""
        print(f"\n{'='*60}")
        print(f"TRIGGERED: NFP Actual = {actual}")
        print(f"  Consensus: {consensus}, Cons Std Dev: {cons_std}")
        print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")
        
        try:
            # Generate dashboard - returns a dict with 'html_path' key
            result = build_postevent_dashboard(
                actual=actual,
                consensus=consensus,
                cons_std=cons_std
            )
            
            # Extract path from result (can be dict or string)
            if isinstance(result, dict):
                html_path = result.get('html_path')
                if result.get('success') and html_path:
                    print(f"Dashboard generated: {html_path}")
                    return Path(html_path)
                else:
                    print(f"Dashboard generation failed: {result}")
                    return None
            elif isinstance(result, str):
                print(f"Dashboard generated: {result}")
                return Path(result)
            else:
                print(f"Unexpected result type: {type(result)}")
                return None
                
        except Exception as e:
            print(f"ERROR generating dashboard: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _send_telegram(self, file_path: Path, actual: float, consensus: float):
        """Send the dashboard file via Telegram."""
        if not self.telegram_chat_id:
            print("Telegram chat ID not configured, skipping send")
            return
        
        try:
            import asyncio
            from scripts.telegram_bot import send_file_to_chat, send_message_to_chat
            
            # Send notification message first
            surprise = actual - consensus
            message = (
                f"🚨 NFP RELEASED!\n\n"
                f"📊 Actual: {actual:,.0f}K\n"
                f"📈 Consensus: {consensus:,.0f}K\n"
                f"⚡ Surprise: {surprise:+,.0f}K\n\n"
                f"Dashboard generated and attached below."
            )
            asyncio.run(send_message_to_chat(self.telegram_chat_id, message))
            
            # Send the dashboard file
            caption = f"🎯 Post-Event Dashboard | Actual: {actual:,.0f}K | Surprise: {surprise:+,.0f}K"
            asyncio.run(send_file_to_chat(self.telegram_chat_id, file_path, caption))
            
            print(f"Dashboard sent to Telegram chat {self.telegram_chat_id}")
        except Exception as e:
            print(f"ERROR sending to Telegram: {e}")
            import traceback
            traceback.print_exc()
    
    def watch(self):
        """Main watch loop - poll Excel for data changes."""
        today = date.today().isoformat()
        
        print(f"\n{'='*60}")
        print(f"Bloomberg Excel Watcher Started")
        print(f"{'='*60}")
        print(f"Workbook: {self.workbook_name}")
        print(f"Cells: Actual={self.actual_cell}, Cons={self.consensus_cell}, ConsStd={self.cons_std_cell}")
        print(f"Poll interval: {self.poll_interval} seconds")
        print(f"Today: {today}")
        print(f"{'='*60}\n")
        
        if today in self._triggered_dates:
            print(f"WARNING: Already triggered today ({today}). Will not re-trigger.")
            print("Delete state file to reset: " + str(self.state_file))
        
        waiting_for_excel = True
        
        while True:
            try:
                # Connect to Excel
                sheet = self._connect_to_excel()
                
                if sheet is None:
                    if not waiting_for_excel:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting for Excel workbook '{self.workbook_name}'...")
                        waiting_for_excel = True
                    time.sleep(self.poll_interval)
                    continue
                
                if waiting_for_excel:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected to Excel workbook")
                    waiting_for_excel = False
                
                # Read current values
                values = self._read_cells(sheet)
                actual = values['actual']
                consensus = values['consensus']
                cons_std = values['cons_std']
                
                # Check if actual has arrived
                if self._is_valid_actual(actual):
                    actual = float(actual) if isinstance(actual, str) else actual
                    
                    # Check if this is a new trigger
                    if today not in self._triggered_dates:
                        # Validate consensus and cons_std
                        if not self._is_valid_actual(consensus):
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] Actual={actual} but consensus invalid, waiting...")
                            time.sleep(self.poll_interval)
                            continue
                        
                        consensus = float(consensus) if isinstance(consensus, str) else consensus
                        cons_std = float(cons_std) if isinstance(cons_std, str) else (cons_std or 27.8)
                        
                        # Trigger dashboard generation
                        output_path = self._trigger_dashboard(actual, consensus, cons_std)
                        
                        if output_path:
                            # Send via Telegram
                            self._send_telegram(output_path, actual, consensus)
                            
                            # Mark as triggered
                            self._triggered_dates.add(today)
                            self._save_state()
                            
                            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Trigger complete. Continuing to monitor...")
                    else:
                        # Already triggered, just show current values periodically
                        if self._last_actual != actual:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] Actual={actual} (already triggered today)")
                            self._last_actual = actual
                else:
                    # Actual not yet available
                    if self._last_actual is not None:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Actual cleared, watching for new data...")
                        self._last_actual = None
                
                time.sleep(self.poll_interval)
                
            except KeyboardInterrupt:
                print("\n\nWatcher stopped by user")
                break
            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Error: {e}")
                time.sleep(self.poll_interval)


def load_config(config_path: Path) -> Dict:
    """Load configuration from JSON file."""
    with open(config_path, 'r') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Watch Bloomberg Excel for NFP actual data and trigger dashboard"
    )
    parser.add_argument(
        '--config', '-c',
        type=Path,
        help='Path to config JSON file (default: config/bbg_cells.json)'
    )
    parser.add_argument(
        '--workbook', '-w',
        type=str,
        default='NFP_Data.xlsx',
        help='Excel workbook name (default: NFP_Data.xlsx)'
    )
    parser.add_argument(
        '--actual-cell',
        type=str,
        default='B5',
        help='Cell containing actual value (default: B5)'
    )
    parser.add_argument(
        '--consensus-cell',
        type=str,
        default='B4',
        help='Cell containing consensus value (default: B4)'
    )
    parser.add_argument(
        '--cons-std-cell',
        type=str,
        default='B6',
        help='Cell containing consensus std dev (default: B6)'
    )
    parser.add_argument(
        '--sheet',
        type=str,
        help='Sheet name (default: first sheet)'
    )
    parser.add_argument(
        '--poll', '-p',
        type=int,
        default=5,
        help='Poll interval in seconds (default: 5)'
    )
    parser.add_argument(
        '--telegram-chat',
        type=str,
        help='Telegram chat ID for auto-send'
    )
    parser.add_argument(
        '--reset',
        action='store_true',
        help='Reset triggered state (allow re-trigger today)'
    )
    
    args = parser.parse_args()
    
    # Load config if provided
    config = {}
    config_path = args.config or PROJECT_ROOT / "config" / "bbg_cells.json"
    if config_path.exists():
        print(f"Loading config from {config_path}")
        config = load_config(config_path)
    
    # Create watcher with CLI args overriding config
    watcher = BBGExcelWatcher(
        workbook_name=args.workbook or config.get('workbook_name', 'NFP_Data.xlsx'),
        actual_cell=args.actual_cell or config.get('actual_cell', 'B5'),
        consensus_cell=args.consensus_cell or config.get('consensus_cell', 'B4'),
        cons_std_cell=args.cons_std_cell or config.get('cons_std_cell', 'B6'),
        sheet_name=args.sheet or config.get('sheet_name'),
        poll_interval=args.poll or config.get('poll_interval', 5),
        telegram_chat_id=args.telegram_chat or config.get('telegram_chat_id')
    )
    
    # Handle reset
    if args.reset:
        print("Resetting triggered state...")
        watcher._triggered_dates.clear()
        watcher._save_state()
    
    # Start watching
    watcher.watch()


if __name__ == '__main__':
    main()

