# Move Exhaustion V2 — Scoreboard

Advisor-maintained, updated after every cycle review and every retrospective.
This is where "how is the research actually going" is answerable at a glance,
without re-reading cycle folders. Numbers here are EXPLORATORY / NOT EVIDENCE
until a row reaches CONFIRMATORY.

## Program north star (fixed; from PROGRAM.md)

Answer the three priority questions with confirmatory-tier evidence, either
direction:

| PQ | Question | Status | Best current lead |
|----|----------|--------|-------------------|
| PQ1 | Does declining price impact per unit flow predict reversal? | PARKED on this prefix | C002 ratio and C003 difference both placebo-failed |
| PQ2 | Does opposing-side replenishment add information beyond aggressive flow? | PARKED on this prefix | C004 +3.2 pp, CI crosses 0, high-AF only |
| PQ3 | Does flow add out-of-sample prediction beyond the price path (per class, per horizon)? | PARKED on this prefix | C006 2-class and C007 3-class B−A both null |

The benchmark every candidate must beat: the **X1 price-only baseline**
(`X1_20260813T143427Z`, primary B=8/x=2/R=4/C=4/H=15). Discovery P(REVERSAL)=**0.3529** (854 resolved / 1244 scored; P(continuation)=0.334; P(unresolved)=0.314). **C015 session caveat:** RTH 41.2% vs non-RTH 30.9% (**+10.4 pp** [+4.9, +16.0]). **C018 vol caveat:** 30m pre-t* range T3−T1 **+18.1 pp** [+11.7, +24.5]. Sample is a 75-date 2022-01-05→2022-04-25 prefix — not the full census; validation unscored. 75-date loop **COMPLETE**. Material improvement = multiclass ΔBrier ≥ 0.005 and per-class ΔAUC ≥ 2pp (confirmatory tier).

C001 (BL-1, PARKED): depth-normalized AF Q5−Q1 continuation **−2.0 pp** (CI −9.7 to +6.8).

C002–C003 (BL-2 / PQ1, PARKED): impact-decay ratio and difference both placebo-failed.

C004 (BL-4 / PQ2, PARKED): NR_opp high−low reversal **+3.2 pp** (CI crosses 0); high-AF only.

C005 (BL-5, PARKED): late-window sign-flip n=3; untestable.

C006–C007 (BL-6 / PQ3, PARKED): nested B−A ΔBrier ≈ 0 (2-class and 3-class).

C008 (BL-15, PARKED): OSCILLATORY = 100% `tight_range` (n=390; max mae/mfe 3.5 ticks vs B=8).

C009 (BL-12, PARKED): AF vs P(osc) T3−T1 −0.6 pp.

C010 (BL-9, PARKED): k=2−k=1 reversal +2.7 pp (CI crosses 0).

C011–C012 (BL-8, PARKED): LIFOScore osc−resolved +0.17 but REV 0.85 > OSC 0.70 > CONT 0.33; AF→LIFOScore T3−T1 −0.05.

C013 (BL-10, PARKED): trade-clock AF T3−T1 reversal −2.2 pp [−8.5, +4.7].

C014 (BL-3, PARKED): IS T3−T1 reversal **−11.3 pp** [−18.5, −4.0]; `corr(IS, Δm_W)=−0.957` (E07 fail).

C015 (BL-16, PARKED): RTH − non-RTH reversal **+10.4 pp** [+4.9, +16.0]. Descriptive X1 caveat, not a signal.

C016 (BL-17, PARKED): event-day +1.9 pp (CI includes 0).

C017 (BL-18, PARKED): up − down +1.8 pp (CI includes 0).

C018 (BL-19, PARKED): 30m range T3−T1 **+18.1 pp** [+11.7, +24.5]. Descriptive X1 caveat. Prefix loop complete.

## Candidate findings ledger (exploratory tier)

One row per finding-thread. `Replicated` = confirmed in a fresh cycle per
AGENT_ROSTER's replication rule.

| Thread | Cycles | Headline effect (with CI) | vs X1 baseline | Replicated | Status |
|--------|--------|---------------------------|----------------|------------|--------|
| BL-1 depth-norm AF | C001 | Q5−Q1 continuation −2.0 pp [−9.7, +6.8] | no lift | n/a | PARKED |
| BL-2 impact decay | C002, C003 | C003 +2.7 pp [−3.5, +8.7]; placebo +2.9 pp | no usable lift | failed twice | PARKED |
| BL-4 opposing NR | C004 | +3.2 pp [−2.7, +9.5]; high-AF only +7.7 pp | no standalone lift | n/a | PARKED |
| BL-5 decel vs flip | C005 | reversed n=3; decel 30.5% < accel 35.6% | untestable | n/a | PARKED |
| BL-6 nested B−A | C006, C007 | 2-class ΔBrier ≈ 0; 3-class ΔBrier −0.001 | no incremental lift | failed twice | PARKED |
| BL-15 oscillatory structure | C008 | 100% tight_range (n=390) | descriptive only | n/a | PARKED |
| BL-12 pressure / osc | C009 | AF T3−T1 P(osc) −0.6 pp | no lift | n/a | PARKED |
| BL-9 repeat episode | C010 | k2−k1 +2.7 pp (CI crosses 0) | no lift | n/a | PARKED |
| BL-8 LIFOScore | C011, C012 | osc−resolved +0.17 [+0.12, +0.21]; AF T3−T1 −0.05 | anti-continuation descriptor | AF iterate null | PARKED |
| BL-10 trade-clock AF | C013 | T3−T1 reversal −2.2 pp [−8.5, +4.7] | no lift | n/a | PARKED |
| BL-3 impact shortfall | C014 | T3−T1 −11.3 pp [−18.5, −4.0]; corr(IS,Δm)=−0.96 | E07 fail (IS ≈ −Δm) | n/a | PARKED |
| BL-16 TOD / session | C015 | RTH − non-RTH +10.4 pp [+4.9, +16.0] | X1 caveat (RTH 41.2% vs 30.9%) | n/a | PARKED (descriptive) |
| BL-17 event day | C016 | +1.9 pp [−3.6, +9.0] | no lift | n/a | PARKED |
| BL-18 direction | C017 | +1.8 pp [−4.0, +8.2] | no lift | n/a | PARKED |
| BL-19 30m range | C018 | T3−T1 +18.1 pp [+11.7, +24.5] | X1 vol caveat | n/a | PARKED (descriptive) |

## Loop health KPIs (updated at each retrospective)

| Retro | Cycles closed | ITERATE | PARKED | PROMOTE_REQ | ESCALATED | Worker failures | Median cycle wall-clock | Threads active | Notes |
|-------|---------------|---------|--------|-------------|-----------|-----------------|-------------------------|----------------|-------|
| R001 | 5 | 1 | 4 | 0 | 0 | 0 | ~3 min | 0 then C006 | PQ1/PQ2 parked; n-floor adopted |
| R002 | 10 | 2 | 8 | 0 | 0 | 0 | ~5 min | 0 | PQ3 parked; stop unless human picks a thread |
| R003 | 15 | 3 | 12 | 0 | 0 | 0 | ~4 min | 0 | TOD +10.4pp caveat; session-before-flow |

Reading guide: a healthy loop shows a real PARKED/killed rate (ideas dying is
the system working), threads converging rather than proliferating, and
candidate-ledger rows gaining replications — not a wall of ITERATE.

## Confirmatory results (the only rows that are evidence)

| Contract | Claim family | Verdict | Key numbers | Validation dates consumed |
|----------|--------------|---------|-------------|---------------------------|
| *(none yet)* | | | | |
