#!/usr/bin/env python3
"""
Test script for Analytics Agent.

Demonstrates statistical analysis and SVG plot generation capabilities.
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.logging_config import setup_logging
from src.agents.analytics_agent.run import AnalyticsAgent


# Sample queries for testing
SAMPLE_QUERIES = {
    1: {
        "name": "Basic Statistics - Random Data",
        "description": "Compute descriptive statistics on sample data",
        "analysis_type": "descriptive",
        "params": {
            "data": [10.5, 12.3, 11.8, 14.2, 13.5, 12.9, 11.2, 15.1, 13.8, 12.6,
                     11.9, 14.5, 13.2, 12.1, 14.8, 11.5, 13.9, 12.4, 14.1, 13.6],
            "title": "Sample Data Distribution"
        }
    },
    2: {
        "name": "Percentile Rank",
        "description": "Find where a value falls in a distribution",
        "analysis_type": "percentile_rank",
        "params": {
            "value": 85,
            "reference_data": [65, 70, 72, 75, 78, 80, 82, 85, 88, 90, 92, 95, 98, 100,
                              68, 73, 77, 81, 84, 87, 91, 94, 97, 76, 79, 83, 86, 89, 93, 96]
        }
    },
    3: {
        "name": "Distribution Comparison",
        "description": "Compare two datasets statistically",
        "analysis_type": "comparison",
        "params": {
            "data_a": [100, 102, 98, 105, 103, 101, 99, 104, 102, 100],
            "data_b": [95, 97, 93, 98, 96, 94, 92, 97, 95, 93],
            "label_a": "Group A",
            "label_b": "Group B"
        }
    },
    4: {
        "name": "Correlation Analysis",
        "description": "Compute correlation between two variables",
        "analysis_type": "correlation",
        "params": {
            "data_x": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "data_y": [2.1, 4.2, 5.8, 8.1, 10.3, 11.9, 14.2, 16.1, 17.8, 20.2]
        }
    },
    5: {
        "name": "Event Surprise Analysis",
        "description": "Analyze historical surprises for an economic event (requires economic_events.db)",
        "analysis_type": "surprise_analysis",
        "params": {
            "event_name_pattern": "Nonfarm",
            "country": "US",
            "current_surprise": 50.0  # Example current surprise value
        }
    },
    6: {
        "name": "Market on Event Dates",
        "description": "Analyze market prices on economic event dates (requires both DBs)",
        "analysis_type": "event_impact",
        "params": {
            "event_name_pattern": "GDP",
            "symbol_pattern": "XCME%",
            "country": "US",
            "price_column": "price"
        }
    },
    7: {
        "name": "Histogram Generation",
        "description": "Generate a histogram from sample data",
        "analysis_type": "descriptive",
        "params": {
            "data": [23, 25, 27, 22, 24, 26, 28, 21, 29, 30, 24, 25, 26, 27, 23,
                     28, 22, 25, 26, 24, 27, 23, 25, 28, 26, 24, 27, 25, 23, 26,
                     29, 21, 24, 27, 25, 26, 28, 23, 25, 27],
            "title": "Sample Histogram"
        }
    },
    8: {
        "name": "Large Dataset Statistics",
        "description": "Statistics on a larger simulated dataset",
        "analysis_type": "descriptive",
        "params": {
            "data": [100 + (i % 20) - 10 + (i * 7 % 5) for i in range(200)],
            "title": "Large Dataset Analysis"
        }
    },
}


def list_queries():
    """List all available sample queries."""
    print("\n" + "=" * 60)
    print("Available Analytics Test Queries")
    print("=" * 60)
    
    for num, query in SAMPLE_QUERIES.items():
        print(f"\n{num}. {query['name']}")
        print(f"   {query['description']}")
        print(f"   Type: {query['analysis_type']}")
    
    print("\n" + "=" * 60)
    print("\nUsage:") 
    print("  python scripts/test_analytics.py --query N")
    print("  python scripts/test_analytics.py --all")
    print("=" * 60 + "\n")


def run_query(query_num: int, verbose: bool = False):
    """Run a specific query."""
    if query_num not in SAMPLE_QUERIES:
        print(f"Error: Query {query_num} not found. Use --list to see available queries.")
        return
    
    query = SAMPLE_QUERIES[query_num]
    
    print("\n" + "=" * 60)
    print(f"Running Query {query_num}: {query['name']}")
    print("=" * 60)
    print(f"Description: {query['description']}")
    print(f"Analysis Type: {query['analysis_type']}")
    print("-" * 60)
    
    try:
        agent = AnalyticsAgent()
        
        output_path = agent.run(
            analysis_type=query["analysis_type"],
            params=query["params"],
            generate_plot=True
        )
        
        print(f"\n[OK] Success!")
        print(f"Output: {output_path}")
        
        # Read and display results
        import json
        with open(output_path, 'r') as f:
            result = json.load(f)
        
        if verbose:
            print("\nFull Result:")
            print(json.dumps(result, indent=2, default=str))
        else:
            # Display summary
            data = result.get("data", {})
            
            if "statistics" in data:
                stats = data["statistics"]
                print("\nStatistics:")
                for key in ["count", "mean", "median", "std_dev", "min", "max"]:
                    if key in stats:
                        print(f"  {key}: {stats[key]}")
                
                if "percentiles" in stats:
                    print("  Percentiles:", stats["percentiles"])
            
            if "percentile_rank" in data:
                print(f"\nPercentile Rank: {data['percentile_rank']}%")
                print(f"Z-Score: {data.get('z_score', 'N/A')}")
                print(f"Interpretation: {data.get('interpretation', 'N/A')}")
            
            if "comparison" in data:
                comp = data["comparison"]
                print("\nComparison Results:")
                for label in comp:
                    if isinstance(comp[label], dict):
                        print(f"  {label}:")
                        for k, v in comp[label].items():
                            print(f"    {k}: {v}")
            
            if "correlation" in data:
                print(f"\nCorrelation: {data['correlation']}")
                print(f"R-squared: {data.get('r_squared', 'N/A')}")
                print(f"Interpretation: {data.get('interpretation', 'N/A')}")
            
            if "histogram_path" in data:
                print(f"\nHistogram: {data['histogram_path']}")
            
            if "scatter_plot_path" in data:
                print(f"Scatter Plot: {data['scatter_plot_path']}")
            
            if "summary" in data:
                print(f"\nSummary: {data['summary']}")
        
        print("-" * 60)
        
    except FileNotFoundError as e:
        print(f"\n[WARN] Database not found: {e}")
        print("This query requires a database that hasn't been set up yet.")
    except Exception as e:
        print(f"\n[FAIL] Error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()


def run_all_queries(verbose: bool = False):
    """Run all sample queries."""
    print("\n" + "=" * 60)
    print("Running All Analytics Test Queries")
    print("=" * 60)
    
    for query_num in SAMPLE_QUERIES:
        run_query(query_num, verbose)
        print("\n")


def test_tools_directly():
    """Test the analytics tools directly without the agent."""
    print("\n" + "=" * 60)
    print("Testing Analytics Tools Directly")
    print("=" * 60)
    
    # Import tools
    from src.servers.analytics.statistics import (
        compute_statistics,
        compute_percentile_rank,
        compare_distributions,
        compute_correlation
    )
    from src.servers.analytics.plotting import (
        generate_histogram,
        generate_line_chart,
        generate_scatter_plot,
        generate_bar_chart
    )
    
    # Test data
    data = [10, 12, 14, 11, 13, 15, 12, 14, 13, 11, 16, 10, 15, 12, 14]
    
    print("\n1. Testing compute_statistics...")
    result = compute_statistics(data)
    if result["success"]:
        print(f"   Mean: {result['statistics']['mean']}")
        print(f"   Std Dev: {result['statistics']['std_dev']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n2. Testing compute_percentile_rank...")
    result = compute_percentile_rank(14, data)
    if result["success"]:
        print(f"   Value 14 is at percentile: {result['percentile_rank']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n3. Testing compare_distributions...")
    data_a = [10, 12, 14, 11, 13]
    data_b = [15, 17, 19, 16, 18]
    result = compare_distributions(data_a, data_b, "Low", "High")
    if result["success"]:
        print(f"   Effect Size: {result['comparison']['difference']['effect_size']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n4. Testing compute_correlation...")
    x = [1, 2, 3, 4, 5]
    y = [2, 4, 5, 4, 5]
    result = compute_correlation(x, y)
    if result["success"]:
        print(f"   Correlation: {result['correlation']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n5. Testing generate_histogram...")
    result = generate_histogram(data, title="Test Histogram", save_to_file=True)
    if result["success"]:
        print(f"   SVG Path: {result['svg_path']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n6. Testing generate_line_chart...")
    result = generate_line_chart(data, title="Test Line Chart", save_to_file=True)
    if result["success"]:
        print(f"   SVG Path: {result['svg_path']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n7. Testing generate_scatter_plot...")
    result = generate_scatter_plot(x, y, title="Test Scatter", save_to_file=True)
    if result["success"]:
        print(f"   SVG Path: {result['svg_path']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n8. Testing generate_bar_chart...")
    result = generate_bar_chart([10, 25, 15, 30, 20], labels=["A", "B", "C", "D", "E"], title="Test Bar Chart")
    if result["success"]:
        print(f"   SVG Path: {result['svg_path']}")
        print("   [OK] Pass")
    else:
        print(f"   [FAIL] Failed: {result['error']}")
    
    print("\n" + "=" * 60)
    print("Direct Tool Tests Complete")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Test Analytics Agent capabilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_analytics.py --list           # List available queries
  python scripts/test_analytics.py --query 1        # Run query 1
  python scripts/test_analytics.py --all            # Run all queries
  python scripts/test_analytics.py --test-tools     # Test tools directly
  python scripts/test_analytics.py --query 1 -v     # Verbose output
        """
    )
    
    parser.add_argument("--list", action="store_true", help="List available sample queries")
    parser.add_argument("--query", "-q", type=int, help="Run a specific query by number")
    parser.add_argument("--all", action="store_true", help="Run all sample queries")
    parser.add_argument("--test-tools", action="store_true", help="Test tools directly")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    
    if args.list:
        list_queries()
    elif args.query:
        run_query(args.query, args.verbose)
    elif args.all:
        run_all_queries(args.verbose)
    elif args.test_tools:
        test_tools_directly()
    else:
        # Default: show help
        parser.print_help()
        print("\nQuick start: python scripts/test_analytics.py --test-tools")


if __name__ == "__main__":
    main()

