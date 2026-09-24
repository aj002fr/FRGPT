# JOLTS And US Labour Market Analyst Playbook

Last updated: 2026-05-29

## Purpose

This playbook is a practical replacement for the economist's interpretive layer around JOLTS and the wider US labour market complex. It is written for rates trading and TY analysis, not for academic completeness. The goal is to help you answer four questions quickly:

1. What did the labour print actually say?
2. Is it fresh enough and important enough to matter today?
3. Does it confirm or contradict the rest of the labour data?
4. What is the likely rates/TY interpretation, and what tools should you run?

JOLTS is the center of this note because it is often misunderstood. It can matter a lot when the Fed is focused on labour-market tightness, but it can also be stale and noisy compared with payrolls, claims, ISM employment, and wage data.

## TL;DR Cheat Sheet

JOLTS is a monthly BLS survey of labour demand and turnover. The market watches it mainly through Job Openings, but the economist reads the whole flow table:

- **Job Openings**: labour demand stock on the last business day of the reference month.
- **Hires**: gross additions to payroll during the month.
- **Quits**: voluntary separations, a proxy for worker confidence and wage-bargaining heat.
- **Layoffs and Discharges**: involuntary separations, a direct warning sign for labour demand destruction.
- **Total Separations**: quits + layoffs/discharges + other separations.

The fastest market shortcut:

- Hot JOLTS = openings high, V/U ratio high, quits firm, layoffs low. Bearish duration if the Fed is labour-tightness focused.
- Soft JOLTS = openings down, V/U lower, hires soft, quits lower, layoffs rising. Bullish duration if it confirms claims/payroll cooling.
- Mixed JOLTS = openings move but quits/hires/layoffs do not confirm. Treat as lower conviction.

The biggest "does it matter today?" test:

> JOLTS matters most when it changes the Fed's story about labour-market balance, not merely when openings miss consensus.

If the Fed is talking about excessive labour demand, wage pressure, and the vacancy-to-unemployed ratio, JOLTS can be a real rates event. If the market is focused on CPI, tariffs, supply shocks, fiscal risk, or today's NFP/claims data, JOLTS is usually second tier unless the surprise is large.

## What JOLTS Measures

JOLTS is the Job Openings and Labor Turnover Survey. It measures how many jobs employers say they want to fill and how workers are flowing into and out of jobs. The key distinction is stock vs flow:

- **Job openings are a stock**: positions open on the last business day of the reference month.
- **Hires and separations are flows**: all additions and exits during the full reference month.

That difference matters. A company can stop adding new vacancies late in the month while hires still reflect activity earlier in the month. Or openings can stay high because firms leave postings up even as actual hiring slows.

### Job Openings

Job openings count positions that are open, could start within 30 days, and for which the employer is actively recruiting outside the establishment. The market tends to treat openings as labour demand.

How to read them:

- Higher openings = more unfilled labour demand and tighter labour-market conditions.
- Lower openings = cooling labour demand, especially if the decline is broad by industry.
- Openings are less powerful if hires are not responding. A high openings number with weak hires can signal poor matching, stale postings, or firms that want optionality but are not actually pulling workers in.

For rates, a large upside openings surprise is usually bearish duration when it revives the "too many vacancies, not enough workers" Fed story. A large downside surprise is bullish duration when it validates soft-landing or labour-cooling narratives.

### Hires

Hires measure additions to payroll during the reference month, including new hires, rehires, recalls, seasonal workers, and transfers from other locations.

How to read them:

- Strong hires = firms are converting demand into actual employment.
- Weak hires with high openings = inefficient matching or employer caution.
- Weak hires with falling openings = genuine demand cooling.

Hires are useful because openings can be aspirational, but hires are realized behaviour.

### Quits

Quits are voluntary separations, excluding retirements and transfers. The quits rate is one of the best JOLTS signals for worker confidence.

How to read them:

- High or rising quits = workers believe outside options are good. This can support wage pressure.
- Falling quits = workers are less confident or firms are less willing to poach.
- Low quits with low layoffs can describe a "low hiring, low firing" equilibrium: the labour market is stable, but less dynamic.

For the Fed, quits matter because wage pressure often comes from job switching. A soft openings print with a still-high quits rate is less dovish than a soft openings print with falling quits.

### Layoffs And Discharges

Layoffs and discharges are involuntary separations initiated by employers.

How to read them:

- Low layoffs = firms are not yet cutting labour aggressively.
- Rising layoffs = the labour market is moving from "cooling through vacancies" to "cooling through unemployment."
- Layoffs are more recessionary than falling openings, because the latter can normalize without people losing jobs.

For rates, rising layoffs should usually matter more than falling openings if the market is debating hard landing vs soft landing.

### Total Separations And Other Separations

Total separations sum quits, layoffs/discharges, and other separations. Other separations include retirements, disability, deaths, and transfers. They are usually not the market focus unless there is a data anomaly.

## The Data Mechanics Traders Get Wrong

### JOLTS Is Stale

JOLTS is released roughly five weeks after the reference month. That means it often describes a labour market that the market has already partially inferred from:

- Weekly initial and continuing claims.
- NFP payrolls and unemployment.
- ISM employment subindexes.
- ADP and other private labour data.
- Company layoff announcements and hiring freezes.

This is why a JOLTS miss can have a muted reaction. If claims have already moved, ISM employment has weakened, and payrolls have slowed, JOLTS may only confirm what the market already knows.

### The Release Covers Different Time Windows

Openings are measured on the last business day of the reference month. Hires and separations cover the full month. This can make the table internally messy around turning points.

Example:

- Firms hired early in the month.
- Conditions worsened late in the month.
- Openings dropped by month-end.
- Hires still looked okay for the full month.

That is not necessarily contradictory. It is a timing mismatch.

### Revisions Matter

JOLTS has monthly revisions as late reports and corrected microdata arrive. It also has annual revisions with updated CES employment benchmarks and seasonal factors, generally revising the most recent five years.

Trading implication:

- Do not overfit one print.
- Check whether the prior month's openings, quits, and layoffs were revised meaningfully.
- A headline miss with an upward prior revision may be less dovish than the first headline suggests.

### Survey Error And Response Quality Matter

JOLTS is a survey, so it is subject to sampling and nonsampling error. The BLS monitors response rates, refusal rates, initiation rates, and collection rates, and publishes standard-error information for significant-change analysis.

Trading implication:

- Treat small surprises as low signal.
- Give more weight to trends across three to six months.
- Give more weight to broad confirmation across openings, quits, hires, layoffs, and other labour indicators.

### Job Postings Are Not The Same As Job Openings

Private job posting series can lead or disagree with JOLTS. JOLTS is employer-reported and methodologically official; online postings are faster but can include duplicates, stale ads, evergreen postings, and shifts in posting behaviour.

Use postings as a real-time nowcast, not as a replacement.

## Derived Metrics That Move The Fed

### Vacancy-To-Unemployed Ratio

The vacancy-to-unemployed ratio, often written V/U, is:

```text
job openings / number of unemployed workers
```

This was central to the 2022-2024 soft-landing debate. If vacancies can fall sharply without unemployment rising much, the Fed gets labour-market rebalancing without a hard landing. If falling vacancies start mapping into rising unemployment, the cycle becomes more dangerous.

How to read V/U:

- Very high V/U = firms need more workers than the unemployed pool can supply. Labour market is tight.
- Falling V/U with stable unemployment = benign rebalancing.
- Falling V/U with rising unemployment = labour demand is weakening enough to damage employment.

For rates:

- Falling V/U is dovish only if it does not come with inflation or wage data that keep the Fed worried.
- A sharp move lower in V/U can be bullish duration if it changes the expected Fed path.
- A rebound in V/U can be bearish duration if the Fed is sensitive to labour tightness.

### Beveridge Curve

The Beveridge curve describes the relationship between job openings or vacancy rates and unemployment. Normally, vacancies and unemployment move in opposite directions.

The key policy question:

> Are we moving down the curve, with vacancies falling and unemployment stable, or are we shifting into a regime where unemployment starts rising?

Use JOLTS with the unemployment rate, claims, and payrolls:

- Openings down, unemployment stable, claims stable = soft landing evidence.
- Openings down, unemployment up, continuing claims up = demand deterioration.
- Openings high, unemployment low, quits firm = tight labour market.

### Quits Rate As Wage-Pressure Proxy

The quits rate is not wages, but it helps explain wage pressure. Workers quit when outside offers are available or when they are confident they can find better compensation.

Read-through:

- Quits up + AHE/ECI firm = wage pressure still alive.
- Quits down + AHE cooling = wage pressure easing.
- Quits low + hires low = labour market frozen, not necessarily weak in layoffs yet.

### Hires Minus Separations

Hires minus separations should broadly line up with employment growth over time, though definitions and survey sources differ from NFP.

Use it as a reasonableness check:

- NFP strong but JOLTS hires weak and separations firm = possible payroll strength is lagging or concentrated.
- NFP weak but JOLTS hires strong = payroll weakness may be noisy.
- Hires and separations both low = low churn, not necessarily immediate recession.

## When JOLTS Is Important

JOLTS importance is regime-dependent. It is not always the same tier.

### High-Importance Regimes

JOLTS matters more when:

- The Fed is explicitly focused on labour-market tightness, vacancies, wage pressure, or the Beveridge curve.
- The market is debating whether the labour market can cool without unemployment rising.
- The print is large enough to change the V/U story.
- It confirms a turning point already visible in claims, ISM employment, or payrolls.
- It comes before a major Fed meeting, payrolls report, CPI print, or refunding/auction risk event where rates positioning is already sensitive.

### Low-Importance Regimes

JOLTS matters less when:

- CPI/PCE or fiscal/supply risk is dominating the Fed reaction function.
- Payrolls, claims, and unemployment have already given a fresher signal.
- The surprise is small relative to historical revisions and standard errors.
- The move is isolated to openings while quits, hires, and layoffs do not confirm.
- The release is bundled with a more important same-time event.

### The JOLTS Importance Checklist

Use this before reacting:

1. **What is the regime?** Is the market focused on labour tightness, inflation, growth scare, fiscal/supply, or Fed communication?
2. **How stale is it relative to what we already know?** Did claims/NFP/ISM already say the same thing?
3. **Is the surprise large?** Small deviations in openings are often noise.
4. **Did revisions change the story?** Check both current print and prior month.
5. **Did the internals confirm?** Openings, quits, hires, layoffs should tell a coherent story.
6. **Is it standalone or contaminated?** In this repo, specifically check whether JOLTS followed ISM Manufacturing PMI.
7. **Does it alter the Fed path?** If not, the rates reaction may fade.
8. **What is positioning?** If the market is crowded short/long duration, even second-tier data can trigger a squeeze.

## How TY And Rates Should React

Your repo already has two useful JOLTS empirical tools:

- `scripts/jolts_job_openings_postevent_dashboard.py`: post-event TY distributions by JOLTS surprise bucket.
- `scripts/jolts_event_study_dashboard.py`: JOLTS days after ISM Manufacturing PMI vs standalone JOLTS days.

Do not memorize a single average move. The TY reaction depends on surprise size, whether ISM already framed the week, and the macro regime.

### Basic Directional Map

For Treasury futures, "TY up" means yields lower, duration rally. "TY down" means yields higher, duration selloff.

- Openings upside surprise, quits firm, layoffs low: TY usually biased lower, especially if the Fed is still worried about labour tightness.
- Openings downside surprise, quits lower, hires weaker: TY usually biased higher, especially if claims/payrolls confirm cooling.
- Openings downside but layoffs not rising: bullish duration, but soft-landing flavour rather than recession panic.
- Layoffs rising sharply: stronger bullish duration signal, because the labour market is moving from vacancy normalization to job loss.
- Hot openings but lower quits/hiring: mixed. Market may fade the initial bearish duration move.

### Horizon Discipline

The current JOLTS post-event dashboard uses these horizons:

- `t+10m`
- `t+30m`
- `t+1h`
- `t+2h`

Use them differently:

- **t+10m**: impulse and algos. Good for shock direction, bad for final interpretation.
- **t+30m**: first human digestion. Better for whether the headline survives.
- **t+1h**: cross-market confirmation and Fed-path repricing.
- **t+2h**: whether the move persisted or faded.

If t+10m and t+30m show a move but t+1h/t+2h fade, JOLTS probably did not change the macro story. If the move grows through t+1h/t+2h, the print likely hit the dominant narrative.

### Why ISM Context Matters

`scripts/jolts_event_study_dashboard.py` explicitly classifies JOLTS days as:

- `with_ism`: JOLTS follows an ISM Manufacturing PMI day.
- `standalone`: JOLTS is not directly conditioned by the previous weekday ISM Manufacturing PMI.

This matters because ISM Manufacturing can pre-frame labour demand through the employment subindex and headline growth signal. If ISM already delivered a clear weak/hot signal, JOLTS is often confirmation. If ISM was mixed, JOLTS can settle the debate.

Practical read:

- Weak ISM employment followed by weak JOLTS openings = stronger labour-cooling confirmation.
- Hot ISM employment followed by hot JOLTS openings = stronger labour-tightness confirmation.
- Weak ISM but hot JOLTS = mixed; fade risk is higher.
- Strong ISM but weak JOLTS = the market must decide whether manufacturing was sector-specific or JOLTS is the broader labour-demand signal.

## The Full Labour Complex

### Release Sequence And Freshness Map

Think of the labour complex as a sequence, not a set of isolated reports.

```text
Weekly claims -> ISM/ADP/Challenger -> JOLTS -> NFP/UER/AHE -> ECI
```

This is not a perfect monthly order, but it is the right mental model for freshness and signal type:

- **Claims**: fastest stress signal.
- **ISM/ADP/Challenger**: early monthly context, sector/private/labour-cost warning signals.
- **JOLTS**: deeper labour-demand and turnover internals, but lagged.
- **NFP/UER/AHE**: main market-moving monthly employment/wage report.
- **ECI**: slower but high-quality wage/labour-cost benchmark.

### Non Farm Payrolls

What it measures:

- Net payroll job growth from the establishment survey.
- Usually the biggest labour-market event for rates.

What to watch:

- Headline payrolls.
- Private payrolls.
- Revisions.
- Diffusion/breadth.
- Hours worked.
- Temporary help.
- Birth-death and strike/weather distortions.

Rates interpretation:

- Strong payrolls with upward revisions = bearish duration.
- Weak payrolls with downward revisions = bullish duration.
- But payrolls alone are incomplete; unemployment, participation, hours, and wages can dominate the read.

Relation to JOLTS:

- NFP is net job creation; JOLTS gives gross flows.
- JOLTS can explain whether payroll slowing is from lower hiring, higher separations, or both.

### Unemployment Rate And Household Survey

What it measures:

- The unemployment rate from the household survey.
- Employment, unemployment, labour-force participation, and employment-population ratio.

What to watch:

- Unemployment rate level and change.
- Participation rate.
- Labour-force entry vs job loss.
- Household employment trend.
- Sahm-rule style recession risk, though do not trade it mechanically.

Rates interpretation:

- Rising unemployment from labour-force entry is less recessionary.
- Rising unemployment from job loss is more bullish duration.
- Falling unemployment with weak participation is not as strong as it looks.

Relation to JOLTS:

- JOLTS openings plus unemployment gives V/U.
- If openings fall and unemployment rises, the Beveridge-curve tradeoff is worsening.

### Average Hourly Earnings

What it measures:

- Wage growth in the establishment survey.

What to watch:

- Monthly wage growth.
- Year-over-year wage growth.
- Production/nonsupervisory wage growth.
- Hours and mix effects.

Rates interpretation:

- Wages matter when inflation services and Fed reaction-function concerns dominate.
- A soft JOLTS print can be neutralized by hot wage data.

Relation to JOLTS:

- Quits are the bridge. Falling quits make wage cooling more believable.

### Weekly Initial And Continuing Claims

What they measure:

- Initial claims: new filings for unemployment insurance.
- Continuing claims: people still receiving unemployment benefits.

What to watch:

- Four-week moving average.
- State distortions and holiday effects.
- Continuing claims trend.
- Claims relative to layoff announcements.

Rates interpretation:

- Initial claims rising = layoffs starting.
- Continuing claims rising = re-employment getting harder.
- Claims are fresher than JOLTS, so they can dominate stale JOLTS information.

Relation to JOLTS:

- JOLTS layoffs/discharges should eventually confirm claims.
- If claims rise but JOLTS layoffs are still low, JOLTS may simply be behind.

### ADP Employment

What it measures:

- Private payroll estimate from ADP data.

What to watch:

- Direction and broad industry signal.
- Do not treat it as a precise NFP forecast.

Rates interpretation:

- Usually second tier unless it is very large or the market is starved for labour data.

Relation to JOLTS:

- ADP can provide fresher private-sector hiring colour.
- If ADP is weak and JOLTS hires/openings are soft, the labour-cooling signal is cleaner.

### Employment Cost Index

What it measures:

- High-quality quarterly measure of wages, salaries, and benefits.

What to watch:

- Private wages and salaries.
- Services-sensitive categories.
- Quarter-over-quarter annualized trend.

Rates interpretation:

- Very important when the Fed is focused on wage inflation.
- Slower than AHE but cleaner.

Relation to JOLTS:

- Quits and openings help explain ECI pressure.
- Falling quits reduce the risk that strong wage growth persists.

### ISM Manufacturing And Services Employment

What they measure:

- Employment subindexes inside ISM Manufacturing and Services surveys.

What to watch:

- Employment subindex, new orders, prices paid, supplier deliveries.
- Manufacturing is cyclical but smaller share of employment.
- Services employment is more relevant for broad labour demand.

Rates interpretation:

- Weak employment plus weak new orders = growth scare.
- Hot employment plus hot prices paid = stagflationary/bearish duration risk.

Relation to JOLTS:

- ISM can pre-frame JOLTS. This repo already treats ISM Manufacturing proximity as a JOLTS conditioning variable.

### Challenger Layoffs

What it measures:

- Announced job cuts from Challenger, Gray and Christmas.

What to watch:

- Broadening beyond tech.
- Announcements vs actual layoffs.
- Year-over-year trend.

Rates interpretation:

- Usually context, not a standalone rates event.
- More important when claims and JOLTS layoffs confirm.

Relation to JOLTS:

- Challenger leads the layoff story narratively.
- JOLTS layoffs and claims tell you whether announcements are becoming actual labour-market stress.

## Cross-Checking JOLTS Against The Rest

### Clean Dovish Labour Signal

This is the highest-conviction bullish-duration setup:

- JOLTS openings down meaningfully.
- Quits rate lower.
- Hires weaker.
- Layoffs/discharges rising or at least not falling.
- Claims rising, especially continuing claims.
- NFP slowing with downward revisions.
- Unemployment rate drifting up for job-loss reasons.
- AHE and/or ECI cooling.
- ISM employment weak.

Interpretation:

The labour market is cooling across demand, turnover, employment, and wages. The Fed has less reason to keep policy restrictive.

### Clean Hawkish Labour Signal

This is the highest-conviction bearish-duration setup:

- JOLTS openings high or reaccelerating.
- V/U ratio high.
- Quits firm or rising.
- Hires healthy.
- Layoffs low.
- Claims stable/low.
- NFP strong with upward revisions.
- Unemployment low and participation not distorting the read.
- Wages firm.
- ISM employment stable or strong.

Interpretation:

Labour demand remains too strong for the Fed to relax. Rate cuts get pushed out or hikes/risk premium can reprice higher depending on inflation context.

### Soft-Landing Signal

This is more nuanced:

- Openings down.
- V/U lower.
- Quits lower.
- Layoffs still low.
- Claims not breaking higher.
- NFP slowing but positive.
- Wage growth cooling.

Interpretation:

This is the Fed's preferred cooling channel: fewer vacancies and less churn without mass unemployment. TY can rally, but the market may fade recession pricing if risk assets hold up.

### Low-Hiring, Low-Firing Equilibrium

This is common in late-cycle labour markets:

- Hires low.
- Quits low.
- Layoffs low.
- Openings drifting lower.
- Claims stable but continuing claims may creep up.

Interpretation:

The labour market is not collapsing, but it is less dynamic. It can be benign until a shock arrives. Watch continuing claims and layoffs for the transition from frozen to deteriorating.

### Mixed Or Noisy Signal

Examples:

- Openings up, but hires down and quits down.
- Openings down, but quits up and layoffs low.
- JOLTS soft, but claims low and payrolls strong.
- JOLTS hot, but ISM employment and claims already weakened.

Interpretation:

Do not force a strong rates view. Use the dashboard horizons to see whether the initial move fades.

## Worked Read-Throughs

### Case 1: Soft Openings, Lower Quits, Stable Layoffs

Print:

- Job openings below consensus and prior revised lower.
- Quits rate falls.
- Hires soft.
- Layoffs little changed.
- Claims stable.
- Payrolls recently slowing but still positive.

Economist read:

This is a clean vacancy-normalization print. Labour demand is cooling, worker confidence is easing, but employers are not yet cutting aggressively. That is soft-landing friendly.

Rates read:

Bullish duration, especially at the front/intermediate part of the curve if Fed cuts are in play. TY should rally if inflation data are not fighting the move. Fade risk is moderate because layoffs are not rising.

Dashboard check:

Run the post-event JOLTS dashboard and compare t+30m vs t+2h. Persistence matters more than the first 10 minutes.

### Case 2: Hot Openings, Firm Quits, Low Layoffs

Print:

- Openings above consensus.
- Quits rate firm.
- Hires stable.
- Layoffs low.
- Claims low.
- AHE/ECI not clearly cooling.

Economist read:

Labour demand remains too strong. The quits signal says workers still have outside options. This is not enough labour-market rebalancing for a dovish Fed.

Rates read:

Bearish duration if the market was leaning toward cuts. TY selloff can persist if Fed speakers have recently emphasized labour tightness.

Dashboard check:

Use the dashboard's surprise-percentile bucket. If the current print is in a high-surprise bucket, focus on t+1h and t+2h matched-event distributions.

### Case 3: Openings Down, Layoffs Up, Claims Rising

Print:

- Openings fall.
- Layoffs/discharges rise.
- Quits fall.
- Continuing claims rising.
- NFP revisions negative.

Economist read:

This is no longer only vacancy normalization. It is labour-demand deterioration. The unemployment rate may be next.

Rates read:

Bullish duration with more recession flavour. Watch curve shape: front-end may rally on cuts, but long-end may need risk sentiment/fiscal context.

Dashboard check:

The standard JOLTS post-event dashboard captures TY reaction, but the macro interpretation should lean more heavily on claims and unemployment than on openings alone.

### Case 4: JOLTS After Weak ISM Manufacturing

Print:

- ISM Manufacturing employment was weak on the previous weekday.
- JOLTS openings also miss.
- Quits lower.

Economist read:

JOLTS confirms that the ISM weakness was not only a manufacturing sentiment blip. Labour demand is cooling more broadly.

Rates read:

Higher conviction TY rally than a standalone openings miss. Check whether the event-study dashboard classifies the day as `with_ism` and compare the average profile with standalone days.

## Operating Manual: Repo Tools

### Data Sources

Core data surfaces:

- Event metadata: `workspace/economic_events.db`
- JOLTS DB event name: `JOLTs Job Openings`
- ISM Manufacturing DB event name: `ISM Manufacturing PMI`
- Market data: PostgreSQL `ohlcv_1m`, usually symbol `TY`
- Treasury conversion utilities: `rules/treasury_price_conversion.py`

Important casing note:

```text
JOLTs Job Openings
```

The DB event name uses lowercase `s` in `JOLTs`. Use it exactly in scripts that require exact matching.

### Verify JOLTS Event-Study Data

Run this before regenerating the JOLTS event-study dashboard:

```bash
python scripts/verify_jolts_event_study.py --start-date 2022-01-01
```

What it checks:

- JOLTS and ISM event counts in `workspace/economic_events.db`.
- Whether actual values are present.
- Whether `consensus_std_dev` is present.
- Timestamp ordering.
- Mode distribution: `with_ism` vs `standalone`.
- Similar event names if exact matching fails.

Use it when:

- Dashboard output looks sparse.
- You suspect event-name mismatch.
- You refreshed the Bloomberg workbook or economic events DB.
- You changed start dates.

### Generate The JOLTS Post-Event Dashboard

Command:

```bash
python scripts/jolts_job_openings_postevent_dashboard.py --symbol TY --event-name "JOLTs Job Openings" --start-date 2022-01-01 --bucket-width 20
```

Optional explicit output:

```bash
python scripts/jolts_job_openings_postevent_dashboard.py --symbol TY --event-name "JOLTs Job Openings" --start-date 2022-01-01 --bucket-width 20 --output workspace/scenarios/jolts_job_openings_postevent.html
```

What it does:

- Loads JOLTS actual, consensus, and consensus std dev.
- Computes standardized surprise.
- Converts surprise to historical percentile.
- Assigns percentile buckets.
- Computes TY returns at `t+10m`, `t+30m`, `t+1h`, and `t+2h`.
- Compares all events vs same surprise-percentile bucket.
- Writes a standalone HTML dashboard.
- Writes summary metadata to `workspace/analysis/jolts_job_openings_postevent/jolts_job_openings_postevent_summary.json`.

How to use the output:

- First inspect all-events distributions to know baseline JOLTS sensitivity.
- Then inspect the current surprise bucket for conditional behaviour.
- Compare t+10m/t+30m vs t+1h/t+2h to identify fade vs persistence.
- Check last-two-event markers to avoid anchoring on stale regimes.

### Generate The JOLTS-ISM Event Study Dashboard

Command:

```bash
python scripts/jolts_event_study_dashboard.py --symbol TY --start-date 2022-01-01 --windows 30,60,120,240
```

Optional explicit output:

```bash
python scripts/jolts_event_study_dashboard.py --symbol TY --start-date 2022-01-01 --windows 30,60,120,240 --out workspace/scenarios/jolts_event_study_dashboard.html
```

What it does:

- Loads `JOLTs Job Openings` and `ISM Manufacturing PMI`.
- Classifies JOLTS days as `with_ism` vs `standalone`.
- Computes pre-window moves over 30m, 60m, 120m, and 240m.
- Builds normalized TY price paths.
- Builds average event-time profiles.
- Includes click-to-candlestick modal data.
- Bins by ISM surprise or JOLTS consensus-std buckets.

How to use the output:

- Use `with_ism` when ISM Manufacturing was the major setup into JOLTS.
- Use `standalone` when JOLTS is the main labour-demand event of the day.
- Compare average profiles by mode to see whether ISM-conditioned JOLTS days behave differently.
- Use click-to-candlestick when you need to inspect event-day contamination.

### Analyst Workflow On JOLTS Day

Before release:

1. Read the regime: inflation scare, growth scare, soft landing, Fed labour tightness, or fiscal/supply.
2. Check the recent labour tape: claims, NFP, unemployment, AHE, ISM employment, ADP, Challenger.
3. Decide whether JOLTS can change the story or only confirm it.
4. Run/refresh the verifier if dashboard data quality is uncertain.
5. Have the JOLTS post-event dashboard ready with the likely start date and bucket width.

At release:

1. Check openings vs consensus and revision.
2. Check quits, hires, layoffs/discharges.
3. Estimate whether V/U moves meaningfully.
4. Classify the print: hot, soft, soft-landing, deterioration, or mixed.
5. Watch TY reaction at t+10m and t+30m, but do not over-trust the first tick.

After release:

1. Compare t+1h and t+2h behaviour with the dashboard's conditional history.
2. Check whether the reaction persists through Fed speakers, auctions, or other events.
3. Update the narrative: did JOLTS change the Fed path or simply confirm known cooling?
4. If the move fades, note whether the fade was because the data were stale, mixed, or over-positioned.

## Market Sensitivity Ranking

Use this as a default hierarchy. Regimes can override it.

1. **NFP / unemployment / AHE**: highest monthly labour-market rates event.
2. **Weekly claims**: highest-frequency labour stress signal; more important near turning points.
3. **ECI**: slower but high-quality wage signal; important for Fed wage/inflation narrative.
4. **JOLTS**: important when vacancies, V/U, and labour demand are central to the Fed debate.
5. **ISM Services employment**: useful broad services labour context.
6. **ISM Manufacturing employment**: useful cyclical context and JOLTS pre-frame.
7. **ADP**: context, not a precise NFP forecast.
8. **Challenger layoffs**: early layoff narrative; needs confirmation from claims/JOLTS layoffs.

## Common Mistakes

- Treating openings as the whole report.
- Ignoring quits when making wage-pressure claims.
- Ignoring layoffs when making recession claims.
- Forgetting the five-week lag.
- Trading a small openings surprise without checking revisions.
- Comparing JOLTS directly to NFP without remembering gross flows vs net employment.
- Assuming online job postings and JOLTS are interchangeable.
- Treating a soft openings print as recessionary when layoffs are still low.
- Treating low layoffs as bullish growth when hires and quits are also weak.
- Ignoring same-day or previous-day ISM context.

## Glossary

- **AHE**: Average Hourly Earnings, wage series in the payrolls report.
- **Beveridge curve**: Relationship between vacancies/openings and unemployment.
- **Continuing claims**: People continuing to receive unemployment insurance.
- **ECI**: Employment Cost Index, quarterly labour-cost measure.
- **Hires rate**: Hires as a share of employment.
- **Initial claims**: New unemployment insurance filings.
- **Job openings rate**: Job openings as a share of employment plus openings.
- **Layoffs and discharges**: Involuntary separations initiated by employers.
- **NFP**: Non Farm Payrolls, monthly net payroll employment change.
- **Quits rate**: Quits as a share of employment.
- **Separations**: Worker exits from payroll.
- **V/U ratio**: Job openings divided by unemployed workers.

## Sources To Keep Bookmarked

- BLS JOLTS homepage: https://www.bls.gov/jlt/
- BLS JOLTS latest release: https://www.bls.gov/news.release/jolts.htm
- BLS JOLTS FAQ: https://www.bls.gov/jlt/jltfaq.htm
- BLS JOLTS Handbook of Methods: https://www.bls.gov/opub/hom/jlt/
- FRED JTSJOL series, Job Openings Total Nonfarm: https://fred.stlouisfed.org/series/JTSJOL
- FRED UNEMPLOY series, Unemployed Level: https://fred.stlouisfed.org/series/UNEMPLOY
- FRED JTSQUR series, Quits Total Nonfarm: https://fred.stlouisfed.org/series/JTSQUR

## Final Mental Model

JOLTS is not just "job openings." It is the labour market's gross-flow table. The best JOLTS read combines:

```text
Openings = labour demand
Quits = worker confidence and wage pressure
Hires = demand converted into actual jobs
Layoffs = demand destruction
V/U = Fed-relevant tightness
Claims/NFP/ISM/ECI = freshness and confirmation
```

Trade it hardest when those signals align and the Fed cares about labour tightness. Fade it faster when it is stale, mixed, or contradicted by fresher labour data.
