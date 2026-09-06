"""
live_eval core breakdown — 突破/跌破 + 量能确认

提供:
  - detect_breakdown(kline, support_resistance_levels) -> {
      'breakouts': [...],   # 价格向上突破关键位 + 量能确认
      'breakdowns': [...],  # 价格向下突破关键位 + 量能确认
      'volume_confirm': True/False,
    }
"""
import numpy as np
import pandas as pd


def _find_local_levels(close, window=20, num_levels=3):
    """Find support/resistance levels using local min/max over rolling windows.

    Returns list of (idx, price, type) where type in ('support', 'resistance').
    """
    levels = []
    for i in range(window, len(close) - window):
        window_data = close.iloc[i - window: i + window + 1]
        center = close.iloc[i]
        if center == window_data.max():
            levels.append((i, float(center), 'resistance'))
        elif center == window_data.min():
            levels.append((i, float(center), 'support'))
    return levels


def detect_breakdown(kline, lookback=50, window=15, num_levels=3, vol_mult=1.3):
    """Detect breakouts/breakdowns with volume confirmation.

    Algorithm:
      1. Find recent support/resistance levels (local min/max)
      2. For each level:
         - If current price > level: check if it's a "breakout" (upward)
         - If current price < level: check if it's a "breakdown" (downward)
      3. Volume confirmation:
         - Recent volume >= lookback_avg_volume * vol_mult

    Args:
      kline:        pd.DataFrame with OHLCV
      lookback:     bars to look at for level detection
      window:       local min/max window
      num_levels:   number of levels to return
      vol_mult:     volume multiplier for confirmation (default 1.3x)

    Returns:
      {
        'breakouts':   [{level_price, current_price, volume_ratio, strength}, ...],
        'breakdowns':  [{level_price, current_price, volume_ratio, strength}, ...],
        'volume_confirm': bool,
        'vol_ratio':    float,  # current / avg
        'support_levels':  [(price, idx), ...],
        'resistance_levels': [(price, idx), ...],
      }
    """
    if kline is None or len(kline) < lookback + 1:
        return _empty_breakdown()

    close = kline['close']
    volume = kline['volume']

    # Step 1: Find local levels
    levels = _find_local_levels(close, window=window, num_levels=num_levels)

    # Filter to recent (in lookback)
    recent_levels = [(i, p, t) for i, p, t in levels
                     if i >= len(kline) - lookback and i < len(kline) - 1]

    # Sort by index, take last num_levels of each type
    supports = sorted([(p, i) for i, p, t in recent_levels if t == 'support'], key=lambda x: -x[1])[:num_levels]
    resistances = sorted([(p, i) for i, p, t in recent_levels if t == 'resistance'], key=lambda x: -x[1])[:num_levels]

    # Step 2: Check current price vs each level
    current_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])

    # Step 3: Volume confirmation
    if len(volume) >= 20:
        avg_vol = float(volume.iloc[-20: -1].mean())
        current_vol = float(volume.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0
    else:
        vol_ratio = 0
    volume_confirm = vol_ratio >= vol_mult

    breakouts = []
    breakdowns = []

    for level_price, level_idx in resistances:
        # Upward breakout: current price > level, was below before
        if current_price > level_price and prev_price <= level_price:
            strength = 'strong' if volume_confirm else 'weak'
            breakouts.append({
                'level_price': float(level_price),
                'current_price': current_price,
                'level_idx': int(level_idx),
                'volume_ratio': vol_ratio,
                'volume_confirm': volume_confirm,
                'strength': strength,
            })

    for level_price, level_idx in supports:
        # Downward breakdown: current price < level, was above before
        if current_price < level_price and prev_price >= level_price:
            strength = 'strong' if volume_confirm else 'weak'
            breakdowns.append({
                'level_price': float(level_price),
                'current_price': current_price,
                'level_idx': int(level_idx),
                'volume_ratio': vol_ratio,
                'volume_confirm': volume_confirm,
                'strength': strength,
            })

    return {
        'breakouts': breakouts,
        'breakdowns': breakdowns,
        'volume_confirm': volume_confirm,
        'vol_ratio': vol_ratio,
        'support_levels': supports,
        'resistance_levels': resistances,
    }


def _empty_breakdown():
    return {
        'breakouts': [],
        'breakdowns': [],
        'volume_confirm': False,
        'vol_ratio': 0,
        'support_levels': [],
        'resistance_levels': [],
    }


__all__ = ['detect_breakdown']
