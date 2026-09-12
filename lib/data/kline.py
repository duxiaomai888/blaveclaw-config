"""Crypto kline — Blave /kline (Binance USDT-M) + exchange-native (BingX).

拆分自原 lib/data.py。normalize_symbol 和 drop_unsettled_bar 也在这:
前者是全项目唯一的 symbol 归一化配方,后者是扫描/回测对齐的单一来源。
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from .http import BASE, _default_headers, _retry_get
from ._shared import _is_sub_5min, _sanity_check_ohlc
from .batch import _fetch_batch_cached
from .cache import _extend_cache_monthly

__all__ = [
    '_fetch_kline_raw', 'normalize_symbol', 'drop_unsettled_bar', '_BAR_SECONDS',
    'fetch_kline', 'fetch_kline_batch',
    '_BINGX_BASE', '_BINGX_INTERVALS', '_BINGX_PAGE', '_EPOCH',
    '_fetch_bingx_kline_raw', 'fetch_bingx_kline',
]


def _fetch_kline_raw(symbol, interval, start, end, headers=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sub_5min = _is_sub_5min(interval)
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    chunks, cursor = [], s
    chunk_days = 30 if sub_5min else 365
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        # Sub-5min cold fetches hit Binance fapi server-side and can take minutes;
        # _retry_get also covers transient 429/5xx/timeouts a bare requests.get dropped.
        try:
            r = _retry_get(f'{BASE}/kline', headers=headers, params={
                'symbol': symbol, 'period': interval,
                'start_date': cs, 'end_date': ce,
            }, timeout=300 if sub_5min else 60)
        except requests.HTTPError as exc:
            resp = exc.response
            raise RuntimeError(
                f'/kline {symbol} {interval} HTTP {resp.status_code}: {resp.text[:200]}'
            ) from exc
        return r.json()

    rows = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_one, cs, ce): (cs, ce) for cs, ce in chunks}
        for future in as_completed(futures):
            rows.extend(future.result())

    if not rows:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    df = pd.DataFrame(rows)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df = df.set_index('time').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                            'close': 'Close', 'volume': 'Volume'})
    if 'Volume' not in df.columns:
        df['Volume'] = 0
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)


def normalize_symbol(symbol):
    """Any venue/ccxt symbol form → platform canonical dashless uppercase
    ('BTC/USDT', 'BTC-USDT', 'BTC_USDT', 'btcusdt' → 'BTCUSDT').

    THE single normalization recipe (the get_positions() symbol contract in
    references/lib.md quotes it) — a new venue whose symbol format introduces a
    separator not covered here must extend this function, not a local copy.
    """
    return symbol.replace('/', '').replace('-', '').replace('_', '').upper()


def drop_unsettled_bar(df, interval='1h'):
    """丢弃末根尚未收盘的 bar。用于扫描 / 回测对齐,单一来源放这里。

    kline API 返回正在形成的当前 bar:10:00 的 1h bar 在 10:11 拉到时只走了
    11 分钟,close 是实时价。alpha API 只给已收盘 bar(实测慢 1 根),所以末根
    kline 的 alpha 列全是 NaN,但 ret_1h / new_high_24h 仍会基于未确认的价格
    算出来 —— 纯价格规则(连续 K 线 I01/I02、新高新低 F02/F04/F05、价格横盘
    B05/C04)会在信号未确认时就触发。对提前布局的扫描来说,这类假信号最危险。

    末根已收盘或区间未知时原样返回。
    """
    if df is None or len(df) == 0:
        return df
    idx = df.index[-1]
    last = idx.to_pydatetime() if hasattr(idx, 'to_pydatetime') else idx
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - last).total_seconds()
    if age < _BAR_SECONDS.get(str(interval).lower(), 3600):
        return df.iloc[:-1]
    return df


_BAR_SECONDS = {
    '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '12h': 43200,
    '1d': 86400, '1w': 604800,
}


def fetch_kline(symbol, interval, start, end, headers=None):
    """Fetch OHLCV kline data from Blave API with date chunking and local cache.

    All intervals reach back to the symbol's Binance um-futures listing date
    (sub-5min included — the API backfills old months from Binance's official
    archive). A window before listing returns empty, not an error. Sub-5min
    requests are chunked 30 days each server-side, so deep 1min backtests pull
    history month-by-month on first run. Cache namespace is kline2 — the old
    kline cache has Volume hard-zeroed and must not be mixed with real volume.
    """
    # Venue forms like 'BTC/USDT' → Binance 'BTCUSDT'; the API 400s on
    # separator forms and the separator would leak into the cache dir name.
    symbol = normalize_symbol(symbol)
    if headers is None or 'api-key' not in headers:
        h = _default_headers() or {'api-key': '', 'secret-key': ''}
    else:
        h = headers
    df = _extend_cache_monthly(
        'kline2', {'symbol': symbol, 'period': interval},
        lambda s, e: _fetch_kline_raw(symbol, interval, s, e, h),
        start, end,
    )
    return _sanity_check_ohlc(df, f'{symbol} {interval} kline')


def fetch_kline_batch(symbols, interval, start, end, headers=None):
    """Batch fetch OHLCV kline for many symbols via /kline/batch (chunk_size=20).
    Returns dict {symbol: DataFrame(Open, High, Low, Close, Volume)} — keys are
    the NORMALIZED canonical symbols (see normalize_symbol), not the caller's
    original strings: index the result with 'BTCUSDT' even if you passed 'BTC/USDT'.

    Uses the same monthly cache dir naming as fetch_kline ('kline2_{interval}_{symbol}')
    so single-symbol and batch calls share cache — a symbol already cached via
    fetch_kline is a warm hit here too, and vice versa. Warm ids are extended through
    the batch endpoint too (not one call per symbol) — see _fetch_batch_cached."""
    symbols = [normalize_symbol(s) for s in symbols]
    def _parse(records):
        df = pd.DataFrame(records)
        df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
        df = df.set_index('time').sort_index()
        df = df[~df.index.duplicated(keep='first')]
        df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                                'close': 'Close', 'volume': 'Volume'})
        if 'Volume' not in df.columns:
            df['Volume'] = 0
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)

    results = _fetch_batch_cached(
        f'kline2_{interval}', f'{BASE}/kline/batch?period={interval}', 'symbols',
        lambda sid, s, e, hdrs: _fetch_kline_raw(sid, interval, s, e, hdrs),
        _parse, symbols, start, end, headers,
        chunk_size=20, start_param='start_date', end_param='end_date',
        date_chunk_days=30 if _is_sub_5min(interval) else 365,
    )
    return {sid: _sanity_check_ohlc(df, f'{sid} {interval} kline') for sid, df in results.items()}


# ── Exchange-native kline ─────────────────────────────────────────────────────
# Blave's own /kline serves Binance USDT-M perps only. A contract listed on the
# exchange the user actually trades — BingX's gold perp GOLD(XAU)-USDT, say — is
# simply not in it, and substituting a same-ish Binance symbol silently backtests
# a different instrument than the one the orders go to (that is a real incident,
# not a hypothetical). Fetch those straight from the exchange instead.

_BINGX_BASE = 'https://open-api.bingx.com'

# lib interval string → BingX interval. Blave's /kline periods spell minutes
# 'min'; BingX spells them 'm'.
_BINGX_INTERVALS = {
    '1min': '1m', '3min': '3m', '5min': '5m', '15min': '15m', '30min': '30m',
    '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '8h': '8h', '12h': '12h',
    '1d': '1d', '3d': '3d', '1w': '1w',
}

# Measured against the live endpoint: a response is capped at 1000 bars (not the
# documented limit=1440) and keeps the NEWEST end of the requested window, so
# paging walks backwards from endTime.
_BINGX_PAGE = 1000
_EPOCH = datetime(1970, 1, 1)


def _fetch_bingx_kline_raw(symbol, interval, start, end):
    bx_interval = _BINGX_INTERVALS.get(interval)
    if bx_interval is None:
        raise ValueError(f"fetch_bingx_kline: unsupported interval {interval!r} "
                         f"(supported: {', '.join(_BINGX_INTERVALS)})")

    to_ms = lambda s: int((datetime.strptime(s, '%Y-%m-%d') - _EPOCH).total_seconds() * 1000)
    start_ms = to_ms(start)
    end_ms   = to_ms(end) if end else int((datetime.utcnow() - _EPOCH).total_seconds() * 1000)

    rows, cursor_ms, prev_oldest = [], end_ms, None
    while cursor_ms > start_ms:
        r = _retry_get(f'{_BINGX_BASE}/openApi/swap/v3/quote/klines', params={
            'symbol': symbol, 'interval': bx_interval,
            'startTime': start_ms, 'endTime': cursor_ms, 'limit': _BINGX_PAGE,
        }, timeout=30)
        body = r.json()
        # BingX signals errors in the body with HTTP 200, so raise_for_status
        # inside _retry_get sees nothing. Fail loud rather than return a short
        # series that looks like "the contract just has no history there".
        if body.get('code') != 0:
            raise RuntimeError(f"BingX kline {symbol} {bx_interval}: "
                               f"code={body.get('code')} {body.get('msg')}")
        page = body.get('data') or []
        if not page:
            break
        rows.extend(page)
        oldest = min(int(bar['time']) for bar in page)
        if prev_oldest is not None and oldest >= prev_oldest:
            break            # no progress — stop instead of spinning forever
        prev_oldest = oldest
        cursor_ms = oldest - 1

    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df['time'] = pd.to_datetime(df['time'].astype('int64'), unit='ms', utc=True)
    df = df.set_index('time').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                            'close': 'Close', 'volume': 'Volume'})
    return df[cols].astype(float)


def fetch_bingx_kline(symbol, interval, start, end):
    """OHLCV for a BingX perpetual, straight from BingX's public API — no key needed.

    `symbol` is the BingX API symbol, NOT the display name shown on the chart:
    GOLD(XAU)-USDT is `NCCOGOLD2USD-USDT`. Look it up in
    `GET /openApi/swap/v3/quote/contracts` (the `symbol` / `displayName` pair).
    Whatever you pass here must be the same symbol the orders use.
    """
    df = _extend_cache_monthly(
        'bingx_kline', {'symbol': symbol, 'period': interval},
        lambda s, e: _fetch_bingx_kline_raw(symbol, interval, s, e),
        start, end,
    )
    return _sanity_check_ohlc(df, f'{symbol} {interval} bingx_kline')
