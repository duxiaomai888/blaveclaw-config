"""
vote.py — 纯算法: 5 指标组合投票
=========================================================================
输入: indicators = {
    "HC": [...历史 N 个值...],   # Holder Concentration 主力集中度
    "MS": [...],                 # Market Sentiment 散户情绪
    "WH": [...],                 # Whale Hunter 巨鲸动向
    "TI": [...],                 # Taker Intensity 主动成交
    "LM": [...],                 # Liquidation Metrics 清算量
}
输出: signal ∈ {"long", "short", "flat", "uncertain"}
      conf  ∈ [0.0, 1.0]   信号置信度

设计原则:
  - 5 个指标独立打分 (raw, conf)
  - 加权求和 + 投票门槛
  - 趋势确认: 至少 min_confirm 根同向才出信号
  - K 线 / 价格完全不参与
=========================================================================
"""
from collections import deque
from typing import Dict, List, Tuple


# ── 默认参数 (13 个可调阈值) ──
DEFAULT_PARAMS = dict(
    # 单指标阈值
    hc_th_long=0.5, hc_th_strong=1.5,
    ms_th_long=0.5, ms_th_strong=1.5,
    ti_th=1.0, ti_strong_log=0.5,
    wh_th_long=0.3, wh_th_strong=1.0,
    lm_th=1.5,
    # 投票
    min_agree=3,            # 至少几个指标同向
    score_threshold=0.2,    # 归一化分数门槛
    # 趋势确认
    min_confirm=3,          # 连续 N 根同向才算确认
    # 输出过滤
    min_conf=0.3,           # 最小置信度, 低于此输出 uncertain
    # 权重 (可动态调)
    base_weight=0.20,
)


# ── 1. 单指标打分函数 ──
def _score_linear(series: List[float], th: float, th_strong: float) -> Tuple[int, float]:
    """
    线性指标: HC / MS / WH
    输出 (raw, conf)
      raw ∈ {-1, 0, +1}
      conf ∈ [0, 1]
    """
    v = series[-1] if series else 0.0
    if v is None: return (0, 0.0)
    th_safe = max(abs(th), 1e-6)
    if v > th_strong:  return (+1, min(1.0, (v - th_strong) / max(th_strong, 1e-6)))
    if v > th:        return (+1, 0.3 * (v - th) / th_safe)
    if v < -th_strong: return (-1, min(1.0, (abs(v) - th_strong) / max(th_strong, 1e-6)))
    if v < -th:        return (-1, 0.3 * (abs(v) - th) / th_safe)
    return (0, 0.0)


def _score_ti(series: List[float], ti_th: float, ti_strong_log: float) -> Tuple[int, float]:
    """
    Taker Intensity: 乘性指标, 用对数空间
    TI > 1 = 主动买盘, < 1 = 主动卖盘
    """
    import math
    v = series[-1] if series else 1.0
    if v is None or v <= 0: return (0, 0.0)
    lv = math.log(v)
    if lv > ti_strong_log:  return (+1, min(1.0, lv / (ti_strong_log * 2)))
    if lv > 0:             return (+1, 0.3 * lv / max(ti_th, 1e-6))
    if lv < -ti_strong_log: return (-1, min(1.0, abs(lv) / (ti_strong_log * 2)))
    if lv < 0:             return (-1, 0.3 * abs(lv) / max(ti_th, 1e-6))
    return (0, 0.0)


def _score_lm(series: List[float], lm_th: float, lookback: int = 6) -> Tuple[int, float]:
    """
    Liquidation: 看最近 lookback 根的方向决定顺势/反向
    大量清算 + 短期方向同向 → 同向 (顺势)
    """
    if len(series) < lookback: return (0, 0.0)
    v = series[-1]
    if v is None or v < lm_th: return (0, 0.0)
    recent = sum(series[-lookback:]) / lookback
    if recent is None: return (0, 0.0)
    direction = +1 if recent > 0 else -1
    conf = min(1.0, (v - lm_th) / max(lm_th, 1e-6)) * 0.7
    return (direction, conf)


# ── 2. 趋势特征 ──
def _trend(series: List[float], window: int = 5) -> int:
    """
    指标在最近 window 根的趋势方向
    +1 = 上升, -1 = 下降, 0 = 平
    """
    if len(series) < window: return 0
    recent = series[-window:]
    delta = recent[-1] - recent[0]
    if abs(delta) < 1e-6: return 0
    return +1 if delta > 0 else -1


def _trend_consistency(series: List[float], window: int = 5) -> float:
    """
    趋势一致性: 最近 window 根里同向变化的比例 [0, 1]
    """
    if len(series) < window + 1: return 0.0
    diffs = [series[-i] - series[-i - 1] for i in range(1, window + 1)
             if series[-i] is not None and series[-i - 1] is not None]
    if not diffs: return 0.0
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    dominant = max(pos, neg)
    return dominant / len(diffs)


# ── 3. 核心投票函数 (无状态) ──
def vote(indicators: Dict[str, List[float]], params: dict = None) -> Tuple[int, float, dict]:
    """
    5 指标点状打分 + 趋势加权

    输入:  indicators = {"HC": [..], "MS": [..], ...} 每个 list 是历史值, 至少 1 个
    输出:  (raw, conf, detail)
        raw ∈ {-1, 0, +1}
        conf ∈ [0, 1]
        detail = {"HC": (raw, conf, trend, consistency), ...}
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    scores = {}
    if "HC" in indicators and indicators["HC"]:
        scores["HC"] = _score_linear(indicators["HC"], p["hc_th_long"], p["hc_th_strong"])
    if "MS" in indicators and indicators["MS"]:
        scores["MS"] = _score_linear(indicators["MS"], p["ms_th_long"], p["ms_th_strong"])
    if "WH" in indicators and indicators["WH"]:
        scores["WH"] = _score_linear(indicators["WH"], p["wh_th_long"], p["wh_th_strong"])
    if "TI" in indicators and indicators["TI"]:
        scores["TI"] = _score_ti(indicators["TI"], p["ti_th"], p["ti_strong_log"])
    if "LM" in indicators and indicators["LM"]:
        scores["LM"] = _score_lm(indicators["LM"], p["lm_th"])

    # 加权求和
    w = p["base_weight"]
    weighted = sum(raw * conf * w for raw, conf in scores.values())
    norm = weighted / max(sum(w for _ in scores.values()), 1e-6)

    pos_votes = sum(1 for r, _ in scores.values() if r > 0)
    neg_votes = sum(1 for r, _ in scores.values() if r < 0)

    # 趋势加成: 如果多数指标趋势同向, 提升置信度
    trend_bonus = 0.0
    trend_signals = []
    for name, ind in indicators.items():
        if not ind: continue
        t = _trend(ind)
        if t != 0:
            trend_signals.append(t)
    if trend_signals:
        if all(t == +1 for t in trend_signals) and pos_votes >= p["min_agree"]:
            trend_bonus = 0.15
        elif all(t == -1 for t in trend_signals) and neg_votes >= p["min_agree"]:
            trend_bonus = 0.15
        # 一致性加成
        consistencies = [_trend_consistency(ind) for ind in indicators.values() if ind]
        if consistencies:
            avg_cons = sum(consistencies) / len(consistencies)
            trend_bonus *= avg_cons

    # 决策
    if pos_votes >= p["min_agree"] and norm > p["score_threshold"]:
        raw = +1
        conf = min(1.0, abs(norm) + trend_bonus)
    elif neg_votes >= p["min_agree"] and norm < -p["score_threshold"]:
        raw = -1
        conf = min(1.0, abs(norm) + trend_bonus)
    else:
        raw = 0
        conf = 0.0

    # detail
    detail = {}
    for name, ind in indicators.items():
        if name in scores:
            r, c = scores[name]
            detail[name] = {
                "raw": r, "conf": c,
                "trend": _trend(ind) if ind else 0,
                "consistency": round(_trend_consistency(ind), 2) if ind else 0.0,
                "current": ind[-1] if ind else None,
            }

    return (raw, conf, detail)


# ── 4. 状态机 (趋势确认: 连续 N 根同向才出信号) ──
class VoteSignal:
    """
    把无状态的 vote() 包装成带"连续 N 根同向才确认"的状态机
    K 线只用来推进状态, 不参与决策
    """
    def __init__(self, params: dict = None):
        self.p = {**DEFAULT_PARAMS, **(params or {})}
        self._streak = 0       # 同向连续计数
        self._streak_dir = 0   # 上一根的方向
        self._last_signal = "flat"
        self._last_conf = 0.0

    def update(self, indicators: Dict[str, List[float]]) -> dict:
        """
        每根 K 线调用一次, 推进状态
        输出: {"signal": "long"/"short"/"flat"/"uncertain", "conf": ..., "raw": ..., "detail": ...}
        """
        raw, conf, detail = vote(indicators, self.p)

        # 状态机: 连续 min_confirm 根同向才确认
        if raw == self._streak_dir and raw != 0:
            self._streak += 1
        else:
            self._streak_dir = raw
            self._streak = 1 if raw != 0 else 0

        if self._streak >= self.p["min_confirm"] and conf >= self.p["min_conf"]:
            signal = "long" if raw > 0 else "short"
        elif conf < self.p["min_conf"]:
            signal = "uncertain"
        else:
            signal = "flat"

        self._last_signal = signal
        self._last_conf = conf

        return {
            "signal": signal,
            "conf": round(conf, 3),
            "raw": raw,
            "streak": self._streak,
            "detail": detail,
        }

    def reset(self):
        self._streak = 0
        self._streak_dir = 0
        self._last_signal = "flat"
        self._last_conf = 0.0


# ── 5. 信号 → 仓位 (仅映射, 不接 K 线) ──
def signal_to_position(signal: str) -> int:
    """long → +1, short → -1, flat/uncertain → 0"""
    if signal == "long":  return +1
    if signal == "short": return -1
    return 0


# ── 测试: mock 指标流验证 ──
if __name__ == "__main__":
    import random
    random.seed(42)

    print("=" * 60)
    print("纯算法测试: mock 指标流 → 信号")
    print("=" * 60)

    # Case 1: 所有指标强烈做多
    print("\n[Case 1] HC=+2, MS=+1.8, WH=+1.5, TI=1.8, LM=+2.5")
    inds = {"HC": [0.5, 1.0, 2.0], "MS": [0.3, 1.0, 1.8],
            "WH": [0.2, 0.8, 1.5], "TI": [1.0, 1.4, 1.8], "LM": [0.5, 1.0, 2.5]}
    vs = VoteSignal()
    for i in range(5):
        d = vs.update(inds)
        print(f"  bar {i}: signal={d['signal']} conf={d['conf']} raw={d['raw']} streak={d['streak']}")

    # Case 2: 指标冲突, 不应有信号
    print("\n[Case 2] HC=+2, MS=-2 (矛盾: 主力做多, 散户恐慌)")
    vs.reset()
    inds = {"HC": [0.5, 1.5, 2.0], "MS": [-0.5, -1.5, -2.0],
            "WH": [0.1, 0.3, 0.5], "TI": [0.8, 0.9, 1.0], "LM": [0.3, 0.5, 0.8]}
    for i in range(5):
        d = vs.update(inds)
        print(f"  bar {i}: signal={d['signal']} conf={d['conf']} raw={d['raw']} streak={d['streak']}")

    # Case 3: 强空头
    print("\n[Case 3] 全部指标强烈做空")
    vs.reset()
    inds = {"HC": [0.5, -0.5, -2.0], "MS": [0.3, -0.5, -1.8],
            "WH": [0.2, -0.3, -1.5], "TI": [1.0, 0.6, 0.4], "LM": [0.5, 0.5, -2.5]}
    for i in range(5):
        d = vs.update(inds)
        print(f"  bar {i}: signal={d['signal']} conf={d['conf']} raw={d['raw']} streak={d['streak']}")

    # Case 4: 噪音 (随机指标)
    print("\n[Case 4] 随机噪声 (期望 flat/uncertain)")
    vs.reset()
    for i in range(8):
        inds = {
            "HC": [random.uniform(-3, 3) for _ in range(3)],
            "MS": [random.uniform(-3, 3) for _ in range(3)],
            "WH": [random.uniform(-3, 3) for _ in range(3)],
            "TI": [random.uniform(0.5, 2.0) for _ in range(3)],
            "LM": [random.uniform(0, 3) for _ in range(3)],
        }
        d = vs.update(inds)
        print(f"  bar {i}: signal={d['signal']} conf={d['conf']} raw={d['raw']} streak={d['streak']}")

    print("\n" + "=" * 60)
    print("无 K 线、无回测、无历史数据 — 纯算法验证完成")
    print("=" * 60)
