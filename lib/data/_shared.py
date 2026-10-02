"""Shared read-time helpers used by every fetcher module.

放在独立的叶子模块(只依赖 pandas)是为了让 import DAG 保持单向:
cache 需要 _is_sub_5min(monthly 布局的 head-check),kline/alpha/twstock/
twmarket/twfutures 都需要 _sanity_check_ohlc —— 谁放谁那边都会成环。
"""
import os
from datetime import timezone, timedelta

import pandas as pd


# Taipei — every Taiwan series is stamped in this zone; the single definition so
# cache.py (future-window bound) and public_sources (publication times) agree.
_TPE = timezone(timedelta(hours=8))

def _call_through(name, *args, **kwargs):
    """Invoke lib.data.<name> rather than a submodule's own binding of it. The official monolith
    kept every internal name in one namespace, so a check that patches lib.data.<name> (a fake
    transport, a spy) was seen by every fetcher; the package re-binds those names per submodule,
    so this hop restores that patch surface. Runs at call time, never at import."""
    import lib.data as _pkg
    return getattr(_pkg, name)(*args, **kwargs)


# Progress 的唯一定义(原在 kline.py + alpha.py 各抄一份)。lib/progress.py 只引
# stdlib,缺失时兜底成 no-op,取数照常工作、只是没有进度行。
try:
    from lib.progress import Progress
except ImportError:  # half-updated workspace (lib/progress.py not copied yet) — fail open, no progress lines
    class Progress:
        def __init__(self, *a, **k): pass
        def tick(self, n=1): pass


__all__ = ['_sanity_check_ohlc', '_is_sub_5min', '_call_through', 'Progress',
           'DataAccessError', '_NO_ACCESS_MSG', '_check_data_access',
           '_check_desktop_key', '_daemon_on_desktop']


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


# ── Data-access gate ─────────────────────────────────────────────────────────
# 拆自官方 lib/data.py(原檔的 62-108 行)。官方 monolith 的 _retry_get 對 {BASE}
# 開頭的 URL 會先 _check_data_access,所以每個取數函式不用自己判斷有沒有權限;
# BBAC-D 的 http._retry_get 是金鑰輪詢加強版、不做這個 gate,所以需要它的取數
# 函式(snapshots / public_sources)自己呼叫 _check_data_access。型別(DataAccessError)
# 與訊息必須與官方逐字一致:lib/report_bricks.py / lib/report_templates.py 只認
# DataAccessError,用它分辨「沒權限」與「端點壞了」。

class DataAccessError(RuntimeError):
    """No Blave data access this turn (BLAVE_DATA_ACCESS=0, or a scheduled desktop run whose `.env`
    holds no working key). Its own type so a caller with a
    public fallback (the report templates) can tell it from a fetch that failed."""


def _check_data_access(headers=None):
    """Desktop shell sets BLAVE_DATA_ACCESS=0 when it withheld the Blave key this turn
    (no balance for the hourly fee / not signed in). Failing here, before any request,
    is what stops the agent from hunting for credentials after a low-level error —
    a KeyError or 403 reads as a bug to fix, this reads as a fact. Unset or 1: no-op, except
    that a scheduled desktop run with no key in `headers` fails the same way (_check_desktop_key)."""
    if os.environ.get('BLAVE_DATA_ACCESS') == '0':
        raise DataAccessError(_NO_ACCESS_MSG)
    if headers is not None:
        _check_desktop_key(headers)


_NO_ACCESS_MSG = ('Blave data is not reachable on this desktop this turn (no balance for the '
                  'hourly fee / not signed in); stop here, do not look for credentials in .env, '
                  'the environment or elsewhere, and answer the user with what public klines allow.')


def _daemon_on_desktop():
    """A scheduled report job on the desktop: report_runner marks it BLAVE_SCHEDULED_RUN=1 next
    to BLAVE_AGENT_LOCAL=1. Not inferred from BLAVE_DATA_ACCESS being absent — the shell leaves
    that unset on a chat turn too when the user put their own key in `.env`."""
    return os.environ.get('BLAVE_AGENT_LOCAL') == '1' and os.environ.get('BLAVE_SCHEDULED_RUN') == '1'


def _check_desktop_key(headers):
    """On the desktop the shell keeps workspace `.env` in step with the account — the Blave
    key is there only while the account has data access — so an empty key is that state
    file saying "no access", not a bug to chase. Scheduled runs have no per-turn flag and
    read it here; nothing is sent."""
    if _daemon_on_desktop() and not (headers or {}).get('api-key'):
        raise DataAccessError(_NO_ACCESS_MSG)
