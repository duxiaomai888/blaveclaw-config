"""
live_eval core binance_client — Binance 公共 API 客户端(OHLCV + trades + 衍生品)

免 key,Binance 公共 API + 合约 API + 大户持仓 API

提供:
  - fetch_klines(symbol, interval, limit) -> pd.DataFrame (含 OHLCV + taker buy/sell)
  - fetch_agg_trades(symbol, limit) -> pd.DataFrame (逐笔成交,用于 Volume Profile)
  - fetch_funding_rate(symbol) -> float (最新 funding rate)
  - fetch_open_interest(symbol) -> float (持仓量)
  - fetch_long_short_ratio(symbol, period) -> pd.DataFrame (多空持仓人数比)
  - fetch_top_trader_ratio(symbol, period) -> pd.DataFrame (大户持仓多空比)
"""
import time
import requests
import pandas as pd
import numpy as np

from .config import BINANCE_BASE_URL, BINANCE_FAPI_BASE_URL, BINANCE_DATA_BASE_URL

# 默认重试 / timeout
_TIMEOUT = 15
_RETRIES = 3


def _get(url, params=None, base_url=None):
    """GET with exponential backoff. Returns JSON or raises."""
    last_err = None
    for i in range(_RETRIES):
        try:
            r = requests.get(url, params=params or {}, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if i < _RETRIES - 1:
                time.sleep(2 ** i)
    raise last_err  # type: ignore[misc]


# ── 现货 K线(免 key) ──

# 常用 interval 映射
INTERVAL_MAP = {
    '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '8h': '8h', '12h': '12h',
    '1d': '1d', '3d': '3d', '1w': '1w', '1M': '1M',
}


def fetch_klines(symbol='BTCUSDT', interval='1d', limit=500, start_time=None, end_time=None):
    """Fetch OHLCV klines from Binance Spot.

    Returns DataFrame with columns: open, high, low, close, volume,
      quote_volume, trades, taker_buy_base_volume, taker_buy_quote_volume
    Indexed by UTC datetime.
    """
    params = {'symbol': symbol, 'interval': interval, 'limit': min(limit, 1000)}
    if start_time is not None:
        params['startTime'] = start_time
    if end_time is not None:
        params['endTime'] = end_time
    raw = _get(f'{BINANCE_BASE_URL}/api/v3/klines', params=params)
    if not raw:
        return pd.DataFrame()
    # Binance kline format:
    # [openTime, open, high, low, close, volume, closeTime, quoteVolume, trades,
    #  takerBuyBaseVolume, takerBuyQuoteVolume, ignore]
    df = pd.DataFrame(raw, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
        'quote_volume', 'trades', 'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
    ])
    df = df.drop(columns=['ignore'])
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume',
                 'taker_buy_base_volume', 'taker_buy_quote_volume']:
        df[col] = df[col].astype(float)
    df['trades'] = df['trades'].astype(int)
    df.index = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    return df.drop(columns=['open_time', 'close_time'])


def fetch_agg_trades(symbol='BTCUSDT', limit=1000, start_time=None, end_time=None):
    """Fetch aggregated trades from Binance Spot.

    Returns DataFrame with columns: price, qty, quote_qty, time, is_buyer_maker
    Indexed by UTC datetime.

    Use for Volume Profile: group by price level to get volume distribution.
    """
    params = {'symbol': symbol, 'limit': min(limit, 1000)}
    if start_time is not None:
        params['startTime'] = start_time
    if end_time is not None:
        params['endTime'] = end_time
    raw = _get(f'{BINANCE_BASE_URL}/api/v3/aggTrades', params=params)
    if not raw:
        return pd.DataFrame()
    # aggTrade format:
    # {a: aggTradeId, p: price, q: qty, f: firstTradeId, l: lastTradeId,
    #  T: time, m: isBuyerMaker, M: ignore}
    df = pd.DataFrame(raw)
    df = df.rename(columns={'p': 'price', 'q': 'qty', 'T': 'time', 'm': 'is_buyer_maker'})
    df['price'] = df['price'].astype(float)
    df['qty'] = df['qty'].astype(float)
    df['quote_qty'] = df['price'] * df['qty']
    df['is_buyer_maker'] = df['is_buyer_maker'].astype(bool)
    df.index = pd.to_datetime(df['time'], unit='ms', utc=True)
    return df[['price', 'qty', 'quote_qty', 'is_buyer_maker']]


# ── 合约 API(fapi) ──

def fetch_funding_rate(symbol='BTCUSDT'):
    """Fetch latest funding rate from Binance Futures."""
    raw = _get(f'{BINANCE_FAPI_BASE_URL}/fapi/v1/premiumIndex', {'symbol': symbol})
    if not raw:
        return None
    return {
        'symbol': raw.get('symbol'),
        'mark_price': float(raw.get('markPrice', 0)),
        'index_price': float(raw.get('indexPrice', 0)),
        'last_funding_rate': float(raw.get('lastFundingRate', 0)),
        'next_funding_time': pd.to_datetime(raw.get('nextFundingTime'), unit='ms', utc=True) if raw.get('nextFundingTime') else None,
        'time': pd.to_datetime(raw.get('time'), unit='ms', utc=True) if raw.get('time') else None,
    }


def fetch_open_interest(symbol='BTCUSDT'):
    """Fetch current open interest from Binance Futures."""
    raw = _get(f'{BINANCE_FAPI_BASE_URL}/fapi/v1/openInterest', {'symbol': symbol})
    if not raw:
        return None
    return {
        'symbol': raw.get('symbol'),
        'open_interest': float(raw.get('openInterest', 0)),
        'time': pd.to_datetime(raw.get('time'), unit='ms', utc=True) if raw.get('time') else None,
    }


def fetch_long_short_ratio(symbol='BTCUSDT', period='5m', limit=500):
    """Fetch global long/short account ratio from Binance Futures.

    period: '5m','15m','30m','1h','2h','4h','6h','12h','1d'
    """
    params = {'symbol': symbol, 'period': period, 'limit': min(limit, 500)}
    raw = _get(f'{BINANCE_FAPI_BASE_URL}/futures/data/globalLongShortAccountRatio', params=params)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    df['longShortRatio'] = df['longShortRatio'].astype(float)
    df['longAccount'] = df['longAccount'].astype(float)
    df['shortAccount'] = df['shortAccount'].astype(float)
    df.index = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df[['longShortRatio', 'longAccount', 'shortAccount']]


def fetch_top_trader_ratio(symbol='BTCUSDT', period='5m', limit=500):
    """Fetch top trader position ratio (long %) from Binance Futures.

    Returns pd.DataFrame with columns: longShortRatio, longAccount, shortAccount, timestamp
    """
    params = {'symbol': symbol, 'period': period, 'limit': min(limit, 500)}
    raw = _get(f'{BINANCE_DATA_BASE_URL}/futures/data/topLongShortPositionRatio', params=params)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    df['longShortRatio'] = df['longShortRatio'].astype(float)
    df['longAccount'] = df['longAccount'].astype(float)
    df['shortAccount'] = df['shortAccount'].astype(float)
    df.index = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df[['longShortRatio', 'longAccount', 'shortAccount']]


# ── 24h ticker(免 key) ──

def fetch_ticker_24h(symbol='BTCUSDT'):
    """Fetch 24h ticker stats from Binance Spot."""
    raw = _get(f'{BINANCE_BASE_URL}/api/v3/ticker/24hr', {'symbol': symbol})
    if not raw:
        return None
    return {
        'symbol': raw.get('symbol'),
        'last_price': float(raw.get('lastPrice', 0)),
        'change_pct': float(raw.get('priceChangePercent', 0)),
        'high_24h': float(raw.get('highPrice', 0)),
        'low_24h': float(raw.get('lowPrice', 0)),
        'volume_24h': float(raw.get('volume', 0)),
        'quote_volume_24h': float(raw.get('quoteVolume', 0)),
        'trades_24h': int(raw.get('count', 0)),
    }


__all__ = [
    'INTERVAL_MAP',
    'fetch_klines', 'fetch_agg_trades',
    'fetch_funding_rate', 'fetch_open_interest',
    'fetch_long_short_ratio', 'fetch_top_trader_ratio',
    'fetch_ticker_24h',
]
