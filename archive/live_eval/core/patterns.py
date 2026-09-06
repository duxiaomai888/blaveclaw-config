"""
live_eval core patterns — 6 经典技术形态识别

识别 6 个形态:
  1. 头肩顶 (Head & Shoulders Top)    — 3 高点(中最高)+ 2 低点
  2. 头肩底 (Head & Shoulders Bottom) — 镜像
  3. 双顶 (Double Top)                — 2 高点接近
  4. 双底 (Double Bottom)             — 镜像
  5. 三角形 (Triangle)                 — 高低点收敛
  6. 旗形 (Flag)                       — 急速趋势后短暂整理

API:
  detect_patterns(kline_df, lookback=100) -> dict
    {
      'patterns': [{name, type, confidence, neckline, target, ...}, ...],
      'peaks': [(idx, price), ...],
      'troughs': [(idx, price), ...],
    }
"""
import numpy as np
import pandas as pd


# ── 峰谷检测 (纯 numpy 实现,无 scipy 依赖) ──

def find_peaks_troughs(series, distance=None, prominence_pct=0.005):
    """Find local peaks and troughs in a price series.

    Pure numpy implementation: for each bar, check if it's higher/lower than
    `distance` neighbors on each side AND its prominence (how much it stands
    out from the surrounding valleys/peaks) exceeds the threshold.

    Prominence = price drop from the peak to the highest surrounding valley
    (or rise from trough to highest surrounding peak). Robust against noise.

    Args:
      series:          pd.Series with price data (indexed by time)
      distance:        min horizontal distance between peaks (default: max(3, n//30))
      prominence_pct:  min prominence as fraction of price range (default 0.5%)

    Returns:
      peaks:   list of (index, value) sorted by index
      troughs: list of (index, value) sorted by index
    """
    values = series.values
    n = len(values)
    if n < 10:
        return [], []

    # Auto distance: 1/30 of data length, min 3
    if distance is None:
        distance = max(3, n // 30)

    # Prominence threshold
    price_range = float(np.ptp(values))
    prom_threshold = price_range * prominence_pct

    def find_extrema(arr, find_max=True, invert=False):
        """Find local extrema with prominence filtering.

        If invert=True, treat arr as inverted (i.e., look for local minima
        in the original values by finding local maxima in the inverted array).

        For PEAKS: prominence = peak - highest_neighboring_valley
        For TROUGHS: prominence = highest_neighboring_peak - trough
        """
        result_idx = []
        for i in range(n):
            # Skip points too close to edges (need full window)
            if i < distance or i >= n - distance:
                continue
            # Check if this point is a local extremum
            left_start = max(0, i - distance)
            right_end = min(n, i + distance + 1)
            left = arr[left_start:i]
            right = arr[i + 1:right_end]
            if find_max:
                # Local max: strictly higher than all in [i-distance, i+distance]
                neighbors = np.concatenate([left, right])
                if arr[i] <= neighbors.max():
                    continue
                # Prominence = how much higher the peak is than surrounding valleys
                surrounding_min = min(left.min() if len(left) > 0 else arr[i],
                                        right.min() if len(right) > 0 else arr[i])
                prominence = arr[i] - surrounding_min
            else:
                # Local min: strictly lower than all neighbors
                neighbors = np.concatenate([left, right])
                if arr[i] >= neighbors.min():
                    continue
                # Prominence = how much lower the trough is than surrounding peaks
                surrounding_max = max(left.max() if len(left) > 0 else arr[i],
                                        right.max() if len(right) > 0 else arr[i])
                prominence = surrounding_max - arr[i]
            if prominence >= prom_threshold:
                result_idx.append(i)
        return result_idx

    peak_idx = find_extrema(values, find_max=True)
    trough_idx = find_extrema(values, find_max=False)

    peaks = [(series.index[i], float(values[i])) for i in peak_idx]
    troughs = [(series.index[i], float(values[i])) for i in trough_idx]
    return peaks, troughs


def _select_significant(pts, n_keep=3):
    """Select top-n significant points (by absolute price deviation from median)."""
    if not pts:
        return []
    vals = [p[1] for p in pts]
    median = np.median(vals)
    # rank by distance from median
    pts_sorted = sorted(pts, key=lambda p: abs(p[1] - median), reverse=True)
    return pts_sorted[:n_keep]


# ── 形态识别器 ──

def _is_head_shoulders_top(peaks, troughs, tol_pct=0.03):
    """H&S Top: 3 peaks, middle highest, 2 troughs between them, peaks/ troughs alternating.

    Try all combinations of 3 peaks (sorted by index), return first match.
    """
    if len(peaks) < 3 or len(troughs) < 2:
        return None
    # Sort by index
    p_sorted = sorted(peaks, key=lambda x: x[0])
    t_sorted = sorted(troughs, key=lambda x: x[0])
    # Try last 5 combinations of 3 peaks (avoid super-old patterns)
    n_p = len(p_sorted)
    for i in range(max(0, n_p - 5), n_p - 2):
        for j in range(i + 1, min(n_p - 1, i + 4)):
            for k in range(j + 1, min(n_p, j + 4)):
                p = [p_sorted[i], p_sorted[j], p_sorted[k]]
                # Check pattern: P-T-P-T-P
                troughs_between = [t for t in t_sorted if p[0][0] < t[0] < p[1][0] and p[1][0] < t[0] < p[2][0]]
                if not troughs_between:
                    continue
                p1, p2, p3 = p[0][1], p[1][1], p[2][1]
                if not (p2 > p1 and p2 > p3):
                    continue
                if abs(p1 - p3) / p2 > tol_pct:
                    continue
                # Find neckline troughs (between P1-P2 and P2-P3)
                t1 = max((t for t in t_sorted if p[0][0] < t[0] < p[1][0]), key=lambda x: x[0], default=None)
                t2 = min((t for t in t_sorted if p[1][0] < t[0] < p[2][0]), key=lambda x: x[0], default=None)
                if not t1 or not t2:
                    continue
                if abs(t1[1] - t2[1]) / p2 > tol_pct:
                    continue
                neckline = (t1[1] + t2[1]) / 2
                target = neckline - (p2 - neckline)
                confidence = 0.7
                if abs(p1 - p3) / p2 < tol_pct / 2:
                    confidence += 0.1
                return {
                    'name': '头肩顶 (H&S Top)',
                    'type': 'bearish_reversal',
                    'confidence': round(confidence, 2),
                    'left_shoulder': p[0],
                    'head': p[1],
                    'right_shoulder': p[2],
                    'neckline': neckline,
                    'target': target,
                    'volume_pattern': 'left > head < right (递减)',
                }
    return None


def _is_head_shoulders_bottom(peaks, troughs, tol_pct=0.03):
    """H&S Bottom: 3 troughs, middle lowest, 2 peaks between them."""
    if len(peaks) < 2 or len(troughs) < 3:
        return None
    t_sorted = sorted(troughs, key=lambda x: x[0])
    p_sorted = sorted(peaks, key=lambda x: x[0])
    n_t = len(t_sorted)
    for i in range(max(0, n_t - 5), n_t - 2):
        for j in range(i + 1, min(n_t - 1, i + 4)):
            for k in range(j + 1, min(n_t, j + 4)):
                t = [t_sorted[i], t_sorted[j], t_sorted[k]]
                peaks_between = [p for p in p_sorted if t[0][0] < p[0] < t[1][0] and t[1][0] < p[0] < t[2][0]]
                if not peaks_between:
                    continue
                t1, t2, t3 = t[0][1], t[1][1], t[2][1]
                if not (t2 < t1 and t2 < t3):
                    continue
                if abs(t1 - t3) / t2 > tol_pct:
                    continue
                p1 = max((p for p in p_sorted if t[0][0] < p[0] < t[1][0]), key=lambda x: x[0], default=None)
                p2 = min((p for p in p_sorted if t[1][0] < p[0] < t[2][0]), key=lambda x: x[0], default=None)
                if not p1 or not p2:
                    continue
                if abs(p1[1] - p2[1]) / t2 > tol_pct:
                    continue
                neckline = (p1[1] + p2[1]) / 2
                target = neckline + (neckline - t2)
                confidence = 0.7
                if abs(t1 - t3) / t2 < tol_pct / 2:
                    confidence += 0.1
                return {
                    'name': '头肩底 (H&S Bottom)',
                    'type': 'bullish_reversal',
                    'confidence': round(confidence, 2),
                    'left_shoulder': t[0],
                    'head': t[1],
                    'right_shoulder': t[2],
                    'neckline': neckline,
                    'target': target,
                    'volume_pattern': 'left > head < right (递减)',
                }
    return None


def _is_double_top(peaks, troughs, tol_pct=0.02):
    """Double Top: 2 peaks within tol_pct, with trough between.

    Tries all combinations of last N peaks (not just last 2) to find valid patterns.
    """
    if len(peaks) < 2 or len(troughs) < 1:
        return None
    p_sorted = sorted(peaks, key=lambda x: x[0])
    t_sorted = sorted(troughs, key=lambda x: x[0])
    n_p = len(p_sorted)
    # Try combinations of last 8 peaks (skip very old)
    for i in range(max(0, n_p - 8), n_p - 1):
        for j in range(i + 1, min(n_p, i + 4)):
            p = [p_sorted[i], p_sorted[j]]
            # Find trough between these 2 peaks
            troughs_between = [t for t in t_sorted if p[0][0] < t[0] < p[1][0]]
            if not troughs_between:
                continue
            # Use the lowest trough (neckline)
            t_neck = min(troughs_between, key=lambda x: x[1])
            p1, p2 = p[0][1], p[1][1]
            if abs(p1 - p2) / max(p1, p2) > tol_pct:
                continue
            neckline = t_neck[1]
            target = neckline - (p1 - neckline)
            confidence = 0.65
            if abs(p1 - p2) / max(p1, p2) < tol_pct / 2:
                confidence += 0.1
            return {
                'name': '双顶 (Double Top)',
                'type': 'bearish_reversal',
                'confidence': round(confidence, 2),
                'left_peak': p[0],
                'right_peak': p[1],
                'neckline': neckline,
                'target': target,
            }
    return None


def _is_double_bottom(peaks, troughs, tol_pct=0.02):
    """Double Bottom: 2 troughs within tol_pct, with peak between.

    Tries all combinations of last N troughs.
    """
    if len(peaks) < 1 or len(troughs) < 2:
        return None
    t_sorted = sorted(troughs, key=lambda x: x[0])
    p_sorted = sorted(peaks, key=lambda x: x[0])
    n_t = len(t_sorted)
    for i in range(max(0, n_t - 8), n_t - 1):
        for j in range(i + 1, min(n_t, i + 4)):
            t = [t_sorted[i], t_sorted[j]]
            peaks_between = [p for p in p_sorted if t[0][0] < p[0] < t[1][0]]
            if not peaks_between:
                continue
            p_neck = max(peaks_between, key=lambda x: x[1])
            t1, t2 = t[0][1], t[1][1]
            if abs(t1 - t2) / max(t1, t2) > tol_pct:
                continue
            neckline = p_neck[1]
            target = neckline + (neckline - t1)
            confidence = 0.65
            if abs(t1 - t2) / max(t1, t2) < tol_pct / 2:
                confidence += 0.1
            return {
                'name': '双底 (Double Bottom)',
                'type': 'bullish_reversal',
                'confidence': round(confidence, 2),
                'left_trough': t[0],
                'right_trough': t[1],
                'neckline': neckline,
                'target': target,
            }
    return None


def _is_triangle(peaks, troughs, tol_pct=0.05):
    """Triangle: highs getting lower, lows getting higher (converging)."""
    if len(peaks) < 2 or len(troughs) < 2:
        return None
    p = sorted(peaks[-2:], key=lambda x: x[0])
    t = sorted(troughs[-2:], key=lambda x: x[0])
    # Highs decreasing
    if not (p[1][1] < p[0][1]):
        return None
    # Lows increasing
    if not (t[1][1] > t[0][1]):
        return None
    # Convergence: distance between high-low narrowing
    initial_range = abs(p[0][1] - t[0][1])
    final_range = abs(p[1][1] - t[1][1])
    if initial_range <= 0:
        return None
    if final_range / initial_range > 0.5:  # not converging enough
        return None
    # Triangle type (ascending/descending) by which side is steeper
    high_diff = p[0][1] - p[1][1]  # positive (going down)
    low_diff = t[1][1] - t[0][1]   # positive (going up)
    if low_diff > high_diff * 1.5:
        tri_type = 'Ascending Triangle (看多)'
    elif high_diff > low_diff * 1.5:
        tri_type = 'Descending Triangle (看空)'
    else:
        tri_type = 'Symmetrical Triangle (中性)'
    # Breakout target: triangle height
    target_up = max(p[0][1], p[1][1], t[0][1], t[1][1]) + initial_range
    target_down = min(p[0][1], p[1][1], t[0][1], t[1][1]) - initial_range
    # apex: last bar index where 2 lines meet (approximate)
    apex_idx = max(p[1][0], t[1][0])
    return {
        'name': '三角形 (Triangle)',
        'type': 'continuation',
        'subtype': tri_type,
        'confidence': 0.6,
        'apex': apex_idx,
        'target_up': target_up,
        'target_down': target_down,
    }


def _is_flag(closes, peaks, troughs, threshold=0.05):
    """Flag: sharp trend (pole) followed by short consolidation (flag).

    Simple check: last 20 bars show trending move, then last 5-10 bars consolidate.
    """
    if len(closes) < 30:
        return None
    # Look at first 20 bars trend
    pole = closes.iloc[-25: -10]
    flag = closes.iloc[-10:]
    pole_pct = (pole.iloc[-1] - pole.iloc[0]) / pole.iloc[0]
    flag_range = (flag.max() - flag.min()) / flag.iloc[0]
    # Flag: strong pole (>3%) then small flag (<2% range)
    if abs(pole_pct) < 0.03:
        return None
    if flag_range > 0.02:
        return None
    direction = '看多' if pole_pct > 0 else '看空'
    target = closes.iloc[-1] + (pole.iloc[-1] - pole.iloc[0])
    return {
        'name': '旗形 (Flag)',
        'type': 'continuation',
        'direction': direction,
        'confidence': 0.55,
        'pole_size_pct': abs(pole_pct) * 100,
        'flag_range_pct': flag_range * 100,
        'target': target,
    }


# ── 统一入口 ──

def _compute_volume_score(kline_df, pattern, lookback=5):
    """Compute volume confirmation score for a detected pattern.

    Volume theory:
      H&S Top:    left_vol > head_vol > right_vol  (递减, 最佳)
      H&S Bottom: left_vol < head_vol < right_vol  (递增, 最佳)
      Double Top:  2nd peak volume < 1st peak volume (递减, 突破时放量)
      Double Bot:  2nd trough volume > 1st trough volume
      Triangle:    volume decreasing during formation, expansion on breakout
      Flag:        flag volume < pole volume

    Returns dict with:
      'volume_score': 0-1 (1 = perfect confirmation)
      'volume_pattern': str (description)
    """
    if kline_df is None or 'volume' not in kline_df.columns or pattern is None:
        return {'volume_score': 0.5, 'volume_pattern': 'N/A'}

    pattern_name = pattern.get('name', '')
    volume = kline_df['volume']

    def avg_vol_at(idx, lookback=5):
        """Average volume around a given index."""
        if idx not in kline_df.index:
            return 0
        pos = kline_df.index.get_loc(idx)
        start = max(0, pos - lookback)
        end = min(len(kline_df), pos + lookback + 1)
        if start >= end:
            return 0
        return float(volume.iloc[start:end].mean())

    # H&S Top
    if pattern_name == '头肩顶 (H&S Top)':
        left = avg_vol_at(pattern['left_shoulder'][0])
        head = avg_vol_at(pattern['head'][0])
        right = avg_vol_at(pattern['right_shoulder'][0])
        if head > 0:
            # Score: left > head > right (best: 1.0, worst 0)
            ratio = (left - right) / head if head > 0 else 0
            vol_score = max(0, min(1, 0.5 + ratio / 2))
            pattern_desc = f'左肩 {left:.0f} > 头 {head:.0f} > 右肩 {right:.0f}'
            return {'volume_score': vol_score, 'volume_pattern': pattern_desc, 'left_vol': left, 'head_vol': head, 'right_vol': right}

    # H&S Bottom
    if pattern_name == '头肩底 (H&S Bottom)':
        left = avg_vol_at(pattern['left_shoulder'][0])
        head = avg_vol_at(pattern['head'][0])
        right = avg_vol_at(pattern['right_shoulder'][0])
        if head > 0:
            ratio = (right - left) / head if head > 0 else 0
            vol_score = max(0, min(1, 0.5 + ratio / 2))
            pattern_desc = f'左肩 {left:.0f} < 头 {head:.0f} < 右肩 {right:.0f}'
            return {'volume_score': vol_score, 'volume_pattern': pattern_desc, 'left_vol': left, 'head_vol': head, 'right_vol': right}

    # Double Top
    if pattern_name == '双顶 (Double Top)':
        l = avg_vol_at(pattern['left_peak'][0])
        r = avg_vol_at(pattern['right_peak'][0])
        if l > 0:
            # 2nd peak volume < 1st peak volume (decreasing)
            ratio = (l - r) / l
            vol_score = max(0, min(1, 0.5 + ratio))
            pattern_desc = f'左峰 {l:.0f} vs 右峰 {r:.0f}'
            return {'volume_score': vol_score, 'volume_pattern': pattern_desc, 'left_vol': l, 'right_vol': r}

    # Double Bottom
    if pattern_name == '双底 (Double Bottom)':
        l = avg_vol_at(pattern['left_trough'][0])
        r = avg_vol_at(pattern['right_trough'][0])
        if l > 0:
            ratio = (r - l) / l
            vol_score = max(0, min(1, 0.5 + ratio))
            pattern_desc = f'左谷 {l:.0f} vs 右谷 {r:.0f}'
            return {'volume_score': vol_score, 'volume_pattern': pattern_desc, 'left_vol': l, 'right_vol': r}

    # Triangle: volume should decrease during formation, then expand on breakout
    if pattern_name == '三角形 (Triangle)':
        # Compare first half volume vs second half
        if pattern.get('apex') is not None and pattern['apex'] in kline_df.index:
            pos = kline_df.index.get_loc(pattern['apex'])
            first_half = volume.iloc[max(0, pos - 20): pos].mean() if pos > 0 else 0
            second_half = volume.iloc[pos: min(len(volume), pos + 20)].mean() if pos < len(volume) else 0
            if first_half > 0:
                ratio = (first_half - second_half) / first_half
                vol_score = max(0, min(1, 0.5 + ratio))
                pattern_desc = f'前段 {first_half:.0f} vs 后段 {second_half:.0f}'
                return {'volume_score': vol_score, 'volume_pattern': pattern_desc}

    return {'volume_score': 0.5, 'volume_pattern': '未检查'}


def detect_patterns(kline_df, lookback=100, distance=None, tol_pct=0.05, prominence_pct=0.05):
    """Detect all 6 classic patterns in the given K-line DataFrame.

    Args:
      kline_df:        pd.DataFrame with 'close' column (UTC indexed)
      lookback:        number of bars to analyze (default 100)
      distance:        min horizontal distance between peaks (default auto)
      tol_pct:         tolerance for pattern matching (default 5%)
      prominence_pct:  min prominence as fraction of price range (default 5%)

    Returns:
      {
        'peaks': [(idx, price), ...],
        'troughs': [(idx, price), ...],
        'patterns': [pattern_dict, ...]   # see individual detectors
      }
    """
    if kline_df is None or len(kline_df) < 30:
        return {'peaks': [], 'troughs': [], 'patterns': []}

    close = kline_df['close']
    work = close.iloc[-lookback:] if len(close) > lookback else close

    # Find peaks and troughs (using scipy)
    peaks, troughs = find_peaks_troughs(work, distance=distance, prominence_pct=prominence_pct)

    patterns = []

    # Try each pattern detector (in priority order), passing tol_pct
    hs_top = _is_head_shoulders_top(peaks, troughs, tol_pct=tol_pct)
    if hs_top: patterns.append(hs_top)

    hs_bot = _is_head_shoulders_bottom(peaks, troughs, tol_pct=tol_pct)
    if hs_bot: patterns.append(hs_bot)

    dt = _is_double_top(peaks, troughs, tol_pct=tol_pct*0.7)
    if dt: patterns.append(dt)

    db = _is_double_bottom(peaks, troughs, tol_pct=tol_pct*0.7)
    if db: patterns.append(db)

    tri = _is_triangle(peaks, troughs, tol_pct=tol_pct)
    if tri: patterns.append(tri)

    flag = _is_flag(work, peaks, troughs)
    if flag: patterns.append(flag)

    # Compute volume confirmation for each pattern
    for p in patterns:
        vol_info = _compute_volume_score(work, p)
        p['volume_score'] = vol_info['volume_score']
        p['volume_pattern_detail'] = vol_info['volume_pattern']
        # Adjust confidence by volume score (multiply)
        p['confidence'] = round(p.get('confidence', 0.5) * (0.5 + 0.5 * vol_info['volume_score']), 2)

    return {
        'peaks': peaks,
        'troughs': troughs,
        'patterns': patterns,
    }


__all__ = [
    'find_peaks_troughs',
    'detect_patterns',
]
