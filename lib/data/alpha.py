"""Blave alpha series + /studio/market/db 期货 + 经济日历。

拆分自原 lib/data.py。fetch_economic_calendar 不是 alpha 序列,但同为
Blave studio 端点,放这里避免再开一个单函数模块。
"""
from datetime import datetime, timedelta

import pandas as pd
import requests

from .http import BASE, _default_headers, _retry_get, _session
from ._shared import _sanity_check_ohlc
from .cache import _extend_cache_monthly

__all__ = [
    '_fetch_alpha_raw', '_fetch_alpha',
    'fetch_holder_concentration', 'fetch_funding_rate', 'fetch_taker_intensity',
    'fetch_whale_hunter', 'fetch_unusual_movement', 'fetch_squeeze_momentum',
    'fetch_liquidation', 'fetch_market_direction', 'fetch_capital_shortage',
    'fetch_market_sentiment', 'fetch_top_trader_exposure',
    '_DB_CHUNK_DAYS', '_fetch_db_raw', 'settlement_signals_from_db', 'fetch_db_kline',
    'fetch_economic_calendar',
]


def _fetch_alpha_raw(endpoint, params, headers, start, end):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    s = datetime.strptime(start, '%Y-%m-%d')
    e = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    chunks, cursor = [], s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=365), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        try:
            r = _retry_get(f'{BASE}/{endpoint}', headers=headers, params={
                **params, 'start_date': cs, 'end_date': ce,
            }, timeout=60)
        except requests.HTTPError as exc:
            resp = exc.response
            raise RuntimeError(
                f'/{endpoint} HTTP {resp.status_code}: {resp.text[:200]}'
            ) from exc
        data = r.json().get('data', {})
        return data.get('timestamp', []), data.get('alpha', [])

    ts_list, alpha_list = [], []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_one, cs, ce): (cs, ce) for cs, ce in chunks}
        for future in as_completed(futures):
            ts, alpha = future.result()
            ts_list.extend(ts)
            alpha_list.extend(alpha)

    df = pd.DataFrame({
        'time':  pd.to_datetime(ts_list, unit='s', utc=True),
        'alpha': pd.to_numeric(alpha_list, errors='coerce'),
    }).set_index('time').sort_index()
    return df[~df.index.duplicated(keep='first')]


def _fetch_alpha(endpoint, params, headers, start, end):
    if headers is None or 'api-key' not in headers:
        h = _default_headers() or {'api-key': '', 'secret-key': ''}
    else:
        h = headers
    slug = endpoint.split('/')[0]
    return _extend_cache_monthly(
        slug, params,
        lambda s, e: _fetch_alpha_raw(endpoint, params, h, s, e),
        start, end,
    )


def fetch_holder_concentration(symbol, interval, start, end, headers=None):
    """籌碼集中度 Holder Concentration. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('holder_concentration/get_alpha',
                        {'symbol': symbol, 'period': interval}, headers, start, end)


def fetch_funding_rate(symbol, interval, start, end, headers=None):
    """資金費率 Funding Rate (Binance). Returns DataFrame with 'alpha' column (alpha = funding rate × 100)."""
    return _fetch_alpha('funding_rate/get_alpha',
                        {'symbol': symbol, 'period': interval}, headers, start, end)


def fetch_taker_intensity(symbol, interval, start, end, headers=None, timeframe='24h'):
    """多空力道 Taker Intensity. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('taker_intensity/get_alpha',
                        {'symbol': symbol, 'period': interval, 'timeframe': timeframe},
                        headers, start, end)


def fetch_whale_hunter(symbol, interval, start, end, headers=None, timeframe='24h', score_type='score_oi'):
    """巨鯨警報 Whale Hunter. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('whale_hunter/get_alpha',
                        {'symbol': symbol, 'period': interval,
                         'timeframe': timeframe, 'score_type': score_type},
                        headers, start, end)


def fetch_unusual_movement(symbol, interval, start, end, headers=None, timeframe='24h'):
    """異常漲跌 Unusual Movement. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('unusual_movement/get_alpha',
                        {'symbol': symbol, 'period': interval, 'timeframe': timeframe},
                        headers, start, end)


def fetch_squeeze_momentum(symbol, start, end, headers=None):
    """擠壓動能 Squeeze Momentum (period fixed to 1d). Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('squeeze_momentum/get_alpha',
                        {'symbol': symbol, 'period': '1d'}, headers, start, end)


def fetch_liquidation(symbol, interval, start, end, headers=None, timeframe='24h'):
    """爆倉指標 Liquidation. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('liquidation/get_alpha',
                        {'symbol': symbol, 'period': interval, 'timeframe': timeframe},
                        headers, start, end)


def fetch_market_direction(interval, start, end, headers=None):
    """市場方向 Market Direction (BTC only, no symbol). Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('market_direction/get_alpha',
                        {'period': interval}, headers, start, end)


def fetch_capital_shortage(interval, start, end, headers=None):
    """資金稀缺 Capital Shortage (market-wide, no symbol). Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('capital_shortage/get_alpha',
                        {'period': interval}, headers, start, end)


def fetch_market_sentiment(symbol, interval, start, end, headers=None):
    """市場情緒 Market Sentiment. Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('market_sentiment/get_alpha',
                        {'symbol': symbol, 'period': interval}, headers, start, end)


def fetch_top_trader_exposure(interval, start, end, headers=None):
    """Blave頂尖交易員曝險 Top Trader Exposure (BTC only, no symbol). Returns DataFrame with 'alpha' column."""
    return _fetch_alpha('blave_top_trader/get_exposure',
                        {'period': interval}, headers, start, end)


# ── CME / NYMEX / ICE futures (via /studio/market/db) ────────────────────────

_DB_CHUNK_DAYS = {'ohlcv-1m': 28, 'ohlcv-1h': 365, 'ohlcv-1d': 3650}


def _fetch_db_raw(dataset, symbol, schema, start, end, headers=None):
    """Fetch OHLCV — chunks fetched concurrently, chunk size by schema."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    s    = datetime.strptime(start, '%Y-%m-%d')
    e    = datetime.utcnow() if not end else datetime.strptime(end, '%Y-%m-%d')
    days = _DB_CHUNK_DAYS.get(schema, 30)

    chunks, cursor = [], s
    while cursor < e:
        chunk_end = min(cursor + timedelta(days=days), e)
        chunks.append((cursor.strftime('%Y-%m-%d'), chunk_end.strftime('%Y-%m-%d')))
        cursor = chunk_end

    def _fetch_one(cs, ce):
        import time as _time
        for attempt in range(3):
            try:
                r = _session().get(
                    f'{BASE}/studio/market/db/ohlcv/{dataset}/{symbol}/{schema}',
                    headers=headers,
                    params={'start': cs, 'end': ce},
                    timeout=120,
                )
                r.raise_for_status()
                return r.json().get('data', [])
            except Exception:
                if attempt == 2:
                    raise
                _time.sleep(2 ** attempt)

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
    ohlcv = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
    if 'instrument_id' in df.columns:
        ohlcv['instrument_id'] = df['instrument_id'].values
    return ohlcv


def settlement_signals_from_db(df, signal):
    """Force signal=0.0 on last bar before each instrument_id rollover (contract expiry).

    Returns (signal, exec_at_close) where exec_at_close is a bool Series marking
    settlement bars — those bars execute at this-bar close, not next-bar open.
    If instrument_id column is absent, exec_at_close is all-False.
    """
    import pandas as pd
    exec_at_close = pd.Series(False, index=df.index)
    if 'instrument_id' not in df.columns:
        return signal, exec_at_close
    changes = (df['instrument_id'] != df['instrument_id'].shift(1)).values
    for i, changed in enumerate(changes):
        if changed and i > 0:
            signal.iloc[i - 1]       = 0.0
            exec_at_close.iloc[i - 1] = True
    return signal, exec_at_close


def fetch_db_kline(dataset, symbol, schema, start, end, headers=None):
    """Fetch CME/NYMEX/ICE OHLCV with local cache."""
    slug = schema.replace('-', '')
    df = _extend_cache_monthly(
        f'db_{slug}', {'dataset': dataset.replace('.', ''), 'symbol': symbol},
        lambda s, e: _fetch_db_raw(dataset, symbol, schema, s, e, headers),
        start, end,
    )
    return _sanity_check_ohlc(df, f'{symbol} {schema} db_kline')


# ── 總經行事曆(studio 端点,非 alpha 序列) ────────────────────────────────────

def fetch_economic_calendar(headers, start=None, end=None, countries=None,
                            max_priority=None, limit=None, lang='zh'):
    """總經事件行事曆（授權資料源）—— 事件時間、市場預期值、前值、實際值。

    **這是總經事件與其數字的唯一來源。** 任何「本週有什麼重要事件」「這個數據預期多少 /
    前值多少」的問題都用這支，不要自己上網搜、更不要憑記憶寫數字：實測 agent 自行搜尋會
    整張抄到內容農場的錯誤表格（把已公布的實際值當成預期值），沒抄到的欄位再用訓練資料
    填空，連「前值」這種唯一解的數字都會寫錯，也會把 A 指標的數字安到 B 指標上。
    查不到的欄位就說查不到，不要填。

    資料只涵蓋前後約五週（滾動窗口，非歷史庫）；超出範圍的區間回空 DataFrame，不是錯誤。

    start / end: 'YYYY-MM-DD' 台北日期，含頭含尾（省略 = 不限）。
    countries:   ISO 兩碼 list，如 ['US', 'CN', 'TW']（省略 = 全部）。
    max_priority: 只回 priority <= 此值。**priority 1 最重要、3 最不重要**（1 是非農、
                 利率決議這種級別）—— 所以「只要最重要的事件」是 max_priority=1，不是 3。
    limit:       筆數上限（依事件時間排序後截斷）。
    lang:        指標與國名的顯示語言，'zh'（預設）或 'en'。伺服器只換掉對照表裡有的
                 名稱，沒收錄的維持原本的中文，所以 'en' 會拿到中英混雜的結果。

    Returns DataFrame sorted by event time, one row per event:
      datetime      台北時間（time 為 null 的事件用當日 00:00）
      date / time   台北日期、'HH:MM'（部分事件沒有公布時間，time 為 None）
      country       ISO 兩碼 / country_name 中文國名
      subject       指標名稱；subject_title 是期別，如 '<7月>'、'<2季>'
      predict       市場預期（consensus）；未提供為 None
      last          前值
      real          實際值；尚未公布為 None
      unit          單位（'%'、'point'、'億USD' …）
      priority      1~3，1 最重要
    """
    params = {'lang': lang}
    if start:
        params['start'] = start
    if end:
        params['end'] = end
    if countries:
        params['country'] = ','.join(countries)
    if max_priority is not None:
        params['max_priority'] = max_priority
    if limit is not None:
        params['limit'] = limit

    r = _retry_get(f'{BASE}/studio/market/anue/economic_calendar',
                   headers=headers, params=params, timeout=60)
    payload = r.json()
    data = payload.get('data', []) if isinstance(payload, dict) else payload
    if not data:
        return pd.DataFrame(columns=['datetime', 'date', 'time', 'country', 'country_name',
                                     'subject_title', 'subject', 'predict', 'last', 'real',
                                     'unit', 'priority'])

    df = pd.DataFrame(data).rename(columns={
        'countryId': 'country', 'countryName': 'country_name', 'subjectTitle': 'subject_title',
    })
    # startDate 是事件當天（台北日期）的 epoch 秒；time 是台北時間的 'HH:MM'
    df['date'] = pd.to_datetime(df['startDate'], unit='s').dt.normalize()
    df['datetime'] = df['date'] + pd.to_timedelta(
        df['time'].fillna('00:00') + ':00'
    )
    # Re-apply the filters locally: this workspace and the API deploy on separate
    # schedules, so an older server silently ignores the query params and hands
    # back all ~1,400 rows. Filtering here keeps the contract true either way.
    if start:
        df = df[df['date'] >= pd.Timestamp(start)]
    if end:
        df = df[df['date'] <= pd.Timestamp(end)]
    if countries:
        wanted = {c.upper() for c in countries}
        df = df[df['country'].str.upper().isin(wanted)]
    if max_priority is not None:
        df = df[df['priority'] <= max_priority]  # 上游 1 最重要、3 最不重要

    cols = ['datetime', 'date', 'time', 'country', 'country_name', 'subject_title',
            'subject', 'predict', 'last', 'real', 'unit', 'priority']
    df = df[[c for c in cols if c in df.columns]].sort_values('datetime').reset_index(drop=True)
    return df.head(limit) if limit else df
