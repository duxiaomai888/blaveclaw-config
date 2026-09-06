"""
live_eval realtime_eval — 实时评估脚本(出 15 段 markdown 报告)

用法:
  python live_eval/scripts/realtime_eval.py BTCUSDT

输出:
  - 终端: 完整 15 段报告
  - 文件: live_eval/reports/{SYMBOL}_{YYYYMMDD_HHMMSS_BJ}.md
"""
import os
import sys
import io
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# Windows console UTF-8 fix
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass

# Make live_eval.core importable
SCRIPT_DIR = Path(__file__).parent
LIVE_EVAL_DIR = SCRIPT_DIR.parent
if str(LIVE_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_EVAL_DIR))

from core import (
    BJ_TZ, to_bj, DATA_DIR, REPORTS_DIR,
    fetch_klines, fetch_ticker_24h, fetch_liquidation_map, fetch_7dim_alpha,
)
from core.indicators import (
    calc_obv, calc_ad_line, calc_taker_buy_ratio, calc_vwap, calc_volume_trend,
)
from core.patterns import detect_patterns
from core.volume_profile import build_volume_profile
from core.breakdown import detect_breakdown
from core.scoring import compute_total_score


# ── Report sections ──

def _section_header(symbol, now):
    """Section 0: Header (price + 24h change)."""
    lines = [f'# {symbol} 7 档 + 形态评估', '']
    now_bj = to_bj(now)
    lines.append(f'**生成时间**: {now_bj.strftime("%Y-%m-%d %H:%M:%S")} 北京时间 (UTC+8)')
    return lines


def _section_price_now(symbol, now):
    """Section 1: Current price + 24h change."""
    t = fetch_ticker_24h(symbol)
    if t is None:
        return ['## 1. 当前价 + 24h', '', '_数据获取失败_', '']
    return [
        '## 1. 当前价 + 24h',
        '',
        f'**当前价**: {t["last_price"]:,.2f}  |  **24h**: {t["change_pct"]:+.2f}%  |  **24h 量**: {t["volume_24h"]:,.0f}',
        f'**24h 高/低**: {t["high_24h"]:,.2f} / {t["low_24h"]:,.2f}',
        '',
    ]


def _section_7tier_price(symbol, end_date_str):
    """Section 2: 7 档价格矩阵(Blave K-line)."""
    from core import fetch_7tier_kline, TIERS
    kline = fetch_7tier_kline(symbol, end_date_str)
    lines = ['## 2. 7 档价格矩阵 (Blave K-line)', '']
    lines.append('| 档 | 区间 | 涨跌幅 | 趋势 |')
    lines.append('|---|---|---|---|')
    for tier_name, df in kline.items():
        if df is None or len(df) < 2:
            lines.append(f'| {tier_name} | n/a | n/a | n/a |')
            continue
        try:
            cfg = TIERS[tier_name]
            n = cfg['bars_back']
            if len(df) <= n:
                pct = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
            else:
                pct = (df['close'].iloc[-1] / df['close'].iloc[-n-1] - 1) * 100
            sign = '+' if pct >= 0 else ''
            direction = '↑' if pct > 0.3 else ('↓' if pct < -0.3 else '→')
            lines.append(f'| {tier_name} | last {n} bars | {sign}{pct:.2f}% | {direction} |')
        except (ValueError, KeyError, TypeError):
            lines.append(f'| {tier_name} | error | n/a | n/a |')
    lines.append('')
    return lines


def _section_patterns(symbol):
    """Section 3: 6 经典形态识别(Binance OHLCV)."""
    k = fetch_klines(symbol, '1d', limit=100)
    if k is None or len(k) < 30:
        return ['## 3. 形态识别', '', '_K线数据不足_', '']
    result = detect_patterns(k, lookback=100, tol_pct=0.05, prominence_pct=0.05)
    lines = ['## 3. 形态识别 (6 经典)', '']
    lines.append(f'**K线范围**: {k.index[0].date()} ~ {k.index[-1].date()} ({len(k)} 根 1d K线)')
    lines.append(f'**找到峰**: {len(result["peaks"])} 个  |  **找到谷**: {len(result["troughs"])} 个')
    lines.append('')
    if not result['patterns']:
        lines.append('_未识别出明显形态(可能横盘,或在形成中)_')
        lines.append('')
        return lines
    lines.append('| 形态 | 类型 | 置信度 | 关键位 | 目标 |')
    lines.append('|---|---|---|---|---|')
    for p in result['patterns']:
        name = p.get('name', '')
        ptype = p.get('type', '')
        conf = p.get('confidence', 0)
        # Find key levels
        key_levels = []
        if 'neckline' in p:
            key_levels.append(f"颈线 {p['neckline']:.0f}")
        if 'left_shoulder' in p:
            key_levels.append(f"左肩 {p[list(p.keys())[1]][1]:.0f}")
        if 'head' in p:
            key_levels.append(f"头部 {p['head'][1]:.0f}")
        if 'right_shoulder' in p:
            key_levels.append(f"右肩 {p['right_shoulder'][1]:.0f}")
        if 'left_peak' in p:
            key_levels.append(f"左峰 {p['left_peak'][1]:.0f}")
        if 'right_peak' in p:
            key_levels.append(f"右峰 {p['right_peak'][1]:.0f}")
        if 'apex' in p and 'subtype' in p:
            key_levels.append(f"apex {p['apex']}")
        target = p.get('target', p.get('target_up', None))
        target_str = f"{target:.0f}" if target is not None else '—'
        key_str = ' / '.join(key_levels[:3]) if key_levels else '—'
        lines.append(f'| {name} | {ptype} | {conf:.2f} | {key_str} | {target_str} |')
    lines.append('')
    return lines


def _section_volume_profile(symbol):
    """Section 4: Volume Profile (POC/Value Area/HVN)."""
    k = fetch_klines(symbol, '1d', limit=60)
    if k is None or len(k) < 5:
        return ['## 4. Volume Profile', '', '_K线数据不足_', '']
    vp = build_volume_profile(k, num_bins=30)
    current_price = float(k['close'].iloc[-1])
    lines = ['## 4. Volume Profile (筹码集中度)', '']
    lines.append(f'**POC** (Point of Control): {vp["poc"][0]:,.0f} (vol={vp["poc"][1]:,.0f})')
    lines.append(f'**Value Area** (70% vol): {vp["value_area"][0]:,.0f} - {vp["value_area"][1]:,.0f} ({vp["value_area"][2]*100:.1f}%)')
    lines.append(f'**当前价位置**: {current_price:,.0f}  |  **相对 POC**: {(current_price - vp["poc"][0]):+,.0f} ({(current_price - vp["poc"][0]) / vp["poc"][0] * 100:+.2f}%)')
    lines.append('')
    lines.append(f'**上方 vs 下方 POC 的成交量**:')
    lines.append(f'  - POC 上方: {vp["above_poc_vol"]:,.0f} ({vp["above_poc_vol"]/vp["total_volume"]*100:.1f}%)')
    lines.append(f'  - POC 下方: {vp["below_poc_vol"]:,.0f} ({vp["below_poc_vol"]/vp["total_volume"]*100:.1f}%)')
    lines.append('')
    if vp['hvn_levels']:
        lines.append('**Top 3 HVN (高量节点,强支撑/阻力)**:')
        for price, vol in vp['hvn_levels'][:3]:
            label = '上方阻力' if price > current_price else '下方支撑'
            lines.append(f'  - {price:,.0f} (vol={vol:,.0f}) — {label}')
    if vp['lvn_levels']:
        lines.append('**Top 3 LVN (低量节点,快速穿越区)**:')
        for price, vol in vp['lvn_levels'][:3]:
            lines.append(f'  - {price:,.0f} (vol={vol:,.0f})')
    lines.append('')
    return lines


def _section_indicators(symbol):
    """Section 5: 量价指标(OBV/A-D/Taker buy ratio/VWAP/Volume trend)."""
    k = fetch_klines(symbol, '1d', limit=60)
    if k is None or len(k) < 5:
        return ['## 5. 量价指标', '', '_K线数据不足_', '']
    lines = ['## 5. 量价指标', '']

    # OBV
    obv = calc_obv(k)
    if len(obv) > 0:
        obv_5d_change = float(obv.iloc[-1] - obv.iloc[-6]) if len(obv) >= 6 else 0
        obv_trend = '上涨' if obv_5d_change > 0 else '下跌'
        lines.append(f'**OBV** (5d 变化): {obv_5d_change:+,.0f} — {obv_trend} (资金在{"流入" if obv_5d_change > 0 else "流出"})')

    # A/D Line
    ad = calc_ad_line(k)
    if len(ad) > 0:
        ad_5d = float(ad.iloc[-1] - ad.iloc[-6]) if len(ad) >= 6 else 0
        ad_trend = '积累' if ad_5d > 0 else '派发'
        lines.append(f'**A/D Line** (5d 变化): {ad_5d:+,.0f} — {ad_trend}')

    # Taker buy ratio
    tbr = calc_taker_buy_ratio(k)
    tbr_recent = float(tbr.iloc[-5:].mean()) if len(tbr) >= 5 else 50
    bias = '多头主导' if tbr_recent > 50 else '空头主导'
    lines.append(f'**主动买/卖比** (5d 均值): {tbr_recent:.1f}% — {bias}')

    # VWAP
    vwap = calc_vwap(k)
    if len(vwap) > 0:
        current_price = float(k['close'].iloc[-1])
        vwap_val = float(vwap.iloc[-1])
        diff_pct = (current_price - vwap_val) / vwap_val * 100
        pos = '在 VWAP 之上' if diff_pct > 0 else '在 VWAP 之下'
        lines.append(f'**VWAP**: {vwap_val:,.2f} (当前价 {current_price:,.2f}, {diff_pct:+.2f}% — {pos})')

    # Volume trend
    vt = calc_volume_trend(k)
    lines.append(f'**Volume Trend**: {vt["trend"]} (current={vt["current"]:,.0f}, avg={vt["avg_lookback"]:,.0f}, ratio={vt["ratio"]:.2f}x)')

    lines.append('')
    return lines


def _section_7d_alpha(symbol, end_date_str):
    """Section 6: 7 维 alpha × 7 档趋势矩阵(Blave)."""
    alpha = fetch_7dim_alpha(symbol, end_date_str)
    kline = fetch_klines_kline(symbol)  # need for 7-档
    from core import TIERS
    lines = ['## 6. 7 维 Alpha × 7 档 趋势矩阵', '']
    lines.append('| 维度 | 7d | 1d | 4h | 2h | 1h | 30m | 15m | 解读 |')
    lines.append('|---|---|---|---|---|---|---|---|---|')
    # PRICE row
    price_row = ['PRICE (K-line)']
    if kline:
        for t in ['7d', '1d', '4h', '2h', '1h', '30m', '15m']:
            df = kline.get(t)
            if df is None or len(df) < 2:
                price_row.append('—')
                continue
            try:
                cfg = TIERS[t]
                n = cfg['bars_back']
                if len(df) <= n:
                    pct = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
                else:
                    pct = (df['close'].iloc[-1] / df['close'].iloc[-n-1] - 1) * 100
                if pct > 0.3: price_row.append('↑')
                elif pct < -0.3: price_row.append('↓')
                else: price_row.append('→')
            except (ValueError, KeyError, TypeError):
                price_row.append('—')
    else:
        price_row.extend(['—'] * 7)
    price_row.append('价格基准')
    lines.append('| ' + ' | '.join(price_row) + ' |')

    # Alpha rows
    for dim, label in [('TI', 'Taker'), ('WH', 'Whale'), ('LIQ', 'Liquidation')]:
        row = [f'{dim} ({label})']
        for t in ['24h', '4h', '1h', '15min']:
            s = alpha.get(dim, {}).get(t, pd.Series(dtype=float))
            if len(s) == 0:
                row.append('—')
            else:
                # Use last value
                v = float(s.iloc[-1])
                # Arrow: depends on dim semantics
                row.append('↑' if v > 0.3 else ('↓' if v < -0.3 else '→'))
        row.extend(['—', '—', '—'])
        row.append('看多/看空')
        lines.append('| ' + ' | '.join(row) + ' |')

    for dim, label in [('HC_1d', 'Holder'), ('MD', 'Market Dir'), ('MS', 'Sentiment')]:
        s = alpha.get(dim, pd.Series(dtype=float))
        if len(s) == 0:
            row = [f'{dim} ({label})', '—', '—', '—', '—', '—', '—', '—', label]
        else:
            v = float(s.iloc[-1])
            row = [f'{dim} ({label})']
            for t in ['7d', '1d', '4h', '2h', '1h', '30m', '15m']:
                row.append('↑' if v > 0.3 else ('↓' if v < -0.3 else '→'))
            row.append(label)
        lines.append('| ' + ' | '.join(row) + ' |')

    lines.append('')
    return lines


def fetch_klines_kline(symbol):
    """Helper: get 7-tier kline."""
    from core import fetch_7tier_kline
    from datetime import datetime, timezone
    end = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    return fetch_7tier_kline(symbol, end)


def _section_funding_liquidation(symbol, current_price):
    """Section 7: Funding + Liquidation Map(Blave)."""
    liq = fetch_liquidation_map(symbol)
    # Get funding from alpha_table (need symbol, e.g. BTCUSDT)
    from core import fetch_alpha_table
    table = fetch_alpha_table()
    funding_weighted = None
    for table_key in [symbol.replace('USDT', ''), symbol]:
        if table_key in table:
            fr = table[table_key].get('funding_rate', {})
            vals = [v for v in fr.values() if v is not None and abs(v) < 0.01]
            if vals:
                funding_weighted = float(np.mean(vals))
                break

    lines = ['## 7. Funding + Liquidation Map', '']
    if funding_weighted is not None:
        annualized = funding_weighted * 100 * 3 * 365
        lines.append(f'**Funding 加权**: {funding_weighted*100:+.4f}%/8h (年化 {annualized:+.1f}%)')
        if funding_weighted < -0.0030:
            lines.append('  → **雷区** (空头极度拥挤,任何回调都可能轧空,做空风险极高)')
        elif funding_weighted < -0.0010:
            lines.append('  → **略空头拥挤** (空头燃料,反弹路上有阻力但也可被轧空)')
        elif funding_weighted > 0.0030:
            lines.append('  → **雷区** (多头极度拥挤)')
        elif funding_weighted > 0.0010:
            lines.append('  → **略多头拥挤** (多头燃料,下跌路上有阻力但也可被轧多)')
        else:
            lines.append('  → **中性** (多空力量均衡)')
    else:
        lines.append('**Funding**: n/a')
    lines.append('')

    if not liq or 'labels' not in liq or not liq.get('labels'):
        return lines

    labels = liq['labels']
    cumsum = liq.get('cumsum', [])
    if current_price and labels and cumsum and len(cumsum) == len(labels):
        import bisect
        cur_idx = min(bisect.bisect_left(labels, current_price), len(labels) - 1)
        lines.append('**累计爆仓地图 (价格移动 → 沿途爆仓量)**:')
        lines.append('')
        lines.append('| 距当前价 | 方向 | 累计触发的爆仓量 (USD) |')
        lines.append('|---|---|---|')
        for pct in [1, 2, 3, 5, 7, 10, 15, 20, 25, 30]:
            target_above = current_price * (1 + pct / 100)
            target_below = current_price * (1 - pct / 100)
            idx_above = min(bisect.bisect_left(labels, target_above), len(cumsum) - 1)
            idx_below = min(bisect.bisect_left(labels, target_below), len(cumsum) - 1)
            lines.append(f'| ±{pct}% | 上涨到 {target_above:,.0f} | 沿途 ~{cumsum[idx_above]:,.0f} USD 爆仓 |')
            lines.append(f'| ±{pct}% | 下跌到 {target_below:,.0f} | 沿途 ~{cumsum[idx_below]:,.0f} USD 爆仓 |')
        lines.append('')
    return lines, funding_weighted  # tuple for caller use


def _section_key_levels(symbol, current_price):
    """Section 8: 关键支撑/阻力(合并 VP + 突破)."""
    k = fetch_klines(symbol, '1d', limit=60)
    if k is None or current_price is None:
        return ['## 8. 关键支撑/阻力', '', '_数据不足_', '']
    vp = build_volume_profile(k, num_bins=30)
    bd = detect_breakdown(k, lookback=30, window=10)
    lines = ['## 8. 关键支撑/阻力', '']

    lines.append('**🟢 关键支撑 (从 Volume Profile HVN + 突破失败)**:')
    lines.append('')
    supports = vp.get('hvn_levels', [])
    for price, vol in supports[:3]:
        if price < current_price:
            dist_pct = (current_price - price) / current_price * 100
            lines.append(f'  - **{price:,.0f}** (距离 -{dist_pct:.2f}%, {vol:,.0f} USD 待触发)')

    lines.append('')
    lines.append('**🔴 关键阻力 (从 Volume Profile HVN + 突破失败)**:')
    lines.append('')
    resistances = vp.get('hvn_levels', [])
    for price, vol in resistances[:3]:
        if price > current_price:
            dist_pct = (price - current_price) / current_price * 100
            lines.append(f'  - **{price:,.0f}** (距离 +{dist_pct:.2f}%, {vol:,.0f} USD 待触发)')

    lines.append('')
    return lines


def _section_score_summary(symbol, alpha_data, patterns, breakdown_result, indicators, funding_weighted):
    """Section 12: 框架综合评分."""
    from core.scoring import compute_total_score
    result = compute_total_score(alpha_data, patterns, breakdown_result, indicators, funding_weighted)
    lines = ['## 12. 框架综合评分', '']
    lines.append(f'**总分**: **{result["total"]:+d}**  |  **方向**: {result["direction"]}')
    lines.append('')
    lines.append('| 组件 | 子分 | 详情 |')
    lines.append('|---|---|---|')
    # Alpha
    alpha = result['components']['alpha']
    alpha_details = ' / '.join(f'{k}={v["score"]:+d}' for k, v in alpha['breakdown'].items())
    lines.append(f'| 7维 alpha | {alpha["total"]:+d} | {alpha_details} |')
    # Pattern
    pat = result['components']['pattern']
    pat_details = ', '.join(f'{s["name"]}({s["score"]:+d}, conf={s["confidence"]:.2f})' for s in pat['scored'][:3]) or '—'
    lines.append(f'| 形态 | {pat["total"]:+d} | {pat_details} |')
    # Breakdown
    bd = result['components']['breakdown']
    bd_details = ', '.join(f'{d[0]}({d[2]:+d})' for d in bd['details'][:3]) or '—'
    lines.append(f'| 突破/跌破 | {bd["total"]:+d} | {bd_details} |')
    # Volume
    vol = result['components']['volume']
    vol_details = ', '.join(f'{d[0]}({d[2]:+d})' for d in vol['details'][:3]) or '—'
    lines.append(f'| 量价 | {vol["total"]:+d} | {vol_details} |')
    # Funding
    fund = result['components']['funding']
    lines.append(f'| Funding 雷区 | {fund["bonus"]:+d} | (雷区 -2, 拥挤 -1, 中性 0) |')
    lines.append('')
    return lines


def _section_open_analysis(symbol, kline_data, alpha_data, patterns, breakdown_result, indicators, funding_weighted, current_price, k):
    """Section 13: 开多/开空 实战分析(基于 4 维信号)."""
    score = compute_total_score(alpha_data, patterns, breakdown_result, indicators, funding_weighted)
    total = score['total']
    is_bullish = total >= 2
    is_bearish = total <= -2

    lines = ['## 13. 开多/开空 实战分析', '']
    lines.append(f'**当前**: {score["direction"]} (总分 {total:+d})')
    lines.append('')

    # 关键位(从 VP + 突破)
    if current_price and k is not None:
        vp = build_volume_profile(k, num_bins=30)
        lines.append('**7 档价格位置 + 关键位**:')
        lines.append('')
        lines.append('| 档 | 涨跌 | 含义 |')
        lines.append('|---|---|---|')
        from core import TIERS
        for tier_name, df in kline_data.items():
            if df is None or len(df) < 2:
                continue
            try:
                cfg = TIERS[tier_name]
                n = cfg['bars_back']
                if len(df) <= n:
                    pct = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
                else:
                    pct = (df['close'].iloc[-1] / df['close'].iloc[-n-1] - 1) * 100
                sign = '+' if pct >= 0 else ''
                arrow = '↑' if pct > 0.3 else ('↓' if pct < -0.3 else '→')
                lines.append(f'| {tier_name} | {sign}{pct:.2f}% {arrow} | {"上行" if pct > 0.3 else ("下行" if pct < -0.3 else "横盘")} |')
            except (ValueError, KeyError, TypeError):
                pass
        # 关键位
        for price, vol in vp.get('hvn_levels', [])[:2]:
            if price > current_price:
                dist = (price - current_price) / current_price * 100
                lines.append(f'| **关键阻力** | +{dist:.2f}% | {price:,.0f} ({vol:,.0f} USD 待触发) |')
            elif price < current_price:
                dist = (current_price - price) / current_price * 100
                lines.append(f'| **关键支撑** | -{dist:.2f}% | {price:,.0f} ({vol:,.0f} USD 待触发) |')
        lines.append('')

    # 实战建议
    if is_bullish:
        lines.append('**🟢 实战建议(框架偏多)**:')
        lines.append('')
        lines.append('  - **情景 1: 做多(主要方向)** — 推荐度 ⭐⭐⭐⭐')
        if current_price:
            lines.append(f'    - 入场: 现价 {current_price:,.0f} 附近')
            if k is not None and current_price:
                vp = build_volume_profile(k, num_bins=30)
                supports = [p for p, v in vp.get('hvn_levels', []) if p < current_price]
                if supports:
                    sup = supports[0]
                    lines.append(f'    - 止损: {sup:,.0f} (-{(current_price - sup) / current_price * 100:.2f}%)')
                resistances = [p for p, v in vp.get('hvn_levels', []) if p > current_price]
                if resistances:
                    res = resistances[0]
                    lines.append(f'    - 第一目标: {res:,.0f} (+{(res - current_price) / current_price * 100:.2f}%)')
        lines.append('  - **情景 2: 观望** — 等 1d 档转 UP 加仓')
        lines.append('  - **情景 3: 做空** — ❌ 不推荐,框架总分偏多')
    elif is_bearish:
        lines.append('**🔴 实战建议(框架偏空)**:')
        lines.append('')
        lines.append('  - **情景 1: 做空(主要方向)** — 推荐度 ⭐⭐⭐⭐')
        if current_price:
            lines.append(f'    - 入场: 现价 {current_price:,.0f} 附近')
            if kline_data and current_price:
                vp = build_volume_profile(kline_data, num_bins=30)
                supports = [p for p, v in vp.get('hvn_levels', []) if p < current_price]
                if supports:
                    sup = supports[0]
                    lines.append(f'    - 止损: {sup:,.0f} (-{(current_price - sup) / current_price * 100:.2f}%)')
        lines.append('  - **情景 2: 观望** — 等 1d 档转 DOWN 加空')
        lines.append('  - **情景 3: 做多** — ❌ 不推荐,框架总分偏空')
    else:
        lines.append('**⚪ 实战建议(框架中性)**:')
        lines.append('')
        lines.append('  - 7 维中正负相近,无明确方向')
        lines.append('  - 等总分散开到 ≥ +5 或 ≤ -5 再行动')
    lines.append('')
    return lines


# ── Main ──

def main():
    if len(sys.argv) < 2:
        print('Usage: python realtime_eval.py SYMBOL [SYMBOL2 ...]')
        print('Example: python realtime_eval.py BTCUSDT')
        sys.exit(1)

    symbols = [s.upper() for s in sys.argv[1:]]
    now = datetime.now(timezone.utc)
    end_date_str = now.strftime('%Y-%m-%d')

    for symbol in symbols:
        print(f'\n{"="*60}\n评估 {symbol} ...\n{"="*60}\n')

        # Build report
        all_lines = []

        # Section 0-1
        all_lines.extend(_section_header(symbol, now))
        all_lines.extend(_section_price_now(symbol, now))

        # Section 2: 7 档价格
        all_lines.extend(_section_7tier_price(symbol, end_date_str))

        # Get all data
        from core import fetch_7tier_kline
        kline_data = fetch_7tier_kline(symbol, end_date_str)
        alpha_data = fetch_7dim_alpha(symbol, end_date_str)
        patterns_result = detect_patterns(fetch_klines(symbol, '1d', limit=100), lookback=100, tol_pct=0.05, prominence_pct=0.05)
        patterns = patterns_result['patterns']
        k = fetch_klines(symbol, '1d', limit=60)
        breakdown_result = detect_breakdown(k, lookback=30, window=10) if k is not None else {}

        # Indicators
        indicators = {}
        if k is not None and len(k) > 0:
            tbr = calc_taker_buy_ratio(k)
            vt = calc_volume_trend(k)
            indicators['taker_buy_recent'] = float(tbr.iloc[-5:].mean()) if len(tbr) >= 5 else 50
            indicators['vol_trend'] = vt.get('trend', 'normal')

        # Funding
        from core import fetch_alpha_table
        table = fetch_alpha_table()
        funding_weighted = None
        for table_key in [symbol.replace('USDT', ''), symbol]:
            if table_key in table:
                fr = table[table_key].get('funding_rate', {})
                vals = [v for v in fr.values() if v is not None and abs(v) < 0.01]
                if vals:
                    funding_weighted = float(np.mean(vals))
                break

        # Get current price
        current_price = None
        t = fetch_ticker_24h(symbol)
        if t:
            current_price = t['last_price']

        # Sections 3-8
        all_lines.extend(_section_patterns(symbol))
        all_lines.extend(_section_volume_profile(symbol))
        all_lines.extend(_section_indicators(symbol))
        all_lines.extend(_section_7d_alpha(symbol, end_date_str))

        # Section 7: Funding + Liquidation (returns tuple)
        result_7 = _section_funding_liquidation(symbol, current_price)
        if isinstance(result_7, tuple):
            sec_7_lines, fw = result_7
            all_lines.extend(sec_7_lines)
            # If we got funding from this, prefer it
            if fw is not None:
                funding_weighted = fw
        else:
            all_lines.extend(result_7)

        all_lines.extend(_section_key_levels(symbol, current_price))

        # Section 12: 综合评分
        all_lines.extend(_section_score_summary(symbol, alpha_data, patterns, breakdown_result, indicators, funding_weighted))

        # Section 13: 实战分析
        all_lines.extend(_section_open_analysis(symbol, kline_data, alpha_data, patterns, breakdown_result, indicators, funding_weighted, current_price, k))

        # Final: 复盘位
        all_lines.append('## 15. 复盘位 (7 天后回填)')
        all_lines.append('')
        all_lines.append('**实际结果**: [ ]')
        all_lines.append('**符合预期**: [ ]')
        all_lines.append('**改进点**: [ ]')
        all_lines.append('')

        report = '\n'.join(all_lines)

        # Print
        print(report)

        # Save
        ts_str = to_bj(now).strftime('%Y%m%d_%H%M%S_BJ')
        out_path = REPORTS_DIR / f'{symbol}_{ts_str}.md'
        out_path.write_text(report, encoding='utf-8')
        print(f'\n[SAVED] {out_path}')


if __name__ == '__main__':
    main()
