"""Taiwan market-wide data (大盤) — index, turnover, institutional, margin, dividend points.

拆分自原 lib/data.py。全市場層級,沒有 stock_id 維度;個股層級的同名資料請用
lib/data/twstock.py 的 fetch_twstock_* 系列。
"""
from datetime import datetime

import pandas as pd

import lib.data as _pkg
from .http import BASE
_retry_get = lambda *a, **k: _call_through('_retry_get', *a, **k)
from ._shared import _sanity_check_ohlc, _call_through
from .cache import _extend_cache_monthly, _load_fundamental_cache, _save_fundamental_cache

__all__ = [
    '_fetch_twmarket_index_raw', 'fetch_twmarket_index',
    '_fetch_twmarket_raw', '_TWMARKET_TURNOVER_COLUMNS', '_TWMARKET_INST_COLUMNS',
    '_TWMARKET_MARGIN_COLUMNS',
    'fetch_twmarket_turnover', 'fetch_twmarket_institutional',
    'fetch_twmarket_margin', 'fetch_twmarket_dividend_points',
]


# ── Taiwan market-wide data (大盤) ────────────────────────────────────────────
# 全市場層級,沒有 stock_id 維度。個股層級的同名資料請用上面的 fetch_twstock_* 系列。

def _fetch_twmarket_index_raw(index_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twmarket/index/{index_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close'])
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()[['open', 'high', 'low', 'close']].rename(
        columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}).astype(float)


def fetch_twmarket_index(start, end, headers=None, index_id='TAIEX'):
    """大盤加權指數日K（發行量加權股價指數）. Returns DataFrame with Open/High/Low/Close.
    1999-01-05 起;`TAIEX` 是目前唯一支援的 index_id（其他值 API 回 400）。
    指數本身沒有成交量欄位——大盤成交量/成交金額請用 fetch_twmarket_turnover。"""
    df = _extend_cache_monthly(
        'twmarket_index', {'id': index_id},
        lambda s, e: _fetch_twmarket_index_raw(index_id, s, e, headers),
        start, end,
    )
    return _sanity_check_ohlc(df, f'{index_id} twmarket index')


def _fetch_twmarket_raw(endpoint, columns, start, end, headers=None):
    """Shared raw fetch for the market-wide (no stock_id) twmarket endpoints."""
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twmarket/{endpoint}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    cols = [c for c in columns if c in df.columns]
    return df[cols].astype(float)


_TWMARKET_TURNOVER_COLUMNS = ['volume', 'value', 'trades']
_TWMARKET_INST_COLUMNS = ['foreign', 'investment_trust', 'dealer', 'total']
_TWMARKET_MARGIN_COLUMNS = ['margin_balance', 'margin_balance_prev', 'margin_balance_value',
                            'short_balance', 'short_balance_prev']


def fetch_twmarket_turnover(start, end, headers=None):
    """全市場每日成交量值（TWSE 集中市場）. Returns DataFrame with columns:
    volume（成交股數,股）、value（成交金額,元）、trades（成交筆數）. 1990-01-04 起。"""
    return _extend_cache_monthly(
        'twmarket_turnover', {'id': 'TWSE'},
        lambda s, e: _fetch_twmarket_raw('turnover', _TWMARKET_TURNOVER_COLUMNS, s, e, headers),
        start, end,
    )


def fetch_twmarket_institutional(start, end, headers=None):
    """全市場三大法人每日買賣超. Returns DataFrame with columns:
    foreign / investment_trust / dealer / total,皆為淨買賣超金額（元,買 - 賣）。
    2004-04-07 起。外資自營商計入 dealer,不計入 foreign。
    個股層級請改用 fetch_twstock_institutional。"""
    return _extend_cache_monthly(
        'twmarket_institutional', {'id': 'TWSE'},
        lambda s, e: _fetch_twmarket_raw('institutional', _TWMARKET_INST_COLUMNS, s, e, headers),
        start, end,
    )


def fetch_twmarket_margin(start, end, headers=None):
    """全市場融資融券餘額. Returns DataFrame with columns:
    margin_balance / margin_balance_prev（融資餘額與前日餘額,張）、
    margin_balance_value（融資金額,元）、
    short_balance / short_balance_prev（融券餘額與前日餘額,張）. 2001-01-03 起。"""
    return _extend_cache_monthly(
        'twmarket_margin', {'id': 'TWSE'},
        lambda s, e: _fetch_twmarket_raw('margin', _TWMARKET_MARGIN_COLUMNS, s, e, headers),
        start, end,
    )


def fetch_twmarket_dividend_points(start, end, headers=None):
    """加權指數每日除息點數 (TAIEX daily index dividend points) — the correction
    term for 正逆價差 fair-basis math. DatetimeIndex, columns: points (index
    points), estimated (bool: False = realized, TR-index derived, from 2003;
    True = forecast — announced dividends + last-year template, zero-filled on
    event-less future weekdays, horizon today+120).

    Deliberately NOT month-file cached (unlike the other twmarket series): the
    estimated leg is recomputed server-side every day and the realized/estimated
    boundary sweeps through the current month, so month files would freeze stale
    forecasts as if they were history. Instead the FULL series (one API call)
    sits in a single-file cache with a 1-hour TTL — realized history rides along
    for free, forecasts are never older than an hour, and cost is capped at 24
    calls/day. Slice locally. Realized leg updates ~17:00 Taipei daily.

    The API's `meta` (estimated_coverage / degraded) rides along in the
    returned frame's `df.attrs['meta']` — pandas attrs survive the parquet
    cache round-trip, so cache hits carry the meta of the fetch that filled
    the cache (same ≤1h freshness as the data itself). Callers whose math
    depends on the estimated leg (e.g. mispricing D(t)) MUST check
    `attrs['meta'].get('degraded')` and refuse to compute on a lower-bound
    estimate; the print below is a courtesy for ad-hoc use, not the guard."""

# lib.data._CACHE_DIR is the patch surface a check redirects; this module's own import would not
# see it (a scratch dir for the run), so read it per call.
def _cache_dir():
    return getattr(_pkg, '_CACHE_DIR')


    path = _cache_dir() / 'twmarket_dividend_points.parquet'
    df = _load_fundamental_cache(path, max_age_days=1 / 24)
    if df is None:
        r = _retry_get(f'{BASE}/studio/market/twmarket/dividend_points',
                       headers=headers, timeout=60)
        payload = r.json()
        meta = payload.get('meta') or {}
        if meta.get('degraded'):
            print(f"  [dividend_points] WARNING degraded estimate — synthesis "
                  f"coverage {meta.get('estimated_coverage')}; treat the "
                  f"estimated leg as a lower bound")
        data = payload.get('data', [])
        if not data:
            out = pd.DataFrame(columns=['points', 'estimated'])
            out.attrs['meta'] = meta
            return out
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df.attrs['meta'] = meta
        _save_fundamental_cache(path, df)
    out = df
    if start:
        out = out[out.index >= pd.Timestamp(start)]
    if end:
        out = out[out.index <= pd.Timestamp(end)]
    out.attrs = dict(df.attrs)   # slicing must not drop the meta
    return out
