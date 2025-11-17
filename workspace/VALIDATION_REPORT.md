# Custom Queries Validation Report
**Date:** 2025-11-11  
**Test Set:** 23 Custom Queries with Natural Language Descriptions

---

## Executive Summary

The validation revealed **significant gaps** in the system's query capabilities:

| Metric | Score | Status |
|--------|-------|--------|
| **Overall Performance** | **26.7/100** | ❌ **FAIL** |
| **Correctness** | **24.8/100** | ❌ **FAIL** |
| **Completeness** | **28.7/100** | ❌ **FAIL** |
| **Pass Rate** | **30.4%** (7/23) | ❌ **POOR** |

### Test Results Breakdown
- ✅ **PASS:** 7 queries (30.4%)
- ⚠️ **NEEDS_REVIEW:** 0 queries
- ❌ **FAIL:** 0 queries  
- 🚨 **ERROR:** 16 queries (69.6%)

---

## Critical Issues Found

### 1. Missing Query Templates (16 queries failed)

The following templates referenced in test queries **do not exist** in the system:

| Template Name | Queries Affected | Impact |
|---------------|------------------|--------|
| `latest_per_symbol` | 1 | Cannot get top-of-book data |
| `by_symbol_and_timerange` | 1 | Cannot query time ranges |
| `by_price_range` | 1 | Cannot filter by bid/ask prices |
| `by_qty_threshold` | 1 | Cannot filter by order quantity |
| `top_n_spread_widest` | 1 | Cannot analyze wide spreads |
| `top_n_spread_narrowest` | 1 | Cannot analyze tight spreads |
| `missing_theoretical` | 1 | Cannot find missing theoretical prices |
| `theoretical_vs_mid` | 1 | Cannot compare theoretical to mid-price |
| `aggregate_by_symbol_and_date` | 1 | Cannot aggregate daily volumes |
| `vwap_by_symbol_and_date` | 1 | Cannot compute VWAP |
| `latest_for_symbol_pattern` | 1 | Cannot get latest snapshot per product |
| `daily_ohlc_mid` | 1 | Cannot create time series |
| `snapshot_two_symbols_at_ts` | 1 | Cannot compare symbols at a point in time |
| `cross_product_spread_candidates` | 1 | Cannot find spread opportunities |
| `anomalous_timestamp_order` | 1 | Cannot detect data quality issues |

**System Currently Supports Only:**
- `by_symbol`
- `by_date`
- `by_symbol_and_date`
- `all_valid`

### 2. Missing Database Columns (1 query failed)

**Query #1:** "All Call Options (sample)" failed because it requested columns that don't exist:

```
ERROR: Invalid column: theoretical
       Requested: theoretical, order_qty
       Available: id, symbol, bid, ask, price, bid_quantity, offer_quantity, 
                  timestamp, file_date, data_source, is_valid, created_at
```

**Impact:** Cannot query theoretical prices or order quantities - critical for options analysis.

### 3. Correctness Issues (Even in passing queries)

Even the 7 queries that passed had correctness issues:

#### Issue: SQL Pattern Parameters Not Visible (All 6 pattern queries)

**Problem:** The validation expects to see patterns like `%.C` or `XCME.OZN.%` in the SQL, but they're replaced with `?` placeholders.

**Example:**
```
NL Query: "Show call options (symbols ending with .C)"
Expected SQL: WHERE symbol LIKE '%.C'
Actual SQL:   WHERE symbol LIKE ?  -- Pattern not visible!
```

**Affected Queries:**
- Query #2: All Put Options (85/100 correctness)
- Query #3: Product Options (85/100 correctness)
- Query #4: Calls for Product and Date (65/100 correctness)
- Query #5: Puts for Product and Date (65/100 correctness)
- Query #6: Expiry Month Filter (85/100 correctness)
- Query #22: Specific columns only (85/100 correctness)

**Impact:** Makes SQL audit trails harder to debug and validate.

#### Issue: Date Filters Not Visible (2 queries)

**Queries #4 & #5** also lost visibility of date filters (`2025-07-21`) in the SQL due to parameterization.

**Correctness dropped to 65/100** for these queries (lost 20 points for missing pattern, 20 points for missing date).

---

## Completeness Issues

### Issue: High Null Rate for `price` Column (All passing queries)

**All 6 passing queries with data** showed 100% null rate for the `price` column:

| Query | Rows | Null Price Count | Null Rate |
|-------|------|------------------|-----------|
| #2: All Put Options | 20 | 20 | **100%** |
| #3: Product Options | 50 | 50 | **100%** |
| #6: Expiry Month Filter | 50 | 50 | **100%** |
| #21: Quick health check | 5 | 5 | **100%** |

**Completeness score reduced by 10 points** per query due to high null rate (>50%).

**Impact:** 
- The `price` column appears to be unused or improperly populated
- Users cannot rely on price data from the database

---

## Queries That Passed (7 of 23)

| # | Query Name | Score | Correctness | Completeness | Issues |
|---|-----------|-------|-------------|--------------|--------|
| 2 | All Put Options | 87.5 | 85 | 90 | Pattern not visible, high null price |
| 3 | Product Options | 87.5 | 85 | 90 | Pattern not visible, high null price |
| 4 | Calls for Product & Date | 82.5 | 65 | 100 | Pattern + date not visible |
| 5 | Puts for Product & Date | 82.5 | 65 | 100 | Pattern + date not visible |
| 6 | Expiry Month Filter | 87.5 | 85 | 90 | Pattern not visible, high null price |
| 21 | Quick health check | 95.0 | 100 | 90 | High null price |
| 22 | Specific columns only | 92.5 | 85 | 100 | Pattern not visible |

**Best performing:** Query #21 (Quick health check) - 95/100

---

## Queries That Failed (16 of 23)

All failures were due to **missing template implementations**. See section "Missing Query Templates" above.

**Worst category:** Advanced analytics queries (spreads, VWAP, OHLC, aggregations)

---

## Recommendations

### Priority 1: Database Schema Issues
1. **Add missing columns:**
   - `theoretical` (theoretical option price)
   - `order_qty` (order quantity)
   
2. **Fix data quality:**
   - Investigate why `price` column is 100% NULL
   - Either populate it or deprecate it

### Priority 2: Missing Templates (High-Value)
Implement these templates to enable critical use cases:

**Tier 1 - Core Analytics:**
- `latest_per_symbol` - Get top-of-book (most recent tick per symbol)
- `by_symbol_and_timerange` - Query time windows
- `by_price_range` - Filter by bid/ask ranges
- `by_qty_threshold` - Filter by order size

**Tier 2 - Market Analytics:**
- `top_n_spread_widest` / `top_n_spread_narrowest` - Spread analysis
- `theoretical_vs_mid` - Implied volatility candidates
- `aggregate_by_symbol_and_date` - Daily volume aggregates
- `vwap_by_symbol_and_date` - Volume-weighted average price

**Tier 3 - Advanced:**
- `daily_ohlc_mid` - Time series OHLC
- `snapshot_two_symbols_at_ts` - Cross-symbol comparison
- `cross_product_spread_candidates` - Calendar spreads
- `anomalous_timestamp_order` - Data quality checks

### Priority 3: SQL Transparency
- Consider logging the actual SQL with parameters substituted (for debugging/audit)
- Or enhance validation to check parameter binding separately

---

## Detailed Logs

- **Full validation log:** `workspace/validation_results.log` (1.2MB, DEBUG level)
- **JSON results:** `workspace/validation_results.json`
- **Agent outputs:** `workspace/agents/market-data-agent/out/`
- **Run logs:** `workspace/agents/market-data-agent/logs/`

---

## Conclusion

The system performs well for the **4 basic query templates** it implements, but fails on **69.6% of custom queries** due to missing functionality. 

**Key takeaway:** The test suite exposed a large gap between user expectations (23 diverse query types) and current implementation (4 basic templates).

To reach production quality:
- Fix schema issues (Priority 1)
- Implement at minimum the Tier 1 templates (Priority 2)
- Add SQL transparency for debugging (Priority 3)

**Current grade: D (26.7/100)**  
**Target grade: A (>90/100)**

