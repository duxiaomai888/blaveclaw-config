"""
live_eval core indicators — 量价指标计算

提供:
  - calc_obv(kline) -> pd.Series       # On Balance Volume
  - calc_ad_line(kline) -> pd.Series   # Accumulation/Distribution Line
  - calc_taker_buy_ratio(kline) -> pd.Series  # 主动买/卖比 (%)
  - calc_vwap(kline) -> pd.Series      # 成交量加权均价
  - calc_volume_profile_kde(kline) -> dict  # 价内成交量分布(简化 VP)
"""
import numpy as np
import pandas as pd


def calc_obv(kline):
    """On Balance Volume.

    OBV rises when close > prev close (volume added)
    OBV falls when close < prev close (volume subtracted)
    OBV flat when close == prev close

    Returns pd.Series indexed same as kline.
    """
    if kline is None or len(kline) < 2 or 'close' not in kline or 'volume' not in kline:
        return pd.Series(dtype=float)

    close = kline['close'].values
    volume = kline['volume'].values

    obv = np.zeros(len(close))
    obv[0] = volume[0]

    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            obv[i] = obv[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            obv[i] = obv[i - 1] - volume[i]
        else:
            obv[i] = obv[i - 1]

    return pd.Series(obv, index=kline.index, name='OBV')


def calc_ad_line(kline):
    """Accumulation/Distribution Line.

    MFM = ((close - low) - (high - close)) / (high - low)
    A/D = cumsum(MFM * volume)

    Returns pd.Series.
    """
    if kline is None or len(kline) < 2 or 'close' not in kline:
        return pd.Series(dtype=float)
    if 'high' not in kline or 'low' not in kline or 'volume' not in kline:
        return pd.Series(dtype=float)

    high = kline['high'].values
    low = kline['low'].values
    close = kline['close'].values
    volume = kline['volume'].values

    # Money Flow Multiplier
    hl_range = high - low
    mfm = np.where(hl_range > 0,
                    ((close - low) - (high - close)) / hl_range,
                    0.0)
    # Money Flow Volume
    mfv = mfm * volume
    # A/D Line (cumulative)
    ad = np.cumsum(mfv)

    return pd.Series(ad, index=kline.index, name='AD')


def calc_taker_buy_ratio(kline):
    """Taker buy ratio: 主动买入量 / 总成交量 (%).

    Requires 'taker_buy_base_volume' column (Binance K-line).
    Returns pd.Series of percentages (0-100).
    """
    if kline is None or len(kline) == 0:
        return pd.Series(dtype=float)
    if 'taker_buy_base_volume' not in kline or 'volume' not in kline:
        return pd.Series(dtype=float)

    vol = kline['volume'].values
    taker_buy = kline['taker_buy_base_volume'].values

    ratio = np.where(vol > 0, taker_buy / vol * 100, 50.0)
    return pd.Series(ratio, index=kline.index, name='taker_buy_pct')


def calc_vwap(kline):
    """Volume Weighted Average Price (cumulative from start of period).

    VWAP = cumsum(typical_price * volume) / cumsum(volume)
    typical_price = (high + low + close) / 3

    Returns pd.Series.
    """
    if kline is None or len(kline) == 0:
        return pd.Series(dtype=float)
    if 'high' not in kline or 'low' not in kline or 'close' not in kline or 'volume' not in kline:
        return pd.Series(dtype=float)

    high = kline['high'].values
    low = kline['low'].values
    close = kline['close'].values
    vol = kline['volume'].values

    tp = (high + low + close) / 3.0
    cum_pv = np.cumsum(tp * vol)
    cum_v = np.cumsum(vol)
    vwap = np.where(cum_v > 0, cum_pv / cum_v, tp)
    return pd.Series(vwap, index=kline.index, name='VWAP')


def calc_volume_trend(kline, lookback=20):
    """Volume trend: 比当前 volume vs 过去 N 根平均 volume.

    Returns: {
      'current': float,         # 最近 volume
      'avg_lookback': float,    # 过去 N 根平均
      'ratio': float,           # current / avg
      'trend': str,             # 'expanding' / 'contracting' / 'normal'
    }
    """
    if kline is None or len(kline) < lookback + 1 or 'volume' not in kline:
        return {'current': 0, 'avg_lookback': 0, 'ratio': 0, 'trend': 'unknown'}

    vol = kline['volume'].values
    current = float(vol[-1])
    avg = float(np.mean(vol[-lookback - 1: -1]))
    ratio = current / avg if avg > 0 else 0

    if ratio > 1.5:
        trend = 'expanding'  # 放量
    elif ratio < 0.5:
        trend = 'contracting'  # 缩量
    else:
        trend = 'normal'

    return {
        'current': current,
        'avg_lookback': avg,
        'ratio': ratio,
        'trend': trend,
    }


__all__ = [
    'calc_obv',
    'calc_ad_line',
    'calc_taker_buy_ratio',
    'calc_vwap',
    'calc_volume_trend',
]
