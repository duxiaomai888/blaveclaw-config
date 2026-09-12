"""Shared read-time helpers used by every fetcher module.

放在独立的叶子模块(只依赖 pandas)是为了让 import DAG 保持单向:
cache 需要 _is_sub_5min(monthly 布局的 head-check),kline/alpha/twstock/
twmarket/twfutures 都需要 _sanity_check_ohlc —— 谁放谁那边都会成环。
"""
import pandas as pd

__all__ = ['_sanity_check_ohlc', '_is_sub_5min']


def _sanity_check_ohlc(df, label):
    """Drop bars with impossible OHLC values (high<low, non-positive or NaN price).

    Corrupt upstream/exchange data would otherwise silently propagate into every
    indicator and signal computed on top of it — not a hypothetical, this is the
    failure mode a strategy author can't see just by eyeballing a chart.

    Called at READ time (on the assembled result, after the cache), never before
    writing the cache: the cache must keep the raw upstream bars, so a transient
    upstream glitch doesn't become a permanent hole in an immutable monthly
    parquet, and bars already cached before this check existed are covered too.

    Dropping leaves a gap in the bar series (shift/pct_change will span it) —
    same as an exchange outage. The dropped timestamps are printed so the gap
    is diagnosable; corrupt bars are strictly worse than a visible gap.
    """
    if df.empty or not all(c in df.columns for c in ('Open', 'High', 'Low', 'Close')):
        return df
    ohlc = df[['Open', 'High', 'Low', 'Close']]
    bad = (df['High'] < df['Low']) | (ohlc <= 0).any(axis=1) | ohlc.isna().any(axis=1)
    if bad.any():
        ts = ', '.join(str(t) for t in df.index[bad][:5])
        more = '' if int(bad.sum()) <= 5 else f' (+{int(bad.sum()) - 5} more)'
        print(f"  ⚠️  {label}: dropped {int(bad.sum())} bar(s) with invalid OHLC "
              f"(high<low, non-positive or NaN price) at: {ts}{more}")
        df = df[~bad]
    return df


def _is_sub_5min(interval):
    return pd.Timedelta(interval) < pd.Timedelta('5min')
