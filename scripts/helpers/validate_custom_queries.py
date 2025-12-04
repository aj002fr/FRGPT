#!/usr/bin/env python3
"""
Validate Custom Queries - Test correctness and completeness metrics

This script runs custom queries (those with nl_query field) and validates:
- Correctness: Does the SQL match the natural language intent?
- Completeness: Are all expected fields present and data filtered correctly?
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.market_data_agent import MarketDataAgent
from src.bus.file_bus import read_json

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('workspace/validation_results.log', mode='w')
    ]
)
logger = logging.getLogger(__name__)


from scripts.test_queries import TEST_QUERIES


def get_custom_queries():
    """Filter to only custom queries (those with nl_query field)."""
    return [q for q in TEST_QUERIES if 'nl_query' in q]


def validate_correctness(query_config, output_data, sql_executed):
    """
    Validate correctness: Does the SQL match the nl_query intent?
    
    Returns: (score, issues)
    - score: 0-100 (100 = perfect)
    - issues: list of problems found
    """
    score = 100
    issues = []
    
    template = query_config.get('template')
    params = query_config.get('params', {})
    nl_query = query_config.get('nl_query', '')
    
    logger.info(f"CORRECTNESS CHECK:")
    logger.info(f"  Template: {template}")
    logger.info(f"  NL Query: {nl_query}")
    logger.info(f"  SQL: {sql_executed}")
    
    # Check 1: Symbol pattern validation
    if 'symbol_pattern' in params:
        pattern = params['symbol_pattern']
        if 'LIKE' not in sql_executed:
            issues.append("Missing LIKE clause for symbol pattern")
            score -= 20
        elif pattern not in sql_executed:
            issues.append(f"Pattern '{pattern}' not found in SQL")
            score -= 15
        else:
            logger.info(f"  ✓ Symbol pattern '{pattern}' correctly applied")
    
    # Check 2: Date filter validation
    if 'file_date' in params:
        date = params['file_date']
        if date not in sql_executed:
            issues.append(f"Date filter '{date}' not found in SQL")
            score -= 20
        else:
            logger.info(f"  ✓ Date filter '{date}' correctly applied")
    
    # Check 3: Time range validation
    if 'start_ts' in params and 'end_ts' in params:
        if 'timestamp >=' not in sql_executed or 'timestamp <=' not in sql_executed:
            issues.append("Time range filter not properly applied")
            score -= 20
        else:
            logger.info(f"  ✓ Time range filter correctly applied")
    
    # Check 4: Price range validation
    if 'min_bid' in params or 'max_ask' in params:
        if 'bid >=' not in sql_executed or 'ask <=' not in sql_executed:
            issues.append("Price range filter not properly applied")
            score -= 20
        else:
            logger.info(f"  ✓ Price range filter correctly applied")
    
    # Check 5: Quantity threshold
    if 'min_qty' in params:
        if 'order_qty >=' not in sql_executed:
            issues.append("Quantity threshold filter not applied")
            score -= 20
        else:
            logger.info(f"  ✓ Quantity threshold correctly applied")
    
    # Check 6: Aggregation queries
    if template in ['aggregate_by_symbol_and_date', 'vwap_by_symbol_and_date', 'daily_ohlc_mid']:
        if 'GROUP BY' not in sql_executed:
            issues.append("Aggregation query missing GROUP BY")
            score -= 25
        else:
            logger.info(f"  ✓ Aggregation query has GROUP BY")
    
    # Check 7: Latest/top-of-book queries
    if template in ['latest_per_symbol', 'latest_for_symbol_pattern']:
        if 'MAX(timestamp)' not in sql_executed and 'ROW_NUMBER()' not in sql_executed:
            issues.append("Latest query missing timestamp ordering mechanism")
            score -= 20
        else:
            logger.info(f"  ✓ Latest query correctly implements timestamp selection")
    
    # Check 8: Spread calculations
    if template in ['top_n_spread_widest', 'top_n_spread_narrowest']:
        if 'ask - bid' not in sql_executed:
            issues.append("Spread calculation missing")
            score -= 25
        else:
            logger.info(f"  ✓ Spread calculation present")
    
    logger.info(f"  CORRECTNESS SCORE: {score}/100")
    if issues:
        logger.warning(f"  Issues found: {len(issues)}")
        for issue in issues:
            logger.warning(f"    - {issue}")
    
    return score, issues


def validate_completeness(query_config, output_data):
    """
    Validate completeness: Are all expected fields present and data complete?
    
    Returns: (score, issues)
    - score: 0-100 (100 = perfect)
    - issues: list of problems found
    """
    score = 100
    issues = []
    
    data = output_data.get('data', [])
    metadata = output_data.get('metadata', {})
    requested_columns = query_config.get('columns')
    limit = query_config.get('limit')
    params = query_config.get('params', {})
    
    logger.info(f"COMPLETENESS CHECK:")
    logger.info(f"  Requested columns: {requested_columns}")
    logger.info(f"  Row count: {len(data)}")
    logger.info(f"  Limit: {limit}")
    
    # Check 1: Data returned
    if not data:
        issues.append("No data returned")
        score = 0
        logger.error("  ✗ No data returned!")
        return score, issues
    
    logger.info(f"  ✓ Data returned: {len(data)} rows")
    
    # Check 2: Requested columns present
    first_row = data[0]
    actual_columns = set(first_row.keys())
    
    if requested_columns:
        requested_set = set(requested_columns)
        missing = requested_set - actual_columns
        extra = actual_columns - requested_set
        
        if missing:
            issues.append(f"Missing requested columns: {missing}")
            score -= 15 * len(missing)
            logger.error(f"  ✗ Missing columns: {missing}")
        else:
            logger.info(f"  ✓ All requested columns present")
        
        if extra and len(requested_columns) < 10:  # Only flag if specific subset requested
            logger.warning(f"  ⚠ Extra columns returned: {extra}")
    
    # Check 3: Symbol pattern filter compliance
    if 'symbol_pattern' in params:
        pattern = params['symbol_pattern']
        # Convert SQL LIKE pattern to Python check
        pattern_check = pattern.replace('%', '').replace('_', '')
        
        violations = []
        for i, row in enumerate(data[:100]):  # Check first 100
            symbol = row.get('symbol', '')
            
            # Check pattern compliance based on pattern type
            if pattern.endswith('.C') and not symbol.endswith('.C'):
                violations.append(f"Row {i}: {symbol} doesn't match call pattern")
            elif pattern.endswith('.P') and not symbol.endswith('.P'):
                violations.append(f"Row {i}: {symbol} doesn't match put pattern")
            elif pattern.startswith('XCME.OZN.') and not symbol.startswith('XCME.OZN.'):
                violations.append(f"Row {i}: {symbol} doesn't match OZN pattern")
            elif pattern.startswith('XCME.VY3.') and not symbol.startswith('XCME.VY3.'):
                violations.append(f"Row {i}: {symbol} doesn't match VY3 pattern")
            elif 'AUG25' in pattern and 'AUG25' not in symbol:
                violations.append(f"Row {i}: {symbol} doesn't contain AUG25")
            elif 'JUL25' in pattern and 'JUL25' not in symbol:
                violations.append(f"Row {i}: {symbol} doesn't contain JUL25")
        
        if violations:
            issues.append(f"Symbol pattern violations: {len(violations)} found")
            score -= min(30, len(violations) * 3)
            logger.error(f"  ✗ Pattern violations found: {len(violations)}")
            for v in violations[:5]:  # Show first 5
                logger.error(f"    {v}")
        else:
            logger.info(f"  ✓ All symbols match pattern '{pattern}'")
    
    # Check 4: Date filter compliance
    if 'file_date' in params:
        date_filter = params['file_date']
        violations = []
        for i, row in enumerate(data[:100]):
            file_date = row.get('file_date', '')
            if file_date != date_filter:
                violations.append(f"Row {i}: file_date={file_date}, expected={date_filter}")
        
        if violations:
            issues.append(f"Date filter violations: {len(violations)} found")
            score -= min(30, len(violations) * 3)
            logger.error(f"  ✗ Date violations: {len(violations)}")
        else:
            logger.info(f"  ✓ All rows match date '{date_filter}'")
    
    # Check 5: Price range compliance
    if 'min_bid' in params:
        min_bid = params['min_bid']
        violations = [i for i, row in enumerate(data) if row.get('bid', 0) < min_bid]
        if violations:
            issues.append(f"min_bid violations: {len(violations)} rows")
            score -= min(20, len(violations) * 2)
            logger.error(f"  ✗ min_bid violations: {len(violations)}")
    
    if 'max_ask' in params:
        max_ask = params['max_ask']
        violations = [i for i, row in enumerate(data) if row.get('ask', 999) > max_ask]
        if violations:
            issues.append(f"max_ask violations: {len(violations)} rows")
            score -= min(20, len(violations) * 2)
            logger.error(f"  ✗ max_ask violations: {len(violations)}")
    
    # Check 6: Quantity threshold compliance
    if 'min_qty' in params:
        min_qty = params['min_qty']
        violations = [i for i, row in enumerate(data) if row.get('order_qty', 0) < min_qty]
        if violations:
            issues.append(f"min_qty violations: {len(violations)} rows")
            score -= min(20, len(violations) * 2)
            logger.error(f"  ✗ min_qty violations: {len(violations)}")
        else:
            logger.info(f"  ✓ All rows meet min_qty >= {min_qty}")
    
    # Check 7: Data quality - null/empty values
    null_counts = {}
    for row in data[:100]:
        for key, value in row.items():
            if value is None or value == '':
                null_counts[key] = null_counts.get(key, 0) + 1
    
    if null_counts:
        logger.warning(f"  ⚠ Null/empty values found:")
        for key, count in null_counts.items():
            logger.warning(f"    {key}: {count} nulls")
            if count > len(data) * 0.5:  # More than 50% nulls
                issues.append(f"High null rate for {key}: {count}/{len(data)}")
                score -= 10
    
    logger.info(f"  COMPLETENESS SCORE: {score}/100")
    if issues:
        logger.warning(f"  Issues found: {len(issues)}")
        for issue in issues:
            logger.warning(f"    - {issue}")
    
    return max(0, score), issues


def run_validation():
    """Run validation on all custom queries."""
    logger.info("="*80)
    logger.info("CUSTOM QUERIES VALIDATION - CORRECTNESS & COMPLETENESS")
    logger.info("="*80)
    
    # Get custom queries
    custom_queries = get_custom_queries()
    logger.info(f"\nFound {len(custom_queries)} custom queries to validate\n")
    
    # Initialize agent
    agent = MarketDataAgent()
    
    # Results tracking
    results = []
    
    for i, query_config in enumerate(custom_queries, 1):
        logger.info("="*80)
        logger.info(f"QUERY {i}/{len(custom_queries)}: {query_config['name']}")
        logger.info("="*80)
        logger.info(f"NL Query: {query_config['nl_query']}")
        logger.info(f"Template: {query_config['template']}")
        logger.info(f"Params: {json.dumps(query_config.get('params', {}), indent=2)}")
        
        try:
            # Run query
            logger.info("\nExecuting query...")
            output_path = agent.run(
                template=query_config['template'],
                params=query_config.get('params'),
                columns=query_config.get('columns'),
                limit=query_config.get('limit')
            )
            
            # Read output
            output_data = read_json(output_path)
            metadata = output_data['metadata']
            sql_executed = metadata['query']
            
            logger.info(f"✓ Query executed successfully")
            logger.info(f"  Output: {output_path.name}")
            logger.info(f"  Rows returned: {metadata['row_count']}")
            logger.info("")
            
            # Validate correctness
            correctness_score, correctness_issues = validate_correctness(
                query_config, output_data, sql_executed
            )
            logger.info("")
            
            # Validate completeness
            completeness_score, completeness_issues = validate_completeness(
                query_config, output_data
            )
            logger.info("")
            
            # Overall assessment
            overall_score = (correctness_score + completeness_score) / 2
            status = "PASS" if overall_score >= 80 else "NEEDS_REVIEW" if overall_score >= 60 else "FAIL"
            
            logger.info(f"OVERALL ASSESSMENT: {status} ({overall_score:.1f}/100)")
            logger.info(f"  Correctness: {correctness_score}/100")
            logger.info(f"  Completeness: {completeness_score}/100")
            
            results.append({
                'query_num': i,
                'name': query_config['name'],
                'status': status,
                'overall_score': overall_score,
                'correctness_score': correctness_score,
                'completeness_score': completeness_score,
                'correctness_issues': correctness_issues,
                'completeness_issues': completeness_issues,
                'row_count': metadata['row_count'],
                'output_file': output_path.name
            })
            
        except Exception as e:
            logger.error(f"✗ Query failed: {e}")
            logger.exception("Full traceback:")
            
            results.append({
                'query_num': i,
                'name': query_config['name'],
                'status': 'ERROR',
                'overall_score': 0,
                'correctness_score': 0,
                'completeness_score': 0,
                'correctness_issues': [str(e)],
                'completeness_issues': [],
                'row_count': 0,
                'output_file': None
            })
        
        logger.info("")
    
    # Summary report
    logger.info("="*80)
    logger.info("VALIDATION SUMMARY")
    logger.info("="*80)
    
    pass_count = sum(1 for r in results if r['status'] == 'PASS')
    review_count = sum(1 for r in results if r['status'] == 'NEEDS_REVIEW')
    fail_count = sum(1 for r in results if r['status'] == 'FAIL')
    error_count = sum(1 for r in results if r['status'] == 'ERROR')
    
    logger.info(f"\nTotal queries: {len(results)}")
    logger.info(f"  PASS: {pass_count}")
    logger.info(f"  NEEDS_REVIEW: {review_count}")
    logger.info(f"  FAIL: {fail_count}")
    logger.info(f"  ERROR: {error_count}")
    
    avg_correctness = sum(r['correctness_score'] for r in results) / len(results)
    avg_completeness = sum(r['completeness_score'] for r in results) / len(results)
    avg_overall = sum(r['overall_score'] for r in results) / len(results)
    
    logger.info(f"\nAverage Scores:")
    logger.info(f"  Correctness: {avg_correctness:.1f}/100")
    logger.info(f"  Completeness: {avg_completeness:.1f}/100")
    logger.info(f"  Overall: {avg_overall:.1f}/100")
    
    # Queries that need attention
    logger.info("\n" + "="*80)
    logger.info("QUERIES NEEDING ATTENTION (Score < 80)")
    logger.info("="*80)
    
    problem_queries = [r for r in results if r['overall_score'] < 80]
    if problem_queries:
        for result in problem_queries:
            logger.warning(f"\n{result['query_num']}. {result['name']} - {result['status']} ({result['overall_score']:.1f}/100)")
            logger.warning(f"   Correctness: {result['correctness_score']}/100")
            logger.warning(f"   Completeness: {result['completeness_score']}/100")
            
            if result['correctness_issues']:
                logger.warning("   Correctness issues:")
                for issue in result['correctness_issues']:
                    logger.warning(f"     - {issue}")
            
            if result['completeness_issues']:
                logger.warning("   Completeness issues:")
                for issue in result['completeness_issues']:
                    logger.warning(f"     - {issue}")
    else:
        logger.info("\n✓ All queries performing well!")
    
    # Save results to JSON
    results_file = Path('workspace/validation_results.json')
    results_file.parent.mkdir(parents=True, exist_ok=True)
    with open(results_file, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total': len(results),
                'pass': pass_count,
                'needs_review': review_count,
                'fail': fail_count,
                'error': error_count,
                'avg_correctness': avg_correctness,
                'avg_completeness': avg_completeness,
                'avg_overall': avg_overall
            },
            'results': results
        }, f, indent=2)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"Detailed results saved to: {results_file}")
    logger.info(f"Full logs saved to: workspace/validation_results.log")
    logger.info(f"{'='*80}")
    
    return 0 if fail_count == 0 and error_count == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(run_validation())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)

