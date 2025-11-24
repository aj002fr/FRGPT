"""CLI for testing Orchestrator Agent."""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.agents.orchestrator_agent import OrchestratorAgent
from src.core.logging_config import setup_logging


# Default query and parameters for IDE run/debug without CLI
DEFAULT_QUERY = "when was the last time zn was below 112 and what was the chance of nuclear war in 2025 at that point"
DEFAULT_SKIP_VALIDATION = False
DEFAULT_NUM_SUBTASKS = None  # Uses orchestrator's default (typically 5)


# Sample queries for testing
SAMPLE_QUERIES = [
    # {
    #     "id": 8,
    #     "query": "Show me all US bond treasuries market data from 2025-07-21",
    #     "description": "Date-filtered SQL query"
    # }
]


def list_queries():
    print("\n" + "="*70)
    print("SAMPLE ORCHESTRATOR QUERIES")
    print("="*70)
    
    for query in SAMPLE_QUERIES:
        print(f"\n{query['id']}. {query['query']}")
        print(f"   Description: {query['description']}")
    
    print("\n" + "="*70)
    print(f"Total: {len(SAMPLE_QUERIES)} sample queries")
    print("="*70 + "\n")


def run_query(query_id: int, skip_validation: bool = False, num_subtasks: int = None):
    """
    Run a specific sample query.
    
    Args:
        query_id: Query ID (1-based)
        skip_validation: Skip validation step
        num_subtasks: Number of subtasks
    """
    # Find query
    query_data = next((q for q in SAMPLE_QUERIES if q['id'] == query_id), None)
    
    if not query_data:
        print(f"Error: Query {query_id} not found")
        sys.exit(1)
    
    query = query_data['query']
    
    print("\n" + "="*70)
    print("ORCHESTRATOR AGENT TEST")
    print("="*70)
    print(f"Query ID: {query_id}")
    print(f"Query: {query}")
    print(f"Description: {query_data['description']}")
    print("="*70 + "\n")
    
    # Initialize agent
    agent = OrchestratorAgent()
    
    # Run orchestration
    try:
        print("Starting orchestration...")
        print("-" * 70)
        
        result = agent.run(
            query=query,
            num_subtasks=num_subtasks,
            skip_validation=skip_validation
        )
        
        print("\n" + "="*70)
        print("ORCHESTRATION RESULTS")
        print("="*70)
        
        # Display answer
        print("\nAnswer:")
        print("-" * 70)
        print(result['answer'])
        print()
        
        # Display metadata
        metadata = result['metadata']
        print("Metadata:")
        print("-" * 70)
        print(f"Run ID: {metadata.get('run_id')}")
        print(f"Duration: {metadata.get('duration_ms', 0):.2f} ms")
        print(f"Total Tasks: {metadata.get('total_tasks', 0)}")
        print(f"Successful: {metadata.get('successful_tasks', 0)}")
        print(f"Failed: {metadata.get('failed_tasks', 0)}")
        print(f"Agents Used: {', '.join(metadata.get('agents_used', []))}")
        print(f"Unmappable Tasks: {metadata.get('unmappable_tasks', 0)}")
        print()
        
        # Display validation
        if result.get('validation'):
            validation = result['validation']
            print("Validation:")
            print("-" * 70)
            print(f"Valid: {validation.get('valid')}")
            print(f"Completeness Score: {validation.get('completeness_score', 0)*100:.1f}%")
            
            issues = validation.get('issues', [])
            if issues:
                print(f"Issues ({len(issues)}):")
                for i, issue in enumerate(issues, 1):
                    print(f"  {i}. {issue}")
            else:
                print("Issues: None")
            print()
        
        # Display output path
        print(f"Output File: {result.get('output_path')}")
        print(f"Script: {metadata.get('script_path')}")
        
        print("\n" + "="*70)
        print("✓ ORCHESTRATION COMPLETED SUCCESSFULLY")
        print("="*70 + "\n")
        
        return result
        
    except Exception as e:
        print("\n" + "="*70)
        print("✗ ORCHESTRATION FAILED")
        print("="*70)
        print(f"Error: {e}")
        print("="*70 + "\n")
        raise


def run_custom_query(query: str, skip_validation: bool = False, num_subtasks: int = None):
    """
    Run a custom query.
    
    Args:
        query: Custom query string
        skip_validation: Skip validation step
        num_subtasks: Number of subtasks
    """
    print("\n" + "="*70)
    print("ORCHESTRATOR AGENT TEST (CUSTOM QUERY)")
    print("="*70)
    print(f"Query: {query}")
    print("="*70 + "\n")
    
    # Initialize agent
    agent = OrchestratorAgent()
    
    # Run orchestration
    try:
        print("Starting orchestration...")
        print("-" * 70)
        
        result = agent.run(
            query=query,
            num_subtasks=num_subtasks,
            skip_validation=skip_validation
        )
        
        print("\n" + "="*70)
        print("ORCHESTRATION RESULTS")
        print("="*70)
        
        # Display answer
        print("\nAnswer:")
        print("-" * 70)
        print(result['answer'])
        print()
        
        # Display metadata
        metadata = result['metadata']
        print("Metadata:")
        print("-" * 70)
        print(f"Run ID: {metadata.get('run_id')}")
        print(f"Duration: {metadata.get('duration_ms', 0):.2f} ms")
        print(f"Total Tasks: {metadata.get('total_tasks', 0)}")
        print(f"Successful: {metadata.get('successful_tasks', 0)}")
        print(f"Failed: {metadata.get('failed_tasks', 0)}")
        print(f"Agents Used: {', '.join(metadata.get('agents_used', []))}")
        print()
        
        # Display validation
        if result.get('validation'):
            validation = result['validation']
            print("Validation:")
            print("-" * 70)
            print(f"Valid: {validation.get('valid')}")
            print(f"Completeness Score: {validation.get('completeness_score', 0)*100:.1f}%")
            
            issues = validation.get('issues', [])
            if issues:
                print(f"Issues ({len(issues)}):")
                for i, issue in enumerate(issues, 1):
                    print(f"  {i}. {issue}")
            print()
        
        print(f"Output File: {result.get('output_path')}")
        
        print("\n" + "="*70)
        print("✓ ORCHESTRATION COMPLETED SUCCESSFULLY")
        print("="*70 + "\n")
        
        return result
        
    except Exception as e:
        print("\n" + "="*70)
        print("✗ ORCHESTRATION FAILED")
        print("="*70)
        print(f"Error: {e}")
        print("="*70 + "\n")
        raise


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Test Orchestrator Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--list',
        action='store_true',
        help='List all sample queries'
    )
    
    parser.add_argument(
        '--query',
        type=int,
        metavar='N',
        help='Run sample query N'
    )
    
    parser.add_argument(
        '--custom',
        type=str,
        metavar='QUERY',
        help='Run custom query'
    )
    
    parser.add_argument(
        '--skip-validation',
        action='store_true',
        help='Skip validation step'
    )
    
    parser.add_argument(
        '--num-subtasks',
        type=int,
        metavar='N',
        help='Number of subtasks (default: 5)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=log_level)
    
    # Handle commands
    if args.list:
        list_queries()
    elif args.query:
        run_query(args.query, args.skip_validation, args.num_subtasks)
    elif args.custom:
        run_custom_query(args.custom, args.skip_validation, args.num_subtasks)
    else:
        # No arguments provided - use defaults for IDE run/debug
        print("\n" + "="*70)
        print("RUNNING WITH DEFAULT QUERY (no CLI arguments provided)")
        print("="*70)
        print(f"Query: {DEFAULT_QUERY}")
        print(f"Skip Validation: {DEFAULT_SKIP_VALIDATION}")
        print(f"Num Subtasks: {DEFAULT_NUM_SUBTASKS}")
        print("="*70 + "\n")
        run_custom_query(
            DEFAULT_QUERY,
            skip_validation=DEFAULT_SKIP_VALIDATION,
            num_subtasks=DEFAULT_NUM_SUBTASKS
        )


if __name__ == "__main__":
    main()

