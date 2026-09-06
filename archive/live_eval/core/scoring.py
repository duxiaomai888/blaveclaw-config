"""
live_eval core scoring — 综合评分

把 4 类信号综合成一个 -10 ~ +10 的分数:
  + 形态信号 (头肩/双顶/三角形等)
  + 量价信号 (OBV/AD/taker buy ratio)
  + 突破信号 (breakout with volume)
  + Blave 7 维 alpha

输出: total_score (int -10 ~ +10), breakdown per signal
"""
import numpy as np
import pandas as pd


# 评分规则
PATTERN_SCORES = {
    '头肩顶 (H&S Top)':    -3,
    '头肩底 (H&S Bottom)': +3,
    '双顶 (Double Top)':    -2,
    '双底 (Double Bottom)': +2,
    '三角形 (Triangle)':    +1,  # 默认看多,实际看 subtype
    '旗形 (Flag)':         +1,
}

TRIANGLE_SUBTYPE_SCORES = {
    'Ascending Triangle (看多)':  +2,
    'Descending Triangle (看空)': -2,
    'Symmetrical Triangle (中性)': 0,
}


def compute_alpha_score(alpha_data):
    """7-dim alpha 综合分(原 Blave 规则)。"""
    breakdown = {}
    # WH-24h
    s = alpha_data.get('WH', {}).get('24h', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['WH_24h'] = _alpha_score_wh(v)
    # HC-1d
    s = alpha_data.get('HC_1d', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['HC_1d'] = _alpha_score_hc(v)
    # TI-24h
    s = alpha_data.get('TI', {}).get('24h', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['TI_24h'] = _alpha_score_ti(v)
    # MD
    s = alpha_data.get('MD', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['MD'] = _alpha_score_md(v)
    # TTE
    s = alpha_data.get('TTE', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['TTE'] = _alpha_score_tte(v)
    # MS
    s = alpha_data.get('MS', pd.Series(dtype=float))
    v = float(s.iloc[-1]) if len(s) > 0 else 0
    breakdown['MS'] = _alpha_score_ms(v)
    total = sum(d['score'] for d in breakdown.values())
    return total, breakdown


def _alpha_score_wh(v):
    if v >= 1.5: return {'value': v, 'score': 2}
    if v >= 0.3: return {'value': v, 'score': 1}
    if v > -0.3: return {'value': v, 'score': 0}
    if v > -1.5: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def _alpha_score_hc(v):
    if v >= 1.0: return {'value': v, 'score': 2}
    if v >= 0.2: return {'value': v, 'score': 1}
    if v > -0.2: return {'value': v, 'score': 0}
    if v > -1.0: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def _alpha_score_ti(v):
    if v >= 2.0: return {'value': v, 'score': 2}
    if v >= 0.5: return {'value': v, 'score': 1}
    if v > -0.5: return {'value': v, 'score': 0}
    if v > -2.0: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def _alpha_score_md(v):
    if v >= 0.5: return {'value': v, 'score': 2}
    if v >= 0.1: return {'value': v, 'score': 1}
    if v > -0.1: return {'value': v, 'score': 0}
    if v > -0.5: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def _alpha_score_tte(v):
    if v >= 30: return {'value': v, 'score': 2}
    if v >= 10: return {'value': v, 'score': 1}
    if v > -10: return {'value': v, 'score': 0}
    if v > -30: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def _alpha_score_ms(v):
    # 逆向: 极度负 = 看多
    if v <= -0.7: return {'value': v, 'score': 2}
    if v <= -0.3: return {'value': v, 'score': 1}
    if v < 0.3: return {'value': v, 'score': 0}
    if v < 0.7: return {'value': v, 'score': -1}
    return {'value': v, 'score': -2}


def compute_pattern_score(patterns):
    """形态信号综合分(取最强信号 + 加权 confidence)。"""
    if not patterns:
        return 0, []
    scored = []
    for p in patterns:
        name = p.get('name', '')
        base = PATTERN_SCORES.get(name, 0)
        # 三角形按 subtype 加权
        if name == '三角形 (Triangle)':
            subtype = p.get('subtype', '')
            base = TRIANGLE_SUBTYPE_SCORES.get(subtype, 0)
        # 加权 confidence
        conf = p.get('confidence', 0.5)
        score = int(round(base * conf / 0.7))  # 0.7 confidence = 1x
        scored.append({'name': name, 'score': score, 'confidence': conf, 'type': p.get('type', '')})
    total = max(-5, min(5, sum(s['score'] for s in scored)))
    return total, scored


def compute_volume_score(indicators):
    """量价信号综合分(基于 OBV 趋势、主动买/卖比、AD 趋势)。"""
    score = 0
    details = []

    # Taker buy ratio (recent 5 bars)
    taker = indicators.get('taker_buy_recent', 50)
    if taker >= 60:
        score += 2
        details.append(('主动买/卖比偏多', taker, 2))
    elif taker >= 55:
        score += 1
        details.append(('主动买/卖比略多', taker, 1))
    elif taker <= 40:
        score -= 2
        details.append(('主动买/卖比偏空', taker, -2))
    elif taker <= 45:
        score -= 1
        details.append(('主动买/卖比略空', taker, -1))

    # Volume trend
    vol_trend = indicators.get('vol_trend', 'normal')
    if vol_trend == 'expanding':
        details.append(('成交量放大', None, 0))  # 中性,需要方向判断
    elif vol_trend == 'contracting':
        details.append(('成交量萎缩', None, -0.5))

    return max(-3, min(3, score)), details


def compute_breakdown_score(breakdown_result):
    """突破/跌破信号分。"""
    score = 0
    details = []
    for b in breakdown_result.get('breakouts', []):
        s = 2 if b.get('strength') == 'strong' else 1
        score += s
        details.append((f"突破 {b['level_price']:.0f}", b.get('strength'), s))
    for b in breakdown_result.get('breakdowns', []):
        s = -2 if b.get('strength') == 'strong' else -1
        score += s
        details.append((f"跌破 {b['level_price']:.0f}", b.get('strength'), s))
    return max(-4, min(4, score)), details


def compute_total_score(alpha_data, patterns, breakdown_result, indicators, funding_weighted=None):
    """综合 4 维信号,输出 -10 ~ +10 的总分 + breakdown。"""
    alpha_total, alpha_breakdown = compute_alpha_score(alpha_data)
    pattern_total, pattern_scored = compute_pattern_score(patterns)
    breakdown_total, breakdown_details = compute_breakdown_score(breakdown_result)
    volume_total, volume_details = compute_volume_score(indicators)

    # Funding 单独处理(已经在 alpha 里,这里只加惩罚/奖励)
    funding_bonus = 0
    if funding_weighted is not None:
        if funding_weighted < -0.0030:
            funding_bonus = -2  # 雷区
        elif funding_weighted < -0.0010:
            funding_bonus = -1
        elif funding_weighted > 0.0030:
            funding_bonus = -2  # 雷区
        elif funding_weighted > 0.0010:
            funding_bonus = 0  # 略拥挤

    total = alpha_total + pattern_total + breakdown_total + volume_total + funding_bonus
    total = max(-14, min(14, total))

    if total >= 5:
        direction = '[GREEN] 强偏多'
    elif total >= 2:
        direction = '[GREEN] 偏多'
    elif total > -2:
        direction = '[GRAY] 中性'
    elif total > -5:
        direction = '[RED] 偏空'
    else:
        direction = '[RED] 强偏空'

    return {
        'total': total,
        'direction': direction,
        'components': {
            'alpha': {'total': alpha_total, 'breakdown': alpha_breakdown},
            'pattern': {'total': pattern_total, 'scored': pattern_scored},
            'breakdown': {'total': breakdown_total, 'details': breakdown_details},
            'volume': {'total': volume_total, 'details': volume_details},
            'funding': {'bonus': funding_bonus},
        },
    }


__all__ = [
    'compute_alpha_score', 'compute_pattern_score',
    'compute_volume_score', 'compute_breakdown_score',
    'compute_total_score',
    'PATTERN_SCORES', 'TRIANGLE_SUBTYPE_SCORES',
]
