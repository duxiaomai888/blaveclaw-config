"""
live_eval core volume_profile — 筹码集中度(Volume Profile)计算

概念:
  - POC (Point of Control): 成交量最大的价位 — 强支撑/阻力
  - Value Area (VA): 包含 70% 成交量的价格区间
  - HVN (High Volume Node): 成交量峰值 — 强支撑
  - LVN (Low Volume Node): 成交量谷值 — 弱支撑(快速穿越)

实现策略:
  1. 用 Binance K线的 volume(更轻量,默认)
  2. 可选: 用 aggTrades(更精确,但量大)
  3. 把价格分成 N 个 bins(默认 50),统计每档成交量
  4. 找 POC(最大 bin),然后扩展到 VA(70% 累计)
"""
import numpy as np
import pandas as pd


def build_volume_profile(kline_df, num_bins=50, current_price=None, value_area_pct=0.70):
    """Build a Volume Profile from Binance K-line data.

    Args:
      kline_df:        pd.DataFrame with 'close' and 'volume' columns (UTC indexed)
      num_bins:        number of price bins to aggregate into (default 50)
      current_price:   reference price for "above/below" classification
      value_area_pct:  fraction of total volume to include in VA (default 0.70)

    Returns:
      {
        'bins':          [(price_low, price_high, volume, is_hvn), ...]  (sorted by price)
        'poc':           (price, volume),
        'value_area':    (va_low, va_high, pct_of_total),
        'total_volume':  float,
        'hvn_levels':    [(price, volume), ...]   # sorted by volume desc
        'lvn_levels':    [(price, volume), ...],  # sorted by volume asc
        'above_poc_vol': float,  # volume above POC
        'below_poc_vol': float,  # volume below POC
      }
    """
    if kline_df is None or len(kline_df) < 5 or 'close' not in kline_df.columns or 'volume' not in kline_df.columns:
        return _empty_profile()

    close = kline_df['close']
    volume = kline_df['volume']

    # Determine price range
    price_min = float(close.min())
    price_max = float(close.max())
    if price_max <= price_min:
        return _empty_profile()

    # Create bins
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_volumes = np.zeros(num_bins)

    # Allocate each bar's volume to its price bin
    for price, vol in zip(close.values, volume.values):
        if np.isnan(price) or np.isnan(vol) or vol <= 0:
            continue
        # Find the bin index for this price
        idx = int((price - price_min) / (price_max - price_min) * num_bins)
        idx = max(0, min(num_bins - 1, idx))
        bin_volumes[idx] += vol

    # Find POC
    poc_idx = int(np.argmax(bin_volumes))
    poc_price = float(bin_centers[poc_idx])
    poc_volume = float(bin_volumes[poc_idx])

    # Find Value Area (expand from POC until 70% total volume)
    total_volume = float(bin_volumes.sum())
    if total_volume <= 0:
        return _empty_profile()
    target_volume = total_volume * value_area_pct

    # Expand from POC outward
    left_idx = poc_idx
    right_idx = poc_idx
    current_volume = bin_volumes[poc_idx]
    while current_volume < target_volume and (left_idx > 0 or right_idx < num_bins - 1):
        left_vol = bin_volumes[left_idx - 1] if left_idx > 0 else 0
        right_vol = bin_volumes[right_idx + 1] if right_idx < num_bins - 1 else 0
        if left_vol >= right_vol and left_idx > 0:
            left_idx -= 1
            current_volume += bin_volumes[left_idx]
        elif right_idx < num_bins - 1:
            right_idx += 1
            current_volume += bin_volumes[right_idx]
        else:
            break

    va_low = float(bin_edges[left_idx])
    va_high = float(bin_edges[right_idx + 1])

    # HVN: bins with volume > mean + 1 std
    mean_vol = float(np.mean(bin_volumes))
    std_vol = float(np.std(bin_volumes))
    hvn_threshold = mean_vol + std_vol
    lvn_threshold = mean_vol - 0.5 * std_vol

    hvn_levels = []
    lvn_levels = []
    bins_list = []
    for i in range(num_bins):
        v = float(bin_volumes[i])
        low = float(bin_edges[i])
        high = float(bin_edges[i + 1])
        is_hvn = v > hvn_threshold
        is_lvn = v < lvn_threshold
        bins_list.append((low, high, v, is_hvn, is_lvn))
        if is_hvn:
            hvn_levels.append((float(bin_centers[i]), v))
        if is_lvn:
            lvn_levels.append((float(bin_centers[i]), v))

    hvn_levels.sort(key=lambda x: x[1], reverse=True)
    lvn_levels.sort(key=lambda x: x[1])

    # Above/below POC volume
    above_poc_vol = float(bin_volumes[poc_idx + 1:].sum())
    below_poc_vol = float(bin_volumes[:poc_idx].sum())

    return {
        'bins': bins_list,
        'poc': (poc_price, poc_volume),
        'value_area': (va_low, va_high, current_volume / total_volume),
        'total_volume': total_volume,
        'hvn_levels': hvn_levels[:5],  # top 5
        'lvn_levels': lvn_levels[:5],
        'above_poc_vol': above_poc_vol,
        'below_poc_vol': below_poc_vol,
    }


def _empty_profile():
    return {
        'bins': [],
        'poc': (0.0, 0.0),
        'value_area': (0.0, 0.0, 0.0),
        'total_volume': 0.0,
        'hvn_levels': [],
        'lvn_levels': [],
        'above_poc_vol': 0.0,
        'below_poc_vol': 0.0,
    }


__all__ = ['build_volume_profile']
