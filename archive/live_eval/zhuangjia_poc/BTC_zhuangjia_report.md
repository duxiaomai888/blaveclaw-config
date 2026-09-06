# BTC Zhuangjia PoC Report

**Generated**: 2026-06-26 07:27:22
**Short period**: 20d (2026-06-06 to 2026-06-25, 475 bars)
**Long period**: 4.3y (2022-01-01 to 2026-05-22, 38346 bars)

**BTC 20d move**: 61238 -> 59693 (-2.5%)
Range 58030 - 67255, amplitude 15.1%

## 1. Stage Detection (20d)

| Stage | Count | Pct |
|---|---|---|
| none | 217 | 45.7% |
| reversal | 155 | 32.6% |
| rally | 23 | 4.8% |
| buildup | 20 | 4.2% |
| wash | 19 | 4.0% |
| distrib | 14 | 2.9% |
| consolid | 10 | 2.1% |
| capitul | 6 | 1.3% |
| bounce | 6 | 1.3% |
| test | 5 | 1.1% |

## 2. 4 Signal Types (20d)

| Signal | Count | Reason |
|---|---|---|
| S1 buildup->liftoff (long) | 0 | Needs >30% horizontal in 7d + volume breakout -> BTC 20d single-direction drop, no accumulation pattern |
| S2a wash (hold) | 29 | Decline + shrinking TR + MA30d still rising |
| S2b distrib (exit) | 51 | Decline + expanding TR OR high-position expansion |
| S3a fib 0.382 (entry) | 0 | Needs 30d drawdown >15% -> 20d only -2.5%, no oversold |
| S3b fib 0.5 (reduce) | 0 | Same as fib_382 |
| S3c fib 0.618 (reduce more) | 0 | Same as fib_382 |
| S4a low critical (bounce) | 2 | Range <1.5% + TR ratio <0.6 + repeated test of low |
| S4b high critical (break) | 0 | Range <1.5% + TR ratio <0.6 + repeated test of high |

> **Key observation**: 20d BTC was small-range oscillation + late drop, no complete build->wash->distrib cycle, so S1/S3 = 0 is expected.

## 3. Signal 24h Forward Performance

| Signal | 24h Win Rate | 24h Avg Return |
|---|---|---|
| S1 buildup->liftoff long | N/A | N/A |
| S2a wash hold | 37.9% | -1.29% |
| S2b distrib exit | 52.9% | +0.15% |
| S3a fib 0.382 entry | N/A | N/A |
| S3b fib 0.5 reduce | N/A | N/A |
| S3c fib 0.618 reduce | N/A | N/A |
| S4a low critical bounce | 0.0% | -3.09% |
| S4b high critical break | N/A | N/A |

> **Honest disclaimer**: 20d sample with at most 51 triggers; 24h forward performance is essentially random. Need at least 90d Blave data + 100+ triggers for significance.

## 4. Long-period (4.3y) Stage Distribution

| Stage | Pct | Meaning |
|---|---|---|
| buildup | 15.3% | horizontal + shrinking TR (zhuanjia accumulating) |
| test | 1.9% | abnormal volume probe of supply |
| consolid | 4.1% | handover / rebalancing |
| liftoff | 0.5% | volume breakout starts rally |
| wash | 13.4% | pullback on shrinking TR |
| rally | 5.3% | consecutive green candles main uptrend |
| distrib | 3.2% | high + expansion + flat (zhuanjia exiting) |
| bounce | 6.2% | rebound after drawdown |
| capitul | 1.7% | accelerating drop |
| reversal | 19.0% | contraction at extreme |
| none | 29.5% | no clear feature |

**Reading the long-period distribution**:
- **buildup 15.3% + wash 13.4%** = 28.7% in zhuangjia-activity states (consistent with crypto high turnover)
- **rally 5.3% + liftoff 0.5%** = 5.8% trending up
- **capitul 1.7% + distrib 3.2%** = 4.9% trending down
- **reversal 19.0%** = nearly 1/5 of bars at contraction extremes
- **none 29.5%** = transition states not explicitly classified in the book

## 5. Threshold Adjustments (A-share daily -> crypto 1h)

| Book Threshold | Crypto Adjustment | Reason |
|---|---|---|
| 3-month horizontal | 168h (7d) | 1h noise much larger |
| 10-20% amplitude | 1-5% | 1h amplitude naturally small |
| 5x volume | 1.5-3x | 1h volume ratio more volatile |
| >30% drawdown | >15% | crypto has larger swings |
| >3% breakout | >2-3% | kept |

## 6. Issues Identified

### Why S1/S3 = 0 (expected but worth noting)
1. **S1 buildup->liftoff**: 20d BTC was monotonically falling (-2.5%); no horizontal base, no accumulation pattern. Algorithm is correct, sample just doesn't contain this pattern.
2. **S3 fib rebound**: Needs 30d drawdown >15%; 20d only -2.5%. Long-period version should trigger often - need to validate on 4.3y data.

### Why S2 trigger count is high
- 20d has 29 wash + 51 distrib = 80 signals = 4/day. Too frequent.
- Need stricter filter: e.g., '30d MA 24h turning' or '3 consecutive shrinking TR bars'

## 7. Next Steps (in priority order)

### Required
1. **Extend Blave data to 90d+** -> enables statistical significance for S2/S4
2. **Write strategy to `strategies/zhuangjia_wash_distrib/strategy.py`** -> 4.3y K-line version first
3. **Add Blave-enhanced version** -> overlay short-period indicators when data arrives

### Optional (optimizations)
4. Add wave-ratio detection (wave 2/4 = 0.382/0.5/0.618)
5. Add '5+3=3 weeks' time-window detection (horizontal -> breakout rhythm)
6. Add 'three strikes' rolling count (same resistance tested multiple times)

### Long-period validation (separate run)
On 4.3y data, expect: fib rebound 100+ triggers, rally->distrib 500+, capitul 600+ (2018+2022 bear markets).

## 8. Lessons (for algorithm design)

1. **Crypto 1h vs A-share daily**: completely different noise; ALL thresholds need recalibration.
2. **Volume field often zero in Blave preprocessed files** -> must use `|Close-Open|/Close` or True Range as volume proxy.
3. **pandas `rolling.apply()` errors on string columns** -> use vectorized `(Series == 'x').rolling().sum()`.
4. **Single-direction markets cannot trigger cycle signals** -> buildup/wash/distrib must co-exist; single trend only triggers one side.
5. **Stage detection is soft classification** -> each bar has primary stage, but adjacent stages overlap at boundaries (need main+aux labels).

## 9. Output Files

- **BTC_zhuangjia_dashboard.png** - 7 panels: price+MA / stage band / 4 signals / Blave indicators
- **BTC_zhuangjia_signals.parquet** - raw signals (Stage + 4 sig_* + Blave + delta)
- **zhuangjia_btc_poc.py** - main script (params tunable)
