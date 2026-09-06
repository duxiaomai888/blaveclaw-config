"""
live_eval core blave_client — Blave API 客户端(7 维 alpha + funding + liquidation)

提供:
  - HDRS: Blave API headers (从 .env 读 api-key / secret-key)
  - _get(): 带 retry 的 GET 请求
  - fetch_kline(symbol, period, start, end) -> pd.DataFrame
  - fetch_alpha_series(endpoint, params) -> (pd.Series, list[ts])
  - fetch_alpha_table() -> dict
  - fetch_liquidation_map(symbol) -> dict (含 labels, oi_value, cumsum, price)
  - fetch_7tier_kline(symbol, end_date_str) -> dict {tier_name: DataFrame}
  - fetch_7dim_alpha(symbol, end_date_str) -> dict
"""
import time
import requests
import pandas as pd
import numpy as np

from .config import BLAVE_BASE_URL, load_blave_keys

HDRS = load_blave_keys()


def _get(url, params, retries=3):
    """GET with exponential backoff retry. Raises on final failure."""
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HDRS, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if i < retries - 1:
                time.sleep(2 ** i)
    raise last_err  # type: ignore[misc]


def fetch_kline(symbol, period, start_date, end_date):
    """Fetch K-line bars. Returns DataFrame indexed by UTC time."""
    j = _get(f'{BLAVE_BASE_URL}/kline', {
        'symbol': symbol, 'period': period,
        'start_date': start_date, 'end_date': end_date,
    })
    if not isinstance(j, list):
        j = j.get('data', [])
    if not j:
        return pd.DataFrame()
    df = pd.DataFrame(j)
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    return df.set_index('time').sort_index()


def fetch_alpha_series(endpoint, params):
    """Fetch alpha series. Returns (Series indexed by UTC time, list of timestamps)."""
    j = _get(f'{BLAVE_BASE_URL}{endpoint}', params=params)
    d = j.get('data', {}) if isinstance(j, dict) else {}
    a, t = d.get('alpha', []), d.get('timestamp', [])
    if not a or not t:
        return pd.Series(dtype=float), []
    return pd.Series(a, index=pd.to_datetime(t, unit='s', utc=True)).sort_index(), t


def fetch_alpha_table():
    """Fetch alpha_table (cross-symbol snapshot incl. funding rate)."""
    j = _get(f'{BLAVE_BASE_URL}/alpha_table', {})
    return j.get('data', {})


def fetch_liquidation_map(symbol):
    """Fetch current liquidation heatmap.

    Returns dict with:
      - labels:    200 price levels
      - oi_value:  200 USD amounts per level
      - cumsum:    200 cumulative USD from current price outward
      - price:     current price
    """
    j = _get(f'{BLAVE_BASE_URL}/liquidation/get_map', {'symbol': symbol})
    return j.get('data', {})


# ── 7 档时间框架定义(方案 A: 7d/1d/4h/2h/1h/30m/15m) ──
TIERS = {
    '7d':  {'period': '1d',    'bars_back': 7,  'unit': 'd'},
    '1d':  {'period': '1h',    'bars_back': 24, 'unit': 'h'},
    '4h':  {'period': '1h',    'bars_back': 4,  'unit': 'h'},
    '2h':  {'period': '1h',    'bars_back': 2,  'unit': 'h'},
    '1h':  {'period': '1h',    'bars_back': 1,  'unit': 'h'},
    '30m': {'period': '15min', 'bars_back': 2,  'unit': 'm'},
    '15m': {'period': '15min', 'bars_back': 1,  'unit': 'm'},
}


def fetch_7tier_kline(symbol, end_date_str):
    """Fetch K-line for all 7 tiers. Returns dict {tier_name: DataFrame}."""
    result = {}
    for tier_name, cfg in TIERS.items():
        if cfg['unit'] == 'd':
            start = (pd.Timestamp(end_date_str) - pd.Timedelta(days=cfg['bars_back']+2)).strftime('%Y-%m-%d')
        elif cfg['unit'] == 'h':
            start = (pd.Timestamp(end_date_str) - pd.Timedelta(hours=cfg['bars_back']+4)).strftime('%Y-%m-%d')
        else:
            start = end_date_str
        try:
            result[tier_name] = fetch_kline(symbol, cfg['period'], start, end_date_str)
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f'  [warn] {tier_name} fetch failed: {e}')
            result[tier_name] = pd.DataFrame()
    return result


def fetch_7dim_alpha(symbol, end_date_str, start_date_7d='2026-06-01', start_date_3d='2026-06-12'):
    """Fetch 7-dim alpha across multiple timeframes.

    Returns dict:
      {
        'MD': pd.Series,
        'TI': {'24h': Series, '4h': Series, '1h': Series, '15min': Series},
        'WH': {'24h': Series, ...},
        'LIQ': {'24h': Series, ...},
        'HC_1d': pd.Series,
        'HC_1h': pd.Series,
        'MS': pd.Series,
        'TTE': pd.Series,
        'SQUEEZE': pd.Series,
      }
    """
    result = {}
    # 1. Market Direction (1d only)
    try:
        a, _ = fetch_alpha_series('/market_direction/get_alpha',
                                   {'period': '1d', 'start_date': start_date_7d, 'end_date': end_date_str})
        result['MD'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['MD'] = pd.Series(dtype=float)
    # 2-4. TI / WH / LIQ (multi-TF: 24h/4h/1h/15min)
    for dim_name, endpoint in [('TI', '/taker_intensity/get_alpha'),
                                ('WH', '/whale_hunter/get_alpha'),
                                ('LIQ', '/liquidation/get_alpha')]:
        result[dim_name] = {}
        for tf in ['24h', '4h', '1h', '15min']:
            try:
                a, _ = fetch_alpha_series(endpoint, {
                    'symbol': symbol, 'period': '1d', 'timeframe': tf,
                    'start_date': start_date_7d, 'end_date': end_date_str,
                })
                result[dim_name][tf] = a
            except (requests.RequestException, ValueError, KeyError):
                result[dim_name][tf] = pd.Series(dtype=float)
    # 5. HC (1d + 1h)
    try:
        a, _ = fetch_alpha_series('/holder_concentration/get_alpha',
                                   {'symbol': symbol, 'period': '1d',
                                    'start_date': start_date_7d, 'end_date': end_date_str})
        result['HC_1d'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['HC_1d'] = pd.Series(dtype=float)
    try:
        a, _ = fetch_alpha_series('/holder_concentration/get_alpha',
                                   {'symbol': symbol, 'period': '1h',
                                    'start_date': start_date_3d, 'end_date': end_date_str})
        result['HC_1h'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['HC_1h'] = pd.Series(dtype=float)
    # 6. MS (1d only)
    try:
        a, _ = fetch_alpha_series('/market_sentiment/get_alpha',
                                   {'symbol': symbol, 'period': '1d',
                                    'start_date': start_date_7d, 'end_date': end_date_str})
        result['MS'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['MS'] = pd.Series(dtype=float)
    # 7. TTE (1d only)
    try:
        a, _ = fetch_alpha_series('/blave_top_trader/get_exposure',
                                   {'period': '1d',
                                    'start_date': start_date_7d, 'end_date': end_date_str})
        result['TTE'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['TTE'] = pd.Series(dtype=float)
    # 8. Squeeze
    try:
        a, _ = fetch_alpha_series('/squeeze_momentum/get_alpha',
                                   {'symbol': symbol,
                                    'start_date': start_date_7d, 'end_date': end_date_str})
        result['SQUEEZE'] = a
    except (requests.RequestException, ValueError, KeyError):
        result['SQUEEZE'] = pd.Series(dtype=float)
    return result


__all__ = [
    'HDRS', '_get', 'fetch_kline', 'fetch_alpha_series', 'fetch_alpha_table',
    'fetch_liquidation_map', 'TIERS', 'fetch_7tier_kline', 'fetch_7dim_alpha',
]
