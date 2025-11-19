
import sys
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def show_run_logs():
    """Display all run logs."""
    workspace = PROJECT_ROOT / "workspace" / "agents"
    
    print("="*80)
    print("RUN LOGS SUMMARY")
    print("="*80)
    
    agents = ["market-data-agent", "consumer-agent"]
    
    for agent_name in agents:
        agent_logs = workspace / agent_name / "logs"
        
        if not agent_logs.exists():
            continue
        
        log_files = sorted(agent_logs.glob("*.json"))
        
        if not log_files:
            continue
        
        print(f"\n{agent_name.upper()}")
        print("-"*80)
        print(f"Total runs: {len(log_files)}\n")
        
        for log_file in log_files:
            with open(log_file) as f:
                log_data = json.load(f)
            
            print(f"Run ID: {log_data['run_id']}")
            print(f"  Status: {log_data['status']}")
            print(f"  Duration: {log_data['duration_ms']:.2f}ms")
            
            if 'sql' in log_data and log_data['sql']:
                sql_short = log_data['sql'][:80] + "..." if len(log_data['sql']) > 80 else log_data['sql']
                print(f"  SQL: {sql_short}")
            
            if 'params' in log_data and log_data['params']:
                print(f"  Params: {log_data['params']}")
            
            if 'row_count' in log_data:
                print(f"  Rows: {log_data['row_count']}")
            
            if log_data.get('output_path'):
                output_name = Path(log_data['output_path']).name
                print(f"  Output: {output_name}")
            
            if 'error' in log_data:
                print(f"  ERROR: {log_data['error']}")
            
            print()


def show_manifest():
    """Display manifest information."""
    workspace = PROJECT_ROOT / "workspace" / "agents"
    
    print("="*80)
    print("MANIFEST STATUS")
    print("="*80)
    
    agents = ["market-data-agent", "consumer-agent"]
    
    for agent_name in agents:
        manifest_path = workspace / agent_name / "meta.json"
        
        if not manifest_path.exists():
            continue
        
        with open(manifest_path) as f:
            manifest = json.load(f)
        
        print(f"\n{agent_name}")
        print(f"  Next ID: {manifest['next_id']}")
        print(f"  Total runs: {manifest['total_runs']}")
        print(f"  Last updated: {manifest['last_updated']}")


def show_artifacts():
    """Display generated artifacts."""
    workspace = PROJECT_ROOT / "workspace" / "agents"
    
    print("\n" + "="*80)
    print("GENERATED ARTIFACTS")
    print("="*80)
    
    agents = ["market-data-agent", "consumer-agent"]
    
    for agent_name in agents:
        out_dir = workspace / agent_name / "out"
        
        if not out_dir.exists():
            continue
        
        output_files = sorted(out_dir.glob("*.json"))
        
        print(f"\n{agent_name}")
        print(f"  Output count: {len(output_files)}")
        print(f"  Files: {[f.name for f in output_files]}")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Show run logs and artifacts")
    parser.add_argument("--logs", action="store_true", help="Show run logs only")
    parser.add_argument("--manifest", action="store_true", help="Show manifest only")
    parser.add_argument("--artifacts", action="store_true", help="Show artifacts only")
    args = parser.parse_args()
    
    # If no args, show everything
    if not (args.logs or args.manifest or args.artifacts):
        show_run_logs()
        show_manifest()
        show_artifacts()
    else:
        if args.logs:
            show_run_logs()
        if args.manifest:
            show_manifest()
        if args.artifacts:
            show_artifacts()


if __name__ == "__main__":
    main()


