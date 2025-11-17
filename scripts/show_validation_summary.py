#!/usr/bin/env python3
"""
Display a console-friendly summary of validation results
"""

import json
from pathlib import Path

def show_summary():
    """Display validation summary in console."""
    results_file = Path('workspace/validation_results.json')
    
    if not results_file.exists():
        print("ERROR: validation_results.json not found. Run validate_custom_queries.py first.")
        return
    
    with open(results_file) as f:
        data = json.load(f)
    
    summary = data['summary']
    results = data['results']
    
    print("=" * 80)
    print(" CUSTOM QUERIES VALIDATION SUMMARY ".center(80, "="))
    print("=" * 80)
    
    # Overall scores
    print("\n  OVERALL PERFORMANCE")
    print("  " + "-" * 76)
    print(f"    Overall Score:     {summary['avg_overall']:.1f}/100  {'FAIL' if summary['avg_overall'] < 60 else 'PASS'}")
    print(f"    Correctness:       {summary['avg_correctness']:.1f}/100")
    print(f"    Completeness:      {summary['avg_completeness']:.1f}/100")
    
    # Pass rate
    print("\n  TEST RESULTS")
    print("  " + "-" * 76)
    total = summary['total']
    print(f"    Total Queries:     {total}")
    print(f"    PASS:              {summary['pass']} ({summary['pass']/total*100:.1f}%)")
    print(f"    NEEDS REVIEW:      {summary['needs_review']}")
    print(f"    FAIL:              {summary['fail']}")
    print(f"    ERROR:             {summary['error']} ({summary['error']/total*100:.1f}%)")
    
    # Passing queries
    passing = [r for r in results if r['status'] == 'PASS']
    if passing:
        print("\n  PASSING QUERIES (7)")
        print("  " + "-" * 76)
        for r in passing:
            print(f"    {r['query_num']:2d}. {r['name'][:55]:<55} {r['overall_score']:5.1f}/100")
    
    # Error queries
    errors = [r for r in results if r['status'] == 'ERROR']
    if errors:
        print("\n  FAILED QUERIES (16) - Missing Templates/Columns")
        print("  " + "-" * 76)
        for r in errors:
            issue = r['correctness_issues'][0] if r['correctness_issues'] else 'Unknown error'
            if 'Invalid template' in issue:
                template = issue.split(':')[1].split('.')[0].strip()
                print(f"    {r['query_num']:2d}. {r['name'][:45]:<45} [missing: {template}]")
            elif 'Invalid column' in issue:
                col = issue.split(':')[1].split('.')[0].strip()
                print(f"    {r['query_num']:2d}. {r['name'][:45]:<45} [missing col: {col}]")
    
    # Top issues
    print("\n  KEY ISSUES IDENTIFIED")
    print("  " + "-" * 76)
    
    missing_templates = set()
    missing_columns = set()
    
    for r in errors:
        for issue in r['correctness_issues']:
            if 'Invalid template' in issue:
                template = issue.split(':')[1].split('.')[0].strip()
                missing_templates.add(template)
            elif 'Invalid column' in issue:
                col = issue.split(':')[1].split('.')[0].strip()
                missing_columns.add(col)
    
    print(f"\n    Missing Templates ({len(missing_templates)}):")
    for template in sorted(missing_templates):
        print(f"      - {template}")
    
    if missing_columns:
        print(f"\n    Missing Columns ({len(missing_columns)}):")
        for col in sorted(missing_columns):
            print(f"      - {col}")
    
    # Correctness issues in passing queries
    correctness_issues = {}
    for r in passing:
        for issue in r['correctness_issues']:
            correctness_issues[issue] = correctness_issues.get(issue, 0) + 1
    
    if correctness_issues:
        print(f"\n    Correctness Issues (even in passing queries):")
        for issue, count in sorted(correctness_issues.items(), key=lambda x: -x[1]):
            print(f"      - {issue} ({count} queries)")
    
    # Completeness issues
    completeness_issues = {}
    for r in passing:
        for issue in r['completeness_issues']:
            completeness_issues[issue] = completeness_issues.get(issue, 0) + 1
    
    if completeness_issues:
        print(f"\n    Completeness Issues:")
        for issue, count in sorted(completeness_issues.items(), key=lambda x: -x[1]):
            print(f"      - {issue} ({count} queries)")
    
    print("\n  RECOMMENDATIONS")
    print("  " + "-" * 76)
    print("    Priority 1: Add missing database columns (theoretical, order_qty)")
    print("    Priority 2: Investigate 100% null rate for 'price' column")
    print("    Priority 3: Implement missing query templates (16 templates needed)")
    print("    Priority 4: Improve SQL logging (show actual params, not ?)")
    
    print("\n  FILES GENERATED")
    print("  " + "-" * 76)
    print("    workspace/VALIDATION_REPORT.md     - Detailed analysis")
    print("    workspace/validation_results.json  - Machine-readable results")
    print("    workspace/validation_results.log   - Full debug logs")
    
    print("\n" + "=" * 80)
    print()

if __name__ == "__main__":
    show_summary()

