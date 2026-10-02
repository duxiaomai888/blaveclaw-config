"""Taiwan futures data — TXF/MTX OHLCV, institutional, PCR, bid/ask vol, stock futures.

拆分自原 lib/data.py。txf_settlement_mask（結算日遮罩）也在這裡——它是所有
TAIFEX 月結算商品的單一真理源（Type A/C 策略的 compute_signals 都要套）。
"""
import io
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

import lib.data as _pkg
from .http import BASE, _get
_retry_get = lambda *a, **k: _call_through('_retry_get', *a, **k)
from ._shared import _sanity_check_ohlc, _check_data_access, _call_through
from .cache import _extend_cache_monthly

__all__ = [
    '_TW_FUTURES_CHUNK_DAYS', '_fetch_twfutures_raw',
    '_ExportUnavailable', '_TW_FUTURES_RESAMPLE_RULES',
    '_fetch_twfutures_via_export', '_fetch_twfutures_raw_smart',
    'fetch_twfutures_ohlcv', 'fetch_twfutures_ohlcv_batch',
    '_fetch_twfutures_bid_ask_vol_raw', 'fetch_twfutures_pcr',
    '_TWFUT_INST_INVESTORS', '_TWFUT_INST_COLUMNS', '_TWFUT_INST_ALIASES',
    '_fetch_twfutures_institutional_raw', 'fetch_twfutures_institutional',
    'fetch_twfutures_bid_ask_vol',
    'fetch_stock_futures_batch_daily', 'fetch_stock_futures_ohlcv_symbols',
    'txf_settlement_mask',
]


_TW_FUTURES_CHUNK_DAYS = {'1d': 3650, '1m': 28, '5m': 28, '15m': 28, '30m': 28, '60m': 28}


def _fetch_twfutures_raw(symbol, schema, start, end, headers=None):
    _check_data_access(headers or {})
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    chunk_days = _TW_FUTURES_CHUNK_DAYS.get(schema, 28)

    chunks, cursor = [], s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        r = _get(
            f'{BASE}/studio/market/twfutures/ohlcv/{symbol}/{schema}',
            headers=headers,
            params={'start': cs, 'end': ce},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get('data', [])

    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_one, cs, ce): (cs, ce) for cs, ce in chunks}
        for future in as_completed(futures):
            rows.extend(future.result())

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df['time'] = pd.to_datetime(df['ts'], utc=True)
    df = df.set_index('time').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                             'close': 'Close', 'volume': 'Volume'})
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)


class _ExportUnavailable(Exception):
    """Bulk-export endpoint not deployed / symbol not served — fall back to chunked JSON."""


_TW_FUTURES_RESAMPLE_RULES = {'5m': '5min', '15m': '15min', '30m': '30min', '60m': '60min'}


def _fetch_twfutures_via_export(symbol, schema, start, end, headers=None):
    """Fetch intraday OHLCV via the 1m-parquet bulk export endpoint
    (GET /studio/market/twfutures/ohlcv/<symbol>/export/<year>) and resample locally.

    One request per calendar year, zero server-side computation — the server just
    streams its own year parquet. Resample semantics replicate the server's
    (resample(rule).agg(first/max/min/last/sum), dropna on open), so the output is
    interchangeable with _fetch_twfutures_raw's. Processes one year at a time to
    keep peak memory at ~one year of 1m bars (~20MB), not the full span.

    Raises _ExportUnavailable when the endpoint isn't deployed yet (or rejects the
    symbol) so the caller can fall back to the chunked JSON path.
    """
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    rule = _TW_FUTURES_RESAMPLE_RULES.get(schema)  # None for '1m' — no resample

    frames = []
    for year in range(s.year, e.year + 1):
        try:
            # _retry_get backs off on 429/5xx — matters when downloading many
            # year files in a row (100 symbols × 8 years brushes the rate limit)
            r = _retry_get(
                f'{BASE}/studio/market/twfutures/ohlcv/{symbol}/export/{year}',
                headers=headers, timeout=120,
            )
        except requests.HTTPError as exc:
            resp = exc.response
            if resp is not None and resp.status_code == 404:
                try:
                    if resp.json().get('error') == 'no_data':
                        continue  # valid year, just no data (e.g. before backfill start)
                except ValueError:
                    pass
                raise _ExportUnavailable(f'export route missing for {symbol}/{year}')
            raise _ExportUnavailable(f'export {symbol}/{year} -> '
                                     f'{resp.status_code if resp is not None else exc}')

        raw = pd.read_parquet(io.BytesIO(r.content))
        if raw.empty:
            continue
        raw.index = pd.to_datetime(raw['ts'], utc=True)
        data = raw[['open', 'high', 'low', 'close', 'volume']].sort_index()
        data = data[~data.index.duplicated(keep='first')]
        if rule:
            data = data.resample(rule).agg(
                open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                close=('close', 'last'), volume=('volume', 'sum'),
            ).dropna(subset=['open'])
        frames.append(data)

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    df = df[(df.index >= pd.Timestamp(start, tz='UTC')) &
            (df.index < pd.Timestamp(e.strftime('%Y-%m-%d'), tz='UTC') + pd.Timedelta(days=1))]
    df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                             'close': 'Close', 'volume': 'Volume'})
    df.index.name = 'time'
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)


def _fetch_twfutures_raw_smart(symbol, schema, start, end, headers=None):
    """Long intraday spans → try the bulk-export path first (zero server CPU, one
    request per year); short spans, '1d', or export-unavailable → chunked JSON API."""
    if schema != '1d':
        s = datetime.strptime(start, '%Y-%m-%d')
        e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
        if (e - s).days > 62:
            try:
                return _fetch_twfutures_via_export(symbol, schema, start, end, headers)
            except _ExportUnavailable as exc:
                print(f'  [twfutures] export unavailable ({exc}); falling back to chunked fetch')
    return _fetch_twfutures_raw(symbol, schema, start, end, headers)


def fetch_twfutures_ohlcv(symbol, schema, start, end, headers=None):
    """台灣期貨 OHLCV. Returns DataFrame with Open/High/Low/Close/Volume/Amount columns.

    symbol: 'TXF' ('MXF'/'TMF' accepted as aliases — see below)
    schema: '1d' | '1m' | '5m' | '15m' | '30m' | '60m'
    Volume is in contracts (口數).

    A Shioaji-style 'R1' suffix (TXFR1, MXFR1, CDFR1…) is accepted and mapped to
    the endpoint's own name (TXF…): the underlying series IS the R1 continuous
    near-month, only the naming differs. 'R2' (next-month continuous) is NOT this
    data and is deliberately not mapped — it still 400s server-side.

    'MXF' / 'TMF' are EXECUTION-INSTRUMENT aliases for the TXF series: a
    strategy's SYMBOL declares the contract it actually trades (大台/小台/微台),
    but signals and backtests always run on TXF data — arbitrage pins all three
    to the same price, TXF minute history is the deepest, and the server has no
    TMF minute data at all. Both map to 'TXF' here (shared cache dir), so
    SYMBOL='TMF' fetches TXF bars while the order layer trades TM0000.

    For 1d: index is Asia/Taipei tz so df.index[-1].date() returns the correct trading date.
    """
    symbol = symbol.upper()
    if symbol.endswith('R1') and len(symbol) > 2:
        symbol = symbol[:-2]
    if symbol in ('MXF', 'TMF'):
        symbol = 'TXF'
    df = _extend_cache_monthly(
        f'twfutures_{schema}', {'symbol': symbol},
        lambda s, e: _fetch_twfutures_raw_smart(symbol, schema, s, e, headers),
        start, end,
        empty_marker_ttl_hours=24,   # history is backfilled progressively server-side
    )
    df = _sanity_check_ohlc(df, f'{symbol} {schema} twfutures')
    if schema == '1d' and not df.empty:
        # Cache stores naive UTC (midnight TWN = prev-day 16:00 UTC). Convert to Asia/Taipei
        # so the index date matches the actual trading date.
        df = df.copy()
        df.index = pd.to_datetime(df.index, utc=True).tz_convert('Asia/Taipei')
    return df


def fetch_twfutures_ohlcv_batch(symbols, schema, start, end, headers=None, max_workers=8):
    """Batch fetch_twfutures_ohlcv across many symbols, concurrently.

    Same per-symbol semantics (monthly cache, export-first for long intraday
    spans, chunked fallback) — this just runs the symbols through a thread pool
    so the per-request fixed overhead (auth round-trips) is amortised instead
    of paid serially. Safe to parallelise: each symbol has its own cache dir.

    Returns dict {symbol: DataFrame} for symbols that succeeded; failures are
    dropped with a printed warning (mirrors fetch_stock_futures_batch_daily).
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_twfutures_ohlcv, sym, schema, start, end, headers): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                results[sym] = future.result()
            except Exception as e:
                print(f"  [twfutures batch] skip {sym}: {e}")
    return results


def _fetch_twfutures_bid_ask_vol_raw(start, end, headers=None):
    """Fetch raw bid/ask vol for a date range (≤31 days per chunk)."""
    _check_data_access(headers or {})
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)
    chunk_days = 28

    chunks, cursor = [], s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        r = _get(
            f'{BASE}/studio/market/twfutures/bid_ask_vol/TXF',
            headers=headers,
            params={'start': cs, 'end': ce},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get('data', [])

    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_one, cs, ce): (cs, ce) for cs, ce in chunks}
        for future in as_completed(futures):
            rows.extend(future.result())

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df['time'] = pd.to_datetime(df['ts'], utc=True)
    df = df.set_index('time').sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df[['bid_vol', 'ask_vol', 'total_vol']].astype(int)


def fetch_twfutures_pcr(start, end, headers=None):
    """台指選擇權買賣權未平倉量比率（日）. Returns DataFrame with 'pcr' column.

    Source: TAIFEX (台灣期貨交易所). (History range: see the blave-quant skill / Notion API doc.)
    index: date (daily, trading days only)
    pcr: 買賣權未平倉量比率%
    """
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twfutures/option/pcr',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=['pcr'])
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()[['pcr']].astype(float)


_TWFUT_INST_INVESTORS = (('foreign', '外資'), ('investment_trust', '投信'), ('dealer', '自營商'))
_TWFUT_INST_COLUMNS = [f'{k}_{m}' for k, _ in _TWFUT_INST_INVESTORS
                       for m in ('net_oi', 'long_oi', 'short_oi', 'net_deal')]
# 用戶會拿執行商品名(TXF/MXF)來問法人籌碼,但 TAIFEX 的法人統計按商品代號分:
# TX=台指期、MTX=小台。微台的代號就是 TMF,有自己的一套法人統計(與 MTX 數字不同),直通。
_TWFUT_INST_ALIASES = {'TXF': 'TX', 'MXF': 'MTX'}


def _fetch_twfutures_institutional_raw(futures_id, start, end, headers=None):
    """Raw fetch → one row per date, 12 float columns (see _TWFUT_INST_COLUMNS).
    The endpoint returns 3 rows per day (外資/投信/自營商); pivoted here so the
    cache layer sees a plain date-indexed frame like the twmarket_* series."""
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twfutures/institutional/{futures_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=_TWFUT_INST_COLUMNS)
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    out = {}
    for key, label in _TWFUT_INST_INVESTORS:
        sub = df[df['institutional_investors'] == label].set_index('date').sort_index()
        sub = sub[~sub.index.duplicated(keep='last')]
        long_oi = sub['long_open_interest_balance_volume'].astype(float)
        short_oi = sub['short_open_interest_balance_volume'].astype(float)
        out[f'{key}_net_oi'] = long_oi - short_oi
        out[f'{key}_long_oi'] = long_oi
        out[f'{key}_short_oi'] = short_oi
        out[f'{key}_net_deal'] = (sub['long_deal_volume'].astype(float)
                                  - sub['short_deal_volume'].astype(float))
    return pd.DataFrame(out).sort_index()


def fetch_twfutures_institutional(futures_id, start, end, headers=None):
    """期貨三大法人未平倉與交易口數(日). Returns a date-indexed DataFrame with
    12 float columns, 口數:
      {foreign|investment_trust|dealer}_net_oi   未平倉淨口數(多 − 空)——「外資期貨淨多單」就是 foreign_net_oi
      {…}_long_oi / {…}_short_oi                  未平倉多方 / 空方口數
      {…}_net_deal                                當日交易淨口數(買 − 賣)

    futures_id: 'TX'(台指期)、'MTX'(小台)、'TMF'(微台,有自己的統計);'TE'/'TF' 端點接受但
    未實測。執行商品名 'TXF'→'TX'、'MXF'→'MTX' 自動對應。
    個股期貨沒有法人統計(伺服器回 404)。資料來源 TAIFEX,盤後約 15:00 後才有當日。
    Cached like the twmarket_* daily series (one parquet per id, delta-updated).
    Raw 3-rows-per-day layout: see references/twfutures.md.
    """
    futures_id = _TWFUT_INST_ALIASES.get(futures_id.upper(), futures_id.upper())
    return _extend_cache_monthly(
        'twfutures_institutional', {'id': futures_id},
        lambda s, e: _fetch_twfutures_institutional_raw(futures_id, s, e, headers),
        start, end,
    )


def fetch_twfutures_bid_ask_vol(start, end, headers=None):
    """台指期內外盤成交量（1 分鐘）. Returns DataFrame indexed by UTC time.

    Columns: bid_vol (內盤口數), ask_vol (外盤口數), total_vol (總口數).
    Both day session (08:45-13:45 TWN) and night session included.
    (History range: see the blave-quant skill / Notion API doc.)
    Monthly cache: cache/twfutures_bav_TXF/YYYY-MM.parquet
    """
    result = _extend_cache_monthly(
        'twfutures_bav', {'symbol': 'TXF'},
        lambda s, e: _fetch_twfutures_bid_ask_vol_raw(s, e, headers),
        start, end,
        empty_marker_ttl_hours=24,   # history is backfilled progressively server-side
    )
    if result.empty:
        return result
    for col in ['bid_vol', 'ask_vol', 'total_vol']:
        if col in result.columns:
            result[col] = result[col].astype(int)
    return result


def fetch_stock_futures_batch_daily(futures_ids, start, end, headers=None):
    """Batch daily OHLCV/OI for individual stock futures (max 250 ids per call,
    server-side parallel fetch + cache). Returns dict {futures_id: DataFrame}
    for ids with data; ids that hit persistent upstream rate-limiting are
    dropped and printed as a warning (a genuinely empty dataset for a valid id
    is not an error, just an empty DataFrame — not omitted).

    Locally cached per (futures_id, start, end) under cache/twfutures_stockfut/ —
    an EXACT-range cache, not the monthly-delta cache the other twstock/twfutures
    fetchers use. This dataset has multiple rows per day per id (every listed
    contract month x trading_session), so the monthly cache's dedup-by-date would
    silently collapse those down to one row per day. An exact-range cache avoids
    that at the cost of not supporting incremental "extend to today" delta fetches —
    with END=None (the standard config, END is never pinned) the resolved end date
    moves daily, so the first run each day re-fetches the full range; later runs the
    same day hit the cache.

    Same fields as fetch_twfutures_daily: date, futures_id, contract_date,
    open, max, min, close, spread, spread_per, volume, settlement_price,
    open_interest, trading_session. futures_ids must be valid stock futures
    ids (股票期貨, e.g. 'CDF') — arbitrary ids are rejected (400).
    """
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')

# lib.data._CACHE_DIR is the patch surface a check redirects; this module's own import would not
# see it (a scratch dir for the run), so read it per call.
def _cache_dir():
    return getattr(_pkg, '_CACHE_DIR')


    cache_dir = _cache_dir() / 'twfutures_stockfut'
    cache_dir.mkdir(parents=True, exist_ok=True)

    results, to_fetch = {}, []
    for fid in futures_ids:
        path = cache_dir / f'{fid}__{start}__{end_str}.parquet'
        if path.exists():
            results[fid] = pd.read_parquet(path)
        else:
            to_fetch.append(fid)

    def _fetch_chunk(chunk):
        r = _retry_get(
            f'{BASE}/studio/market/twfutures/stock_futures/batch/daily',
            headers=headers,
            params={'futures_ids': ','.join(chunk), 'start': start, 'end': end},
            timeout=120,
        )
        body = r.json()
        failed = body.get('failed', [])
        if failed:
            print(f"[fetch_stock_futures_batch_daily] rate-limited after retries, dropped: {failed}")
        for fid, rows in body.get('data', {}).items():
            df = pd.DataFrame(rows)
            df.to_parquet(cache_dir / f'{fid}__{start}__{end_str}.parquet')
            results[fid] = df

    chunks = [to_fetch[i:i + 200] for i in range(0, len(to_fetch), 200)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_fetch_chunk, chunks))

    return results


def fetch_stock_futures_ohlcv_symbols(headers=None):
    """Currently-allowed symbols for fetch_twfutures_ohlcv (intraday/minute-line
    coverage) — always includes 'TXF' plus whichever individual stock futures
    ids currently have backfilled Shioaji minute-line data (a dynamically-
    growing subset of the 231 total). Returns a plain list of symbol strings.

    Call this before fetch_twfutures_ohlcv on a stock future to check coverage
    up front, instead of trial-and-erroring against the 400 response.

    Entries are suffix-less names ('TXF', 'CDF') — strip a Shioaji-style 'R1'
    suffix before the membership check (fetch_twfutures_ohlcv itself accepts
    'CDFR1' and maps it, but 'CDFR1' will never appear in this list). 'MXF' /
    'TMF' likewise never appear — fetch_twfutures_ohlcv aliases them to 'TXF'.
    """
    r = _retry_get(f'{BASE}/studio/market/twfutures/ohlcv/symbols', headers=headers, timeout=30)
    return r.json().get('data', [])


def txf_settlement_mask(index):
    """Return a boolean Series (same index) that is True on the last bar strictly
    before each TAIFEX monthly settlement (3rd Wednesday, 13:30 TWN).

    Interval-agnostic: 1m data marks the 13:29 bar, 60m data marks the 13:00 bar,
    etc. Applies to every TAIFEX monthly-settled product — TXF and individual
    stock futures share the same settlement calendar — and MUST be applied by any
    strategy on `fetch_twfutures_*` data: the source is Shioaji's R1 continuous
    near-month series, which switches contracts at settlement WITHOUT price
    adjustment, so an unmasked position books the contract-basis gap as fake PnL
    (measured 2018-2026 across 10 stock futures: mean +0.36%/roll, std 3.9%,
    August dividend-season mean -1.9%).

    Usage in compute_signals:
        settle = txf_settlement_mask(df.index)
        signal[settle] = 0.0        # Type A;  Type C: weights.loc[settle] = 0.0
        return signal, settle       # settle doubles as exec_at_close
    """
    import datetime
    from zoneinfo import ZoneInfo   # stdlib — no pytz dependency (pandas 3.x stopped pulling it in;
                                    # a fresh Windows box had no pytz and every 台指期 backtest died here)

    twn = ZoneInfo('Asia/Taipei')   # ZoneInfo handles offsets/DST natively — no pytz localize() needed

    def _third_wed(year, month):
        d, count = datetime.date(year, month, 1), 0
        while True:
            if d.weekday() == 2:
                count += 1
                if count == 3:
                    return d
            d = d + datetime.timedelta(days=1)

    mask  = pd.Series(False, index=index)
    start = index.min()
    end   = index.max()
    # Reach one bar past the last label: on a live tick the pre-settlement bar IS the last
    # bar, and bounding the loop at index.max() never considered the settlement it precedes,
    # so live held through settlement while the backtest (which sees the next bar) was flat.
    # One bar, not one day — a day would mark the 12:00 tick and flatten hours early.
    bar = index.to_series().diff().median() if len(index) > 1 else pd.NaT
    if pd.notna(bar):
        end = end + bar

    year, month = start.year, start.month
    while True:
        wed = _third_wed(year, month)
        ts_settle = pd.Timestamp(
            datetime.datetime(wed.year, wed.month, wed.day, 13, 30, tzinfo=twn)
            .astimezone(datetime.timezone.utc)
        )
        if index.tz is None:
            ts_settle = ts_settle.tz_localize(None)
        if ts_settle > end:
            break
        # last bar with label strictly before the settlement moment; guard
        # against marking a far-away bar when the symbol has a data gap
        pos = index.searchsorted(ts_settle) - 1
        if pos >= 0 and (ts_settle - index[pos]) <= pd.Timedelta(days=1):
            mask.iloc[pos] = True
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return mask
