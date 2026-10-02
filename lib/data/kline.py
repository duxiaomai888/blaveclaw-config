"""Crypto kline — Blave /kline (Binance USDT-M) + exchange-native (BingX).

拆分自原 lib/data.py。normalize_symbol 和 drop_unsettled_bar 也在这:
前者是全项目唯一的 symbol 归一化配方,后者是扫描/回测对齐的单一来源。
"""
from datetime import datetime, timedelta, timezone
import os
import threading
import time

import pandas as pd
import requests

from .http import BASE, _default_headers, _RateLimiter
_retry_get = lambda *a, **k: _call_through('_retry_get', *a, **k)
from ._shared import Progress, _is_sub_5min, _sanity_check_ohlc, _call_through

# Patchable names (_fetch_kline_raw / _fetch_binance_kline_raw / _binance_get /
# _extend_cache_monthly) are read off the package per call, not off this module's globals:
# the checks patch lib.data.<name>, and a submodule function resolves a bare name in its
# own globals, so a patch on the package would not reach it. Same bridge as feeds.py.
import lib.data as _pkg
_resolve = lambda name: getattr(_pkg, name)

__all__ = [
    '_fetch_kline_raw', 'normalize_symbol', 'drop_unsettled_bar', '_BAR_SECONDS',
    'closed_bars_only', '_drop_forming_bar',
    'fetch_kline', 'fetch_kline_batch',
    '_BINGX_BASE', '_BINGX_INTERVALS', '_BINGX_PAGE', '_EPOCH',
    '_fetch_bingx_kline_raw', 'fetch_bingx_kline',
    '_kline_source', '_BINANCE_KLINES', '_binance_klines_to_df', '_fetch_binance_kline_raw',
]


def _kline_source():
    """'blave' (default) or 'binance' — where fetch_kline gets its bars.

    Read per call rather than at import so a shell that exports it after this
    module is loaded still takes effect. Opt-in by design: an unset variable is
    the fleet's behaviour, byte for byte.
    """
    return os.environ.get('BLAVE_KLINE_SOURCE', 'blave').strip().lower()


_closed_bars_only = 0   # >0 while a strategy's fetch_data runs under closed_bars_only()


class closed_bars_only:
    """Scope in which the crypto kline fetchers drop the bar that has not closed yet.

    The runner and wait_for_bar wrap a strategy's fetch_data in it: every 24/7 crypto
    source hands the forming bar back — Blave /kline resamples closed 1m/5m base bars into
    the requested period without dropping the partial last bucket (at 10:32 the "10:00 1h
    bar" holds 32 minutes), Binance / BingX klines always include the open candle — and the
    live tick reads iloc[-1], so the signal would come from a half bar the backtest never
    sees. Outside the scope nothing changes: paper fills, reports and the drift-band sigma
    read the current price / trim the forming bar themselves.
    """
    def __enter__(self):
        global _closed_bars_only
        _closed_bars_only += 1
        return self

    def __exit__(self, *exc):
        global _closed_bars_only
        _closed_bars_only -= 1
        return False


def _drop_forming_bar(df, interval, now=None, force=False):
    """Rows with label + interval <= now (label = bar open time, crypto/UTC/continuous).
    No-op outside closed_bars_only() unless force; a weekly-or-longer or unparseable
    interval passes through untouched — its label convention is not open-time."""
    if not (force or _closed_bars_only):
        return df
    try:
        td = pd.Timedelta(interval)
    except (ValueError, TypeError):
        return df
    if df.empty or not isinstance(df.index, pd.DatetimeIndex) or td >= pd.Timedelta(days=7):
        return df
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if df.index.tz is None:
        now = (now.tz_convert('UTC') if now.tz is not None else now).tz_localize(None)
    elif now.tz is None:
        now = now.tz_localize('UTC')
    return df[df.index + td <= now]


def _fetch_kline_raw(symbol, interval, start, end, headers=None, max_retries=6):
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
            r = _retry_get(f'{BASE}/kline', headers=headers, max_retries=max_retries, params={
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


def fetch_kline(symbol, interval, start, end, headers=None, max_retries=6):
    """Fetch OHLCV kline data from Blave API with date chunking and local cache.

    All intervals reach back to the symbol's Binance um-futures listing date
    (sub-5min included — the API backfills old months from Binance's official
    archive). A window before listing returns empty, not an error. Sub-5min
    requests are chunked 30 days each server-side, so deep 1min backtests pull
    history month-by-month on first run. Cache namespace is kline2 — the old
    kline cache has Volume hard-zeroed and must not be mixed with real volume.

    With BLAVE_KLINE_SOURCE=binance (the desktop build's BYO data) the bars come
    straight from Binance's public endpoint instead, `headers` unused. Same
    market (USDT-M perps), same columns, same kline2 cache. Not literally the
    same bars: measured 2026-09-19, 8 of 41,335 1h BTCUSDT bars come back from
    /kline as placeholders (O=H=L=C, Volume 0) where Binance has the real bar,
    so a cache dir fed by both sources is a mixed one.
    """
    # Venue forms like 'BTC/USDT' → Binance 'BTCUSDT'; the API 400s on
    # separator forms and the separator would leak into the cache dir name.
    symbol = normalize_symbol(symbol)
    if _kline_source() == 'binance':
        fetch_raw = lambda s, e: _resolve('_fetch_binance_kline_raw')(symbol, interval, s, e)
    else:
        if headers is None or 'api-key' not in headers:
            h = _default_headers() or {'api-key': '', 'secret-key': ''}
        else:
            h = headers
        fetch_raw = lambda s, e: _resolve('_fetch_kline_raw')(symbol, interval, s, e, h, max_retries)
    df = _resolve('_extend_cache_monthly')(
        'kline2', {'symbol': symbol, 'period': interval},
        fetch_raw,
        start, end,
    )
    return _drop_forming_bar(_sanity_check_ohlc(df, f'{symbol} {interval} kline'), interval)


def fetch_kline_batch(symbols, interval, start, end, headers=None):
    """Batch fetch OHLCV kline for many symbols via /kline/batch (chunk_size=20).
    Returns dict {symbol: DataFrame(Open, High, Low, Close, Volume)} — keys are
    the NORMALIZED canonical symbols (see normalize_symbol), not the caller's
    original strings: index the result with 'BTCUSDT' even if you passed 'BTC/USDT'.

    Uses the same monthly cache dir naming as fetch_kline ('kline2_{interval}_{symbol}')
    so single-symbol and batch calls share cache — a symbol already cached via
    fetch_kline is a warm hit here too, and vice versa. Warm ids are extended through
    the batch endpoint too (not one call per symbol) — see _fetch_batch_cached.

    Under BLAVE_KLINE_SOURCE=binance there is no batch endpoint to call, so this
    fans out to fetch_kline per symbol — otherwise a desktop Type C backtest
    would quietly keep pulling its prices from api.blave.org."""
    symbols = [normalize_symbol(s) for s in symbols]
    if _kline_source() == 'binance':
        return _binance_batch(list(dict.fromkeys(symbols)), interval, start, end, headers)
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

    results = _resolve('_fetch_batch_cached')(
        f'kline2_{interval}', f'{BASE}/kline/batch?period={interval}', 'symbols',
        lambda sid, s, e, hdrs: _resolve('_fetch_kline_raw')(sid, interval, s, e, hdrs),
        _parse, symbols, start, end, headers,
        chunk_size=20, start_param='start_date', end_param='end_date',
        date_chunk_days=30 if _is_sub_5min(interval) else 365,
    )
    return {sid: _drop_forming_bar(_sanity_check_ohlc(df, f'{sid} {interval} kline'), interval)
            for sid, df in results.items()}


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
    df = _resolve('_extend_cache_monthly')(
        'bingx_kline', {'symbol': symbol, 'period': interval},
        lambda s, e: _fetch_bingx_kline_raw(symbol, interval, s, e),
        start, end,
    )
    return _drop_forming_bar(_sanity_check_ohlc(df, f'{symbol} {interval} bingx_kline'), interval)


# ── BYO kline source: Binance fapi, no key ────────────────────────────────────
# The desktop build is free software running on the user's own machine, so its
# market data is BYO: with BLAVE_KLINE_SOURCE=binance, fetch_kline pages the
# public Binance endpoint directly instead of api.blave.org. Off unless that
# variable is set, so the fleet never reaches this code.

# USDT-M perpetuals, deliberately — that is the market /kline serves and the
# collector stores. Pointing this at spot (api.binance.com/api/v3/klines) would
# make the same strategy backtest differently on the desktop than in the cloud,
# because basis and funding live in the perp price and not in the spot price.
_BINANCE_KLINES = 'https://fapi.binance.com/fapi/v1/klines'
_BINANCE_PAGE   = 1000          # server cap per response, not a preference

# fapi's own exchangeInfo reports 2400 request-weight per minute and a 1000-bar
# kline page costs 5 (measured off the x-mbx-used-weight-1m header, not the
# docs). 400 pages/min = 2000 weight, leaving headroom for whatever else the box
# is doing; the 429 handling below is the backstop, not the throttle.
_BINANCE_LIMITER = _RateLimiter(400, 60)
# At most 10 Binance requests in flight per process — one symbol's page pool was already 10;
# fetch_kline_batch running symbols side by side must not multiply it (IP-level 429s).
_BINANCE_INFLIGHT = threading.BoundedSemaphore(10)


# _BINANCE_LIMITER / _BINANCE_INFLIGHT are read off the package per call, not off this module's
# globals: checks patch lib.data.<name> (a fast fake limiter, or a 3-slot semaphore to prove the
# cap is honored), and _binance_get resolving them in its own globals would sleep for real.
def _binance_limiter():
    return getattr(_pkg, '_BINANCE_LIMITER', _BINANCE_LIMITER)


def _binance_inflight():
    return getattr(_pkg, '_BINANCE_INFLIGHT', _BINANCE_INFLIGHT)

# Binance and BingX spell intervals identically, so the lib's own '1min' family
# maps onto both. Binance spellings map to themselves: lib/paper_data calls
# fetch_kline with '1m'.
_BINANCE_INTERVALS = {**_BINGX_INTERVALS, **{v: v for v in _BINGX_INTERVALS.values()}}


def _binance_get(url, params, max_retries=6, timeout=30, max_wait=None):
    """GET a public Binance endpoint, honouring Retry-After on 429/418.

    Deliberately not _retry_get: that one is the fleet's path to our own API and
    its fixed 2/4/8… backoff is tuned for it. Binance answers a rate-limit with
    the exact number of seconds to wait and escalates an ignored 429 into a 418
    IP ban, so guessing the wait here is the wrong move.
    """
    for attempt in range(max_retries):
        _binance_limiter().acquire()
        try:
            with _binance_inflight():
                r = requests.get(url, params=params, timeout=timeout)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f'  {type(e).__name__} transient — retrying in {wait}s (binance klines)')
            time.sleep(wait)
            continue
        if r.status_code in (429, 418) or r.status_code >= 500:
            if attempt == max_retries - 1:
                break
            try:
                wait = int(r.headers.get('Retry-After', ''))
            except ValueError:
                wait = 2 ** (attempt + 1)
            print(f'  {r.status_code} from Binance — retrying in {wait}s')
            if max_wait is not None and wait > max_wait:
                break   # a caller that cannot wait (a report brick) gives up instead
            time.sleep(min(wait, 300 if max_wait is None else max_wait))
            continue
        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            # Binance puts the reason in the body ({"code":-1121,"msg":"Invalid
            # symbol."}); raise_for_status() alone would say only "400".
            raise requests.HTTPError(f'{exc} — {r.text[:200]}', response=r) from exc
        return r
    r.raise_for_status()
    return r


def _binance_klines_to_df(rows):
    """Binance's array-of-arrays → the five-column frame every lib consumer eats.

    Index 0 is the bar's open time in ms, 1-4 OHLC, 5 the base-asset volume;
    everything after (close time, quote volume, taker splits) is dropped.
    """
    cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame([row[:6] for row in rows], columns=['time'] + cols)
    df['time'] = pd.to_datetime(df['time'].astype('int64'), unit='ms', utc=True)
    df = df.set_index('time').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df[cols].astype(float)


def _fetch_binance_kline_raw(symbol, interval, start, end):
    """_fetch_kline_raw's twin against Binance. Same 30/365-day chunking, so the
    monthly cache sees the same spans either way; inside a chunk we page forward
    on startTime because one response is capped at 1000 bars — a 30-day 1min
    chunk is 43,200 of them.
    """
    bn_interval = _BINANCE_INTERVALS.get(interval)
    if bn_interval is None:
        raise ValueError(f"fetch_kline (binance source): unsupported interval {interval!r} "
                         f"(supported: {', '.join(sorted(_BINANCE_INTERVALS))})")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    chunks, cursor = [], s
    chunk_days = 30 if _is_sub_5min(interval) else 365
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end

    to_ms = lambda d: int((d - _EPOCH).total_seconds() * 1000)

    def _fetch_one(cs, ce):
        rows, cursor_ms, end_ms = [], to_ms(cs), to_ms(ce)
        while cursor_ms <= end_ms:
            page = _resolve('_binance_get')(_BINANCE_KLINES, {
                'symbol': symbol, 'interval': bn_interval,
                'startTime': cursor_ms, 'endTime': end_ms, 'limit': _BINANCE_PAGE,
            }).json()
            if not page:
                break
            rows.extend(page)
            nxt = int(page[-1][0]) + 1
            if nxt <= cursor_ms:
                break                      # no progress — stop instead of spinning forever
            cursor_ms = nxt
            if len(page) < _BINANCE_PAGE:
                break                      # short page = this window is exhausted
        return rows

    rows = []
    progress = Progress(f'fetch {symbol} {interval} (binance)', len(chunks), 'chunks')
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_fetch_one, cs, ce) for cs, ce in chunks]
        for future in as_completed(futures):
            rows.extend(future.result())
            progress.tick()
    return _binance_klines_to_df(rows)


def _binance_batch(uniq, interval, start, end, headers):
    """fetch_kline per symbol, up to 4 side by side (the limiter and _BINANCE_INFLIGHT still pace
    the requests). Sub-5-minute intervals stay one at a time: a cold year of 1m bars is ~360 MB of
    raw rows per symbol, so four at once would quadruple a large backtest's peak memory. The first
    failure stops the rest (cancel what has not started) and is raised, as the sequential loop did."""
    from concurrent.futures import ThreadPoolExecutor, FIRST_EXCEPTION, wait
    # fetch_kline is read off the package for the same reason as _fetch_kline_raw: a check that
    # patches lib.data.fetch_kline to spy on the call shape must reach a fan-out helper too.
    _fetch = _resolve('fetch_kline')
    workers = 1 if _is_sub_5min(interval) else min(4, max(1, len(uniq)))
    if workers == 1:
        return {sid: _fetch(sid, interval, start, end, headers) for sid in uniq}
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {sid: pool.submit(_fetch, sid, interval, start, end, headers) for sid in uniq}
        done, _ = wait(futs.values(), return_when=FIRST_EXCEPTION)
        for f in done:
            if f.exception() is not None:
                pool.shutdown(wait=True, cancel_futures=True)
                raise f.exception()
        return {sid: f.result() for sid, f in futs.items()}
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
