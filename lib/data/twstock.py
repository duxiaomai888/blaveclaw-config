"""Taiwan stock data — daily/minute OHLCV, institutional, shareholding, dividend, fundamentals, broker/trader flows.

拆分自原 lib/data.py。個股層級的所有 fetch_twstock_* 在這裡;全市場層級用
lib/data/twmarket.py。批量骨架(_fetch_batch_cached / _fetch_twstock_cached_batch)
和 TTL 單檔快取(_fundamental_cache_path 等)分別在 .batch / .cache。
"""
import numbers
import time
from datetime import datetime, date as _date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from .http import BASE, _CACHE_DIR, _retry_get, _session, _RateLimiter
from ._shared import _sanity_check_ohlc
from .cache import (
    _extend_cache_monthly,
    _fundamental_cache_path, _load_fundamental_cache, _save_fundamental_cache,
)
from .batch import _fetch_twstock_cached_batch

__all__ = [
    '_fetch_twstock_price_raw', 'fetch_twstock_price_adj',
    '_fetch_twstock_price_nonadj_raw', 'fetch_twstock_price',
    'fetch_twstock_quote', 'fetch_twstock_quote_batch',
    '_TWSTOCK_MINUTE_CHUNK_DAYS', '_TWSTOCK_MINUTE_MAX_DAYS',
    '_fetch_twstock_minute_raw', 'fetch_twstock_ohlcv', 'fetch_twstock_ohlcv_symbols',
    '_fetch_twstock_inst_raw', 'fetch_twstock_institutional',
    '_fetch_twstock_shareholding_raw', 'fetch_twstock_shareholding',
    '_fetch_twstock_per_raw', 'fetch_twstock_per',
    '_DIVIDEND_COLUMNS', '_dividend_slice', 'fetch_twstock_dividend', 'fetch_twstock_dividend_batch',
    '_broker_day_cache_path', '_make_date_chunks',
    '_populate_broker_day_cache', '_populate_trader_day_cache',
    'fetch_twstock_broker_net', 'fetch_twstock_all_broker_net', 'fetch_twstock_branch_daily_net',
    '_fetch_twstock_fundamental_raw', '_fetch_fundamental',
    'fetch_twstock_financials', 'fetch_twstock_balance_sheet', 'fetch_twstock_monthly_revenue',
    '_twstock_list_cache_path', 'fetch_twstock_list', 'fetch_twstock_info', 'fetch_twstock_market_value_all',
    '_fetch_fundamental_batch',
    'fetch_twstock_financials_batch', 'fetch_twstock_balance_sheet_batch', 'fetch_twstock_monthly_revenue_batch',
    'fetch_twstock_shareholding_batch', 'fetch_twstock_price_adj_batch',
    'fetch_twstock_price_batch', 'fetch_twstock_per_batch', 'fetch_twstock_institutional_batch',
    '_fetch_twstock_foreign_shareholding_raw', 'fetch_twstock_foreign_shareholding_batch',
    '_trader_day_cache_path', 'fetch_twstock_trader_flows',
]


def _fetch_twstock_price_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/price_adj/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=['Open', 'Close'])
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()[['open', 'close']].rename(
        columns={'open': 'Open', 'close': 'Close'}).astype(float)
    return df.replace(0, float('nan')).ffill()


def fetch_twstock_price_adj(stock_id, start, end, headers=None):
    """台股向後調整日K（除權息還原價）. Returns DataFrame with Open/Close columns.
    Use for backtesting — prices are dividend-adjusted so returns are comparable across time."""
    return _extend_cache_monthly(
        'twstock_price', {'id': stock_id},
        lambda s, e: _fetch_twstock_price_raw(stock_id, s, e, headers),
        start, end,
    )


def _fetch_twstock_price_nonadj_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/price/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
    df = df.set_index('date').sort_index()[cols].rename(
        columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}).astype(float)
    return df.replace(0, float('nan')).ffill()


def fetch_twstock_price(stock_id, start, end, headers=None):
    """台股原始日K（未除權息）. Returns DataFrame with Open/High/Low/Close/Volume columns.
    Use for visualization/charting — matches prices users see on broker apps.
    Do NOT use for backtesting (dividends cause artificial price drops that distort signals)."""
    df = _extend_cache_monthly(
        'twstock_price_nonadj', {'id': stock_id},
        lambda s, e: _fetch_twstock_price_nonadj_raw(stock_id, s, e, headers),
        start, end,
    )
    return _sanity_check_ohlc(df, f'{stock_id} twstock price')


def fetch_twstock_quote(stock_id, headers=None):
    """台股即時報價快照（約 10 秒更新）. Returns a flat dict — NOT a DataFrame, since a quote
    is a single point-in-time observation with no date range to index on. Keys: open/high/low/close
    (today so far), change_price, change_rate, average_price, volume (latest tick), total_volume
    (day cumulative), amount, total_amount, yesterday_volume, buy_price/buy_volume (best bid),
    sell_price/sell_volume (best ask), volume_ratio, quote_time (full timestamp), stock_id,
    tick_type (0=indeterminate/1=sell-initiated/2=buy-initiated).
    No local cache — the server enforces a 10s Redis TTL; every call means "right now"."""
    r = _retry_get(f'{BASE}/studio/market/twstock/quote/{stock_id}', headers=headers, timeout=30)
    return r.json().get('data', {})


def fetch_twstock_quote_batch(stock_ids, headers=None):
    """Batch 即時報價（最多 50 檔）. Returns dict {stock_id: quote_dict}, same fields as
    fetch_twstock_quote per entry. No local cache, same as the single-stock version."""
    r = _retry_get(f'{BASE}/studio/market/twstock/quote', headers=headers,
                    params={'stock_ids': ','.join(stock_ids)}, timeout=30)
    return r.json().get('data', {})


# ── Taiwan stock minute-line OHLCV ────────────────────────────────────────────

_TWSTOCK_MINUTE_CHUNK_DAYS = {'1d': 3650, '1m': 28, '5m': 28, '15m': 28, '30m': 28, '60m': 28}
# Server-side per-request range caps — also used as the default lookback window
# when start is omitted, matching the endpoint's own default.
_TWSTOCK_MINUTE_MAX_DAYS = {'1d': 3650, '1m': 31, '5m': 62, '15m': 93, '30m': 186, '60m': 365}


def _fetch_twstock_minute_raw(stock_id, schema, start, end, headers=None, adjust=False):
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    chunk_days = _TWSTOCK_MINUTE_CHUNK_DAYS.get(schema, 28)

    chunks, cursor = [], s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=chunk_days), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        r = _retry_get(
            f'{BASE}/studio/market/twstock/minute/ohlcv/{stock_id}/{schema}',
            headers=headers,
            params={'start': cs, 'end': ce, 'adjust': '1' if adjust else '0'},
            timeout=60,
        )
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


def fetch_twstock_ohlcv(stock_id, schema, headers=None, start=None, end=None, adjust=False):
    """台股現股分線 OHLCV. Returns DataFrame with Open/High/Low/Close/Volume columns.

    stock_id: any listed TWSE/TPEx security (e.g. '2330')
    schema: '1d' | '1m' | '5m' | '15m' | '30m' | '60m'
    Volume is in lots (張), NOT shares. Bars carry minute-START labels (UTC
    index); the 13:30 Taipei bar is the closing auction. start/end optional
    (YYYY-MM-DD): omitted end = today, omitted start = end minus the server's
    max window for the schema (1m→31d, 5m→62d, 15m→93d, 30m→186d, 60m→365d,
    1d→3650d).

    adjust=True returns forward-adjusted (後復權) OHLC — use for backtests
    spanning ex-dividend dates; same factor pipeline as fetch_twstock_price_adj
    so the numbers match the Studio daily adjusted series exactly. Volume is
    unchanged. If the factor source is unavailable the server fails loud (503,
    retried then raised here) instead of silently returning raw prices.
    Raw and adjusted bars are cached in separate monthly cache dirs.

    History starts at 2019-01-01 (FinMind TaiwanStockKBar data origin); an
    earlier start is silently clamped to 2019-01-01 so pre-2019 months are
    never queried or cached.

    Server-side, the whole market's history is already backfilled from
    2019-01; only very newly listed stocks are demand-driven — seeded on
    their first-ever query and queued for deep backfill, so that query may
    return only recent data, with full history usually landing by the next
    day. Locally,
    an empty past month is cached with a 24-hour TTL (not permanently): once
    the server has the data, the next query after the TTL re-fetches and
    self-heals the cache.

    For 1d: index is Asia/Taipei tz so df.index[-1].date() returns the correct trading date.
    """
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    if not start:
        lookback = _TWSTOCK_MINUTE_MAX_DAYS.get(schema, 31)
        start = (datetime.strptime(end_str, '%Y-%m-%d')
                 - timedelta(days=lookback)).strftime('%Y-%m-%d')
    if start < '2019-01-01':
        start = '2019-01-01'
    df = _extend_cache_monthly(
        f'twstock_minute_{schema}', {'id': stock_id, 'adj': int(adjust)},
        lambda s, e: _fetch_twstock_minute_raw(stock_id, schema, s, e, headers, adjust=adjust),
        start, end,
        empty_marker_ttl_hours=24,
    )
    df = _sanity_check_ohlc(df, f'{stock_id} {schema} twstock minute')
    if schema == '1d' and not df.empty:
        # Cache stores naive UTC (midnight TWN = prev-day 16:00 UTC). Convert to Asia/Taipei
        # so the index date matches the actual trading date.
        df = df.copy()
        df.index = pd.to_datetime(df.index, utc=True).tz_convert('Asia/Taipei')
    return df


def fetch_twstock_ohlcv_symbols(headers=None):
    """Stocks that currently have minute-line data server-side — the covered set
    for fetch_twstock_ohlcv. Returns a plain list of stock_id strings.

    Unlike the stock-futures variant, absence here is not a hard 400: any listed
    TWSE/TPEx stock_id can still be queried, and the first query seeds recent
    data + enrolls the stock for ongoing collection. Call this first anyway to
    know whether deep history is already backfilled before running a backtest.
    """
    r = _retry_get(f'{BASE}/studio/market/twstock/minute/ohlcv/symbols',
                   headers=headers, timeout=30)
    return r.json().get('data', [])


def _fetch_twstock_inst_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/institutional/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()
    df['foreign_net'] = df['foreign_buy'] - df['foreign_sell']
    return df.fillna(0)


def fetch_twstock_institutional(stock_id, start, end, headers=None):
    """台股三大法人每日買賣超. Returns DataFrame with foreign_net and raw columns."""
    return _extend_cache_monthly(
        'twstock_inst', {'id': stock_id},
        lambda s, e: _fetch_twstock_inst_raw(stock_id, s, e, headers),
        start, end,
    )


def _fetch_twstock_shareholding_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/shareholding/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame(columns=['shareholders'])
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    total = df[df['level'] == 'total'].set_index('date').sort_index()
    result = total[['people']].rename(columns={'people': 'shareholders'}).astype(float)
    return result[~result.index.duplicated(keep='last')]


def fetch_twstock_shareholding(stock_id, start, end, headers=None):
    """台股週頻股東人數（持股分級表 total）. Returns DataFrame with 'shareholders' column."""
    return _extend_cache_monthly(
        'twstock_shareholding', {'id': stock_id},
        lambda s, e: _fetch_twstock_shareholding_raw(stock_id, s, e, headers),
        start, end,
    )


def _fetch_twstock_per_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/per/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()


def fetch_twstock_per(stock_id, start, end, headers=None):
    """台股每日本益比 / 股價淨值比 / 殖利率. Columns: dividend_yield, PER, PBR. Data from 2005-10-01."""
    return _extend_cache_monthly(
        'twstock_per', {'id': stock_id},
        lambda s, e: _fetch_twstock_per_raw(stock_id, s, e, headers),
        start, end,
    )


_DIVIDEND_COLUMNS = ['record_date', 'period', 'announce_date', 'cash_ex_date',
                     'stock_ex_date', 'pay_date', 'cash', 'stock', 'stock_ratio']


def _dividend_slice(df, start, end):
    """Range-filter on the API's three-tier effective date (cash_ex_date, else
    stock_ex_date, else record_date) — same ladder the server applies, so a
    locally-sliced full-history cache matches a server-side ranged query.
    Announced-but-undated rows fall through to record_date and stay visible."""
    if df.empty or (not start and not end):
        return df
    eff = df['cash_ex_date'].where(df['cash_ex_date'] != '', df['stock_ex_date'])
    eff = eff.where(eff != '', df['record_date'])
    mask = pd.Series(True, index=df.index)
    if start:
        mask &= eff >= start
    if end:
        mask &= eff <= end
    return df[mask]


def fetch_twstock_dividend(stock_id, start, end, headers=None):
    """台股股利事件 (one row per announcement row; cash + stock dividends).
    Columns: record_date, period, announce_date, cash_ex_date, stock_ex_date,
    pay_date, cash, stock, stock_ratio. Empty dates are '' (never NaN); `period`
    is an OPAQUE label ('114年第3季', '不適用', …) — never parse it as a year.
    Zero-value rows (cash==0 and stock==0) are announced no-distribution
    decisions and are kept. Returns an empty DataFrame for unknown ids / no
    dividend history (the API's 404). Full history is cached per stock with a
    1-day TTL (new announcements land daily) and sliced locally, so repeated
    calls with different ranges cost one API hit per stock per day."""
    path = _fundamental_cache_path('twstock_dividend', stock_id)
    df = _load_fundamental_cache(path, max_age_days=1)
    if df is None:
        try:
            r = _retry_get(f'{BASE}/studio/market/twstock/dividend/{stock_id}',
                           headers=headers, timeout=30)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return pd.DataFrame(columns=_DIVIDEND_COLUMNS)
            raise
        df = pd.DataFrame(r.json().get('data', []))
        if not df.empty:
            _save_fundamental_cache(path, df)
    return _dividend_slice(df, start, end).reset_index(drop=True)


def fetch_twstock_dividend_batch(stock_ids, start, end, headers=None):
    """Batch 台股股利事件. Returns dict {stock_id: DataFrame} (same columns as
    fetch_twstock_dividend). Ids with no dividend history are silently absent
    (the batch API's contract); ids in the API's `failed` list are reported and
    absent — re-call for those. Cache-first per stock (1-day TTL, full history),
    uncached ids fetched in chunks of 50; ranges sliced locally."""
    results, uncached = {}, []
    for sid in stock_ids:
        path = _fundamental_cache_path('twstock_dividend', sid)
        df = _load_fundamental_cache(path, max_age_days=1)
        if df is not None:
            results[sid] = _dividend_slice(df, start, end).reset_index(drop=True)
        else:
            uncached.append(sid)

    for i in range(0, len(uncached), 50):
        chunk = uncached[i:i + 50]
        try:
            r = _retry_get(f'{BASE}/studio/market/twstock/batch/dividend',
                           headers=headers,
                           params={'stock_ids': ','.join(chunk)}, timeout=120)
            payload = r.json()
            failed = payload.get('failed', [])
            if failed:
                print(f'  [batch] dividend server-side fetch failed for {failed} — '
                      f'absent from results, re-call for those ids')
            for sid, records in payload.get('data', {}).items():
                if not records:
                    continue
                df = pd.DataFrame(records)
                _save_fundamental_cache(
                    _fundamental_cache_path('twstock_dividend', sid), df)
                results[sid] = _dividend_slice(df, start, end).reset_index(drop=True)
        except Exception as e:
            print(f'  [batch] dividend chunk {i//50 + 1} error: {e}')

    return results


def _broker_day_cache_path(stock_id, date_str):
    """cache/twstock_broker_stock_<stock_id>/<date>.parquet"""
    d = _CACHE_DIR / f'twstock_broker_stock_{stock_id}'
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{date_str}.parquet'


def _make_date_chunks(dates, chunk_days=90):
    """Split a sorted date list into chunks, each spanning ≤ chunk_days calendar days."""
    if not dates:
        return []
    chunks = []
    start = dates[0]
    for i, d in enumerate(dates):
        if i == len(dates) - 1 or (dates[i + 1] - start).days >= chunk_days:
            chunks.append((start, d))
            if i < len(dates) - 1:
                start = dates[i + 1]
    return chunks


def _populate_broker_day_cache(stock_id, weekdays, headers,
                               chunk_days=90, rate_limit=270, period=300,
                               max_retries=5):
    """Ensure all weekdays have cached broker data. Uses 90-day range API chunks."""
    EMPTY_COLS = ['date', 'stock_id', 'broker_id', 'broker_name', 'price', 'buy', 'sell']
    missing = [d for d in weekdays if not _broker_day_cache_path(stock_id, d.isoformat()).exists()]
    if not missing:
        return

    chunks  = _make_date_chunks(missing, chunk_days)
    limiter = _RateLimiter(rate_limit, period)
    total   = len(chunks)

    for idx, (cs, ce) in enumerate(chunks):
        chunk_missing = [d for d in missing if cs <= d <= ce]
        for attempt in range(max_retries):
            try:
                limiter.acquire()
                r = _session().get(
                    f'{BASE}/studio/market/twstock/broker/stock/{stock_id}',
                    headers=headers,
                    params={'start': cs.isoformat(), 'end': ce.isoformat()},
                    timeout=120,
                )
                if r.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if r.status_code >= 500:
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                data    = r.json().get('data', [])
                df_all  = pd.DataFrame(data) if data else pd.DataFrame(columns=EMPTY_COLS)
                by_date = {}
                if not df_all.empty and 'date' in df_all.columns:
                    for date_str, grp in df_all.groupby('date'):
                        by_date[date_str] = grp
                for d in chunk_missing:
                    date_str = d.isoformat()
                    df_day   = by_date.get(date_str, pd.DataFrame(columns=EMPTY_COLS)).copy()
                    df_day['date'] = date_str
                    df_day.to_parquet(_broker_day_cache_path(stock_id, date_str),
                                      index=False, compression='snappy')
                print(f"  [broker_cache {stock_id}] chunk {idx+1}/{total} "
                      f"({cs.isoformat()}~{ce.isoformat()})", flush=True)
                break
            except requests.exceptions.Timeout:
                time.sleep(2 ** (attempt + 1))
            except Exception as e:
                print(f"  [broker_cache {stock_id}] error chunk {cs}~{ce}: {e}")
                break


def _populate_trader_day_cache(trader_id, weekdays, headers,
                               chunk_days=90, rate_limit=270, period=300,
                               max_retries=5):
    """Ensure all weekdays have cached trader data. Uses 90-day range API chunks."""
    EMPTY_COLS = ['date', 'broker_id', 'broker_name', 'stock_id', 'price', 'buy', 'sell']
    missing = [d for d in weekdays if not _trader_day_cache_path(trader_id, d.isoformat()).exists()]
    if not missing:
        return

    chunks  = _make_date_chunks(missing, chunk_days)
    limiter = _RateLimiter(rate_limit, period)
    total   = len(chunks)

    for idx, (cs, ce) in enumerate(chunks):
        chunk_missing = [d for d in missing if cs <= d <= ce]
        for attempt in range(max_retries):
            try:
                limiter.acquire()
                r = _session().get(
                    f'{BASE}/studio/market/twstock/broker/trader/{trader_id}',
                    headers=headers,
                    params={'start': cs.isoformat(), 'end': ce.isoformat()},
                    timeout=120,
                )
                if r.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if r.status_code >= 500:
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                data    = r.json().get('data', [])
                df_all  = pd.DataFrame(data) if data else pd.DataFrame(columns=EMPTY_COLS)
                by_date = {}
                if not df_all.empty and 'date' in df_all.columns:
                    for date_str, grp in df_all.groupby('date'):
                        by_date[date_str] = grp
                for d in chunk_missing:
                    date_str = d.isoformat()
                    df_day   = by_date.get(date_str, pd.DataFrame(columns=EMPTY_COLS)).copy()
                    df_day['date'] = date_str
                    df_day.to_parquet(_trader_day_cache_path(trader_id, date_str),
                                      index=False, compression='snappy')
                print(f"  [trader_cache {trader_id}] chunk {idx+1}/{total} "
                      f"({cs.isoformat()}~{ce.isoformat()})", flush=True)
                break
            except requests.exceptions.Timeout:
                time.sleep(2 ** (attempt + 1))
            except Exception as e:
                print(f"  [trader_cache {trader_id}] error chunk {cs}~{ce}: {e}")
                break


def fetch_twstock_broker_net(stock_id, broker_id, start, end, headers,
                             max_workers=10, rate_limit=270, period=300,
                             max_retries=5):
    """台股特定分點每日淨買賣超 (buy - sell 股數).

    broker_id: securities_trader_id, e.g. '9217' for 凱基-松山.
    Local cache: cache/twstock_broker_stock_<stock_id>/<YYYY-MM-DD>.parquet
    每天存全部分點完整資料，任何 broker_id 都可直接從 local cache 過濾，無需重抓。
    只 fetch 尚未 cache 的日期；concurrent requests + token bucket rate limit。
    """

    end_dt   = _date.fromisoformat(end)   if end   else _date.today()
    start_dt = _date.fromisoformat(start)

    weekdays = [start_dt + timedelta(days=i)
                for i in range((end_dt - start_dt).days + 1)
                if (start_dt + timedelta(days=i)).weekday() < 5]

    _populate_broker_day_cache(stock_id, weekdays, headers,
                               rate_limit=rate_limit, period=period, max_retries=max_retries)

    frames = []
    for d in weekdays:
        path = _broker_day_cache_path(stock_id, d.isoformat())
        if not path.exists():
            continue
        df_day = pd.read_parquet(path)
        row = df_day[df_day['broker_id'] == broker_id]
        if not row.empty:
            net = float(row['buy'].sum() - row['sell'].sum())
            frames.append({'date': d.isoformat(), 'net': net})

    if not frames:
        return pd.DataFrame(columns=['net'])
    result = pd.DataFrame(frames)
    result['date'] = pd.to_datetime(result['date'])
    return result.set_index('date').sort_index()


def fetch_twstock_all_broker_net(stock_id, start, end, headers,
                                  max_workers=10, rate_limit=270, period=300,
                                  max_retries=5):
    """台股每日全市場所有分點合計淨買賣超 (sum of buy - sell across ALL broker branches).

    Shares the same per-day parquet cache as fetch_twstock_broker_net.
    Returns pd.Series indexed by date (trading days only).
    """

    end_dt   = _date.fromisoformat(end)   if end   else _date.today()
    start_dt = _date.fromisoformat(start)

    weekdays = [start_dt + timedelta(days=i)
                for i in range((end_dt - start_dt).days + 1)
                if (start_dt + timedelta(days=i)).weekday() < 5]

    _populate_broker_day_cache(stock_id, weekdays, headers,
                               rate_limit=rate_limit, period=period, max_retries=max_retries)

    frames = []
    for d in weekdays:
        path = _broker_day_cache_path(stock_id, d.isoformat())
        if not path.exists():
            continue
        df_day = pd.read_parquet(path)
        net = float(df_day['buy'].sum() - df_day['sell'].sum()) if not df_day.empty else 0.0
        frames.append({'date': d.isoformat(), 'net': net})

    if not frames:
        return pd.Series(dtype=float, name='net')
    result = pd.DataFrame(frames)
    result['date'] = pd.to_datetime(result['date'])
    return result.set_index('date')['net'].sort_index()


def fetch_twstock_branch_daily_net(stock_id, start, end, headers,
                                    max_workers=10, rate_limit=270, period=300,
                                    max_retries=5):
    """台股每日各分點淨買賣超明細.

    Returns DataFrame shape (dates, broker_ids): each cell = daily net (buy - sell)
    for that branch on that date. Missing branch-day pairs are 0.
    Shares the same per-day parquet cache as fetch_twstock_broker_net.
    """

    end_dt   = _date.fromisoformat(end)   if end   else _date.today()
    start_dt = _date.fromisoformat(start)

    weekdays = [start_dt + timedelta(days=i)
                for i in range((end_dt - start_dt).days + 1)
                if (start_dt + timedelta(days=i)).weekday() < 5]

    _populate_broker_day_cache(stock_id, weekdays, headers,
                               rate_limit=rate_limit, period=period, max_retries=max_retries)

    frames = []
    for d in weekdays:
        path = _broker_day_cache_path(stock_id, d.isoformat())
        if not path.exists():
            continue
        df_day = pd.read_parquet(path)
        if df_day.empty:
            frames.append(pd.Series(dtype=float, name=d.isoformat()))
            continue
        df_day['_net'] = df_day['buy'] - df_day['sell']
        net_per_branch = df_day.groupby('broker_id')['_net'].sum()
        net_per_branch.name = d.isoformat()
        frames.append(net_per_branch)

    if not frames:
        return pd.DataFrame()
    result = pd.DataFrame(frames)   # shape: (dates × branches)
    result.index = pd.to_datetime(result.index)
    return result.sort_index().fillna(0.0)


# ── Taiwan fundamental data (quarterly / monthly) ────────────────────────────



def _fetch_twstock_fundamental_raw(endpoint, stock_id, headers=None):
    r = _retry_get(f'{BASE}/studio/market/twstock/{endpoint}/{stock_id}',
                   headers=headers, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()


def _fetch_fundamental(prefix, endpoint, stock_id, headers=None):
    path = _fundamental_cache_path(prefix, stock_id)
    df = _load_fundamental_cache(path)
    if df is not None:
        return df
    df = _fetch_twstock_fundamental_raw(endpoint, stock_id, headers)
    if not df.empty:
        _save_fundamental_cache(path, df)
    return df


def fetch_twstock_financials(stock_id, headers=None):
    """台股季頻綜合損益表 (long format). index=date, columns: type, value, origin_name.
    Key types: Revenue, GrossProfit, OperatingIncome, IncomeAfterTaxes, EPS.
    Pivot: df.pivot_table(index='date', columns='type', values='value', aggfunc='last')"""
    return _fetch_fundamental('twstock_fin', 'financials', stock_id, headers)


def fetch_twstock_balance_sheet(stock_id, headers=None):
    """台股季頻資產負債表 (long format). index=date, columns: type, value, origin_name.
    Key types: TotalAssets, Equity. ROE = IncomeAfterTaxes / Equity."""
    return _fetch_fundamental('twstock_bs', 'balance_sheet', stock_id, headers)


def fetch_twstock_monthly_revenue(stock_id, headers=None):
    """台股月營收. index=date, columns: revenue (NTD 元, full amount not thousands), revenue_month, revenue_year.
    YoY = (rev - rev_same_month_last_year) / abs(rev_same_month_last_year)."""
    return _fetch_fundamental('twstock_rev', 'monthly_revenue', stock_id, headers)


def _twstock_list_cache_path():
    return _CACHE_DIR / 'twstock_list.parquet'


def fetch_twstock_list(headers=None):
    """全市場股票清單（上市+上櫃，含 ETF）。DataFrame indexed by stock_id, columns:
    name, close, industry_code, listing_date (YYYY-MM-DD). Basic company data, not a
    time series — refreshed once a day: single-file cache like fundamentals (see
    references/cache.md), just 1-day TTL instead of 30-day.
    ETFs and other non-company securities have industry_code/listing_date = None/NaN
    (use .notna() to filter, not `is not None` — parquet round-trips None as NaN).
    industry_code is TWSE/TPEx's raw numeric 產業別 code (e.g. '24'=半導體業), not a
    decoded name — group/filter by it, don't assume a fixed label mapping."""
    path = _twstock_list_cache_path()
    df = _load_fundamental_cache(path, max_age_days=1)
    if df is not None:
        return df
    r = _retry_get(f'{BASE}/studio/market/twstock/list', headers=headers, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).set_index('stock_id')
    _save_fundamental_cache(path, df)
    return df


def fetch_twstock_info(stock_id, headers=None):
    """單支股票基本資料: {stock_id, name, close, industry_code, listing_date}, or None
    if not currently listed. Looks up within fetch_twstock_list's cached universe
    (same 1-day-fresh data) instead of a separate network call."""
    df = fetch_twstock_list(headers)
    if df.empty or stock_id not in df.index:
        return None
    return {'stock_id': stock_id, **df.loc[stock_id].to_dict()}


def fetch_twstock_market_value_all(headers=None, top=None):
    """全市場市值排名快照 (whole-market market-cap ranking). 上市 + 上櫃 + ETF
    (興櫃 excluded, ETNs have no data) — about 2,400 rows. DataFrame with columns
    rank (1-based, market_value desc), stock_id, name, market_value (NTD 元,
    integer); the as-of publication date rides along in `df.attrs['date']`
    ('YYYY-MM-DD'). Updated once a day after the close; server caches 30 min.

    `top` (int 1–3000) keeps the first N ranks, None = all. This is the first-layer
    screening filter for anything market-cap based (top-N pool, top-10 權值股) —
    never rebuild it from per-stock shares × price across the market. ETFs are in
    the ranking (ETFs such as 0050 rank among the large caps); drop ETFs with
    `df[~df['stock_id'].str.startswith('00')]`.

    Single-file cache like fetch_twmarket_dividend_points: the FULL ranking is
    fetched once (one call, ~2.4k rows) and kept 1 hour, `top` is sliced locally,
    so repeat calls with different `top` are free within the hour. attrs survive
    the parquet round-trip, so cache hits keep the as-of date."""
    if top is not None and (not isinstance(top, numbers.Integral)
                            or isinstance(top, bool) or not 1 <= top <= 3000):
        raise ValueError(f'top must be an int in 1–3000 or None, got {top!r}')
    path = _CACHE_DIR / 'twstock_market_value_all.parquet'
    df = _load_fundamental_cache(path, max_age_days=1 / 24)
    if df is None:
        r = _retry_get(f'{BASE}/studio/market/twstock/market_value/all',
                       headers=headers, timeout=60)
        payload = r.json()
        data = payload.get('data', [])
        if not data:
            out = pd.DataFrame(columns=['rank', 'stock_id', 'name', 'market_value'])
            out.attrs['date'] = payload.get('date')
            return out
        df = pd.DataFrame(data)[['rank', 'stock_id', 'name', 'market_value']]
        df = df.sort_values('rank').reset_index(drop=True)
        df.attrs['date'] = payload.get('date')
        _save_fundamental_cache(path, df)
    out = df if top is None else df.head(top).copy()
    out.attrs = dict(df.attrs)   # slicing must not drop the as-of date
    return out


def _fetch_fundamental_batch(prefix, endpoint, stock_ids, headers=None):
    """Batch fetch fundamental data. Returns dict {stock_id: DataFrame}.
    Uses cache first; fetches uncached stocks in chunks of 50 via batch API."""
    results = {}
    uncached = []

    for sid in stock_ids:
        path = _fundamental_cache_path(prefix, sid)
        df = _load_fundamental_cache(path)
        if df is not None:
            results[sid] = df
        else:
            uncached.append(sid)

    for i in range(0, len(uncached), 50):
        chunk = uncached[i:i + 50]
        try:
            r = _retry_get(f'{BASE}/studio/market/twstock/batch/{endpoint}',
                           headers=headers,
                           params={'stock_ids': ','.join(chunk)},
                           timeout=120)
            batch_data = r.json().get('data', {})
            for sid, records in batch_data.items():
                if not records:
                    continue
                df = pd.DataFrame(records)
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                _save_fundamental_cache(_fundamental_cache_path(prefix, sid), df)
                results[sid] = df
        except Exception as e:
            print(f'  [batch] {endpoint} chunk {i//50 + 1} error: {e}')

    return results


def fetch_twstock_financials_batch(stock_ids, headers=None):
    """Batch fetch 台股季頻綜合損益表. Returns dict {stock_id: DataFrame}."""
    return _fetch_fundamental_batch('twstock_fin', 'financials', stock_ids, headers)


def fetch_twstock_balance_sheet_batch(stock_ids, headers=None):
    """Batch fetch 台股季頻資產負債表. Returns dict {stock_id: DataFrame}."""
    return _fetch_fundamental_batch('twstock_bs', 'balance_sheet', stock_ids, headers)


def fetch_twstock_monthly_revenue_batch(stock_ids, headers=None):
    """Batch fetch 台股月營收. Returns dict {stock_id: DataFrame}."""
    return _fetch_fundamental_batch('twstock_rev', 'monthly_revenue', stock_ids, headers)




def fetch_twstock_shareholding_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股週頻股東人數. Returns dict {stock_id: DataFrame(shareholders)}."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        total = df[df['level'] == 'total'][['people']].rename(columns={'people': 'shareholders'}).astype(float)
        return total[~total.index.duplicated(keep='last')]
    return _fetch_twstock_cached_batch(
        'twstock_shareholding', 'shareholding', _fetch_twstock_shareholding_raw, _parse,
        stock_ids, start, end, headers)


def fetch_twstock_price_adj_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股向後調整日K. Returns dict {stock_id: DataFrame(Open, Close)}."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()[['open', 'close']].rename(
            columns={'open': 'Open', 'close': 'Close'}).astype(float)
        return df.replace(0, float('nan')).ffill()
    return _fetch_twstock_cached_batch(
        'twstock_price', 'price_adj', _fetch_twstock_price_raw, _parse,
        stock_ids, start, end, headers)


def fetch_twstock_price_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股原始日K OHLCV（未除權息）. Returns dict {stock_id: DataFrame(Open,
    High, Low, Close, Volume)}. Same data and cache as fetch_twstock_price — use for
    High/Low-based screens (KD, breakout, range) across many stocks; do NOT use for
    backtesting across ex-dividend dates (use fetch_twstock_price_adj_batch)."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        df = df.set_index('date').sort_index()[cols].rename(
            columns={'open': 'Open', 'high': 'High', 'low': 'Low',
                     'close': 'Close', 'volume': 'Volume'}).astype(float)
        return df.replace(0, float('nan')).ffill()
    results = _fetch_twstock_cached_batch(
        'twstock_price_nonadj', 'price', _fetch_twstock_price_nonadj_raw, _parse,
        stock_ids, start, end, headers)
    return {sid: _sanity_check_ohlc(df, f'{sid} twstock price')
            for sid, df in results.items()}


def fetch_twstock_per_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股每日本益比/股價淨值比/殖利率. Returns dict {stock_id:
    DataFrame(dividend_yield, PER, PBR)}. Same data and cache as fetch_twstock_per —
    use for value screens (殖利率 > x%, PER < y) across many stocks."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        return df.set_index('date').sort_index()
    return _fetch_twstock_cached_batch(
        'twstock_per', 'per', _fetch_twstock_per_raw, _parse,
        stock_ids, start, end, headers)


def fetch_twstock_institutional_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股三大法人. Returns dict {stock_id: DataFrame(foreign_net, ...)}."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df['foreign_net'] = df['foreign_buy'] - df['foreign_sell']
        return df.fillna(0)
    return _fetch_twstock_cached_batch(
        'twstock_inst', 'institutional', _fetch_twstock_inst_raw, _parse,
        stock_ids, start, end, headers)


def _fetch_twstock_foreign_shareholding_raw(stock_id, start, end, headers=None):
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    r = _retry_get(f'{BASE}/studio/market/twstock/foreign_shareholding/{stock_id}',
                   headers=headers, params={'start': start, 'end': end_str}, timeout=60)
    data = r.json().get('data', [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date').sort_index()


def fetch_twstock_foreign_shareholding_batch(stock_ids, start, end, headers=None):
    """Batch fetch 台股外資持股表. Returns dict {stock_id: DataFrame}.
    Key columns: ForeignInvestmentSharesRatio (持股比率%), ForeignInvestmentShares (持股股數),
    ForeignInvestmentRemainRatio (剩餘可投資比率%), NumberOfSharesIssued (已發行股數)."""
    def _parse(records):
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        return df.set_index('date').sort_index()
    return _fetch_twstock_cached_batch(
        'twstock_foreign_sh', 'foreign_shareholding', _fetch_twstock_foreign_shareholding_raw, _parse,
        stock_ids, start, end, headers)
def _trader_day_cache_path(trader_id, date_str):
    """cache/twstock_broker_trader_<trader_id>/<date>.parquet"""
    d = _CACHE_DIR / f'twstock_broker_trader_{trader_id}'
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{date_str}.parquet'


def fetch_twstock_trader_flows(trader_id, start, end, headers,
                               rate_limit=270, period=300, max_retries=5, **_kwargs):
    """分點對所有股票每日淨買賣超 (buy - sell).

    trader_id: securities_trader_id, e.g. '9217' for 凱基-松山.
    Local cache: cache/twstock_broker_trader_<trader_id>/<YYYY-MM-DD>.parquet
    每天存全部股票完整資料，只 fetch 沒有的日期（90-day range chunks）。
    Returns long-format DataFrame indexed by (date, stock_id) with 'net' column.
    """

    end_dt   = _date.fromisoformat(end)   if end   else _date.today()
    start_dt = _date.fromisoformat(start)

    weekdays = [start_dt + timedelta(days=i)
                for i in range((end_dt - start_dt).days + 1)
                if (start_dt + timedelta(days=i)).weekday() < 5]

    _populate_trader_day_cache(trader_id, weekdays, headers,
                               rate_limit=rate_limit, period=period, max_retries=max_retries)

    frames = []
    for d in weekdays:
        path = _trader_day_cache_path(trader_id, d.isoformat())
        if not path.exists():
            continue
        df_day = pd.read_parquet(path)
        if df_day.empty or 'stock_id' not in df_day.columns:
            continue
        df_day = df_day.copy()
        df_day['net'] = df_day['buy'].astype(float) - df_day['sell'].astype(float)
        df_day['date'] = pd.to_datetime(d.isoformat())
        frames.append(df_day[['date', 'stock_id', 'net']])

    if not frames:
        return pd.DataFrame(columns=['date', 'stock_id', 'net']).set_index(['date', 'stock_id'])
    result = pd.concat(frames, ignore_index=True)
    return result.groupby(['date', 'stock_id'])['net'].sum().to_frame()
