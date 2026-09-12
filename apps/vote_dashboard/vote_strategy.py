"""
VoteStrategy — BBAC-D α 指标六票制算法
完全利用 Blave α 指标 (HC/MS/WH/TI/BC/LM), K 线只用于价格执行
不依赖 K 线形态(无缠论/无 MA/无 RSI)

设计: 六指标独立决策 → 加权投票 → 状态机控制进出
"""
import numpy as np
import pandas as pd
from typing import Tuple


# ──────────────────────────────────────────────────────────────
# 1. 动态权重
# ──────────────────────────────────────────────────────────────
def adaptive_weights(df: pd.DataFrame, base: dict) -> dict:
    """
    根据市场状态(波动率/清算量)动态调权重
    - 高波动: TI ↑ (动量更重要), HC ↓ (集中度易被打破)
    - 低波动: HC ↑ (集中度更稳定), TI ↓
    - 大量清算: LM ↑ (清算信号有信息量)
    """
    w = base.copy()

    # 波动率调节
    vol = df["realized_vol"].iloc[-1] if "realized_vol" in df.columns else 0.3
    if pd.notna(vol):
        if vol > 0.5:
            w["TI"] = w.get("TI", 0.2) * 1.5
            w["HC"] = w.get("HC", 0.2) * 0.7
        else:
            w["HC"] = w.get("HC", 0.2) * 1.5
            w["TI"] = w.get("TI", 0.2) * 0.7

    # 清算信号加成
    if "LM" in df.columns:
        lm = df["LM"].iloc[-1]
        if pd.notna(lm) and lm > 1.5:
            w["LM"] = w.get("LM", 0.15) * 2.0

    # 归一化
    total = sum(w.values())
    if total > 0:
        w = {k: v / total for k, v in w.items()}
    return w


# ──────────────────────────────────────────────────────────────
# 2. 单指标决策函数
# ──────────────────────────────────────────────────────────────
def _decide_linear(series: pd.Series, th: float, th_strong: float) -> Tuple[int, float]:
    """线性阈值决策 (HC/MS/WH/BC 用)"""
    if len(series) == 0:
        return (0, 0.0)
    v = series.iloc[-1]
    if pd.isna(v):
        return (0, 0.0)
    th_safe = max(abs(th), 1e-6)
    if v > th_strong:
        return (+1, min(1.0, (v - th_strong) / max(th_strong, 1e-6)))
    if v > th:
        return (+1, 0.3 * (v - th) / th_safe)
    if v < -th_strong:
        return (-1, min(1.0, (abs(v) - th_strong) / max(th_strong, 1e-6)))
    if v < -th:
        return (-1, 0.3 * (abs(v) - th) / th_safe)
    return (0, 0.0)


def _decide_ti(series: pd.Series, ti_th: float, ti_strong_log: float) -> Tuple[int, float]:
    """Taker Intensity 决策 — 乘性指标用对数"""
    if len(series) == 0:
        return (0, 0.0)
    v = series.iloc[-1]
    if pd.isna(v) or v <= 0:
        return (0, 0.0)
    lv = np.log(v)
    if lv > ti_strong_log:
        return (+1, min(1.0, lv / (ti_strong_log * 2)))
    if lv > 0:
        return (+1, 0.3 * lv / max(ti_th, 1e-6))
    if lv < -ti_strong_log:
        return (-1, min(1.0, abs(lv) / (ti_strong_log * 2)))
    if lv < 0:
        return (-1, 0.3 * abs(lv) / max(ti_th, 1e-6))
    return (0, 0.0)


def _decide_lm(series: pd.Series, lm_th: float) -> Tuple[int, float]:
    """Liquidation 决策 — 看 6 根均值方向判定顺势/反向"""
    if len(series) < 6:
        return (0, 0.0)
    v = series.iloc[-1]
    if pd.isna(v) or v < lm_th:
        return (0, 0.0)
    recent = series.iloc[-6:].mean()
    if pd.isna(recent):
        return (0, 0.0)
    direction = +1 if recent > 0 else -1
    conf = min(1.0, (v - lm_th) / max(lm_th, 1e-6)) * 0.7  # 清算信号降权
    return (direction, conf)


# ──────────────────────────────────────────────────────────────
# 3. 投票核心
# ──────────────────────────────────────────────────────────────
class VoteSignal:
    def __init__(self,
                 # 单指标阈值
                 hc_th_long=0.5, hc_th_strong=1.5,
                 ms_th_long=0.5, ms_th_strong=1.5,
                 ti_th=1.0, ti_strong_log=0.5,
                 wh_th_long=0.3, wh_th_strong=1.0,
                 lm_th=1.5, bc_th=0.6,
                 # 投票门槛
                 min_agree=3,
                 score_threshold=0.2,
                 # 状态机
                 confirm_bars=3,
                 exit_bars=5,
                 max_hold_bars=48,
                 ):
        self.p = dict(
            hc_th_long=hc_th_long, hc_th_strong=hc_th_strong,
            ms_th_long=ms_th_long, ms_th_strong=ms_th_strong,
            ti_th=ti_th, ti_strong_log=ti_strong_log,
            wh_th_long=wh_th_long, wh_th_strong=wh_th_strong,
            lm_th=lm_th, bc_th=bc_th,
            min_agree=min_agree, score_threshold=score_threshold,
            confirm_bars=confirm_bars, exit_bars=exit_bars,
            max_hold_bars=max_hold_bars,
        )
        self._reset_sm()

    def _reset_sm(self):
        self.state = "IDLE"           # IDLE/ENTERING/HOLDING
        self.position = 0              # +1/-1/0
        self.pending_dir = 0
        self.pending_count = 0
        self.opposite_count = 0
        self.bars_held = 0
        self.entry_idx = -1

    def _vote_once(self, df: pd.DataFrame) -> Tuple[int, float, dict]:
        """对当前 df 最后一根 K 线做六指标投票"""
        p = self.p
        base_w = {"HC": 0.20, "MS": 0.20, "TI": 0.20, "WH": 0.20, "LM": 0.15, "BC": 0.05}
        w = adaptive_weights(df, base_w)

        detail = {}
        if "HC" in df.columns:
            detail["HC"] = _decide_linear(df["HC"], p["hc_th_long"], p["hc_th_strong"])
        if "MS" in df.columns:
            detail["MS"] = _decide_linear(df["MS"], p["ms_th_long"], p["ms_th_strong"])
        if "TI" in df.columns:
            detail["TI"] = _decide_ti(df["TI"], p["ti_th"], p["ti_strong_log"])
        if "WH" in df.columns:
            detail["WH"] = _decide_linear(df["WH"], p["wh_th_long"], p["wh_th_strong"])
        if "LM" in df.columns:
            detail["LM"] = _decide_lm(df["LM"], p["lm_th"])
        if "BC" in df.columns:
            detail["BC"] = _decide_linear(df["BC"], p["bc_th"], 0.8)

        score = sum(raw * conf * w.get(k, 0) for k, (raw, conf) in detail.items())
        norm = score / max(sum(w.values()), 1e-6)

        pos_votes = sum(1 for r, _ in detail.values() if r > 0)
        neg_votes = sum(1 for r, _ in detail.values() if r < 0)

        if pos_votes >= p["min_agree"] and norm > p["score_threshold"]:
            return (+1, abs(norm), detail)
        if neg_votes >= p["min_agree"] and norm < -p["score_threshold"]:
            return (-1, abs(norm), detail)
        return (0, 0.0, detail)

    # ── 主信号接口: 喂历史 K 线 + 指标 → 输出交易决策 ──
    def next(self, df: pd.DataFrame, idx: int = -1) -> dict:
        """
        df: 必须有列 Close/HC/MS/TI/WH[+LM/BC], index 是时间
        idx: 取哪一根做决策(默认最后一根 = -1)

        返回 dict:
          action: "open" / "close" / "hold"
          side: +1 / -1 / 0
          reason: 触发原因字符串
          vote_signal: 当前投票原始方向
          vote_conf: 当前投票置信度
          detail: 各指标贡献 {HC: (raw, conf), ...}
          state: 当前状态机状态
        """
        if len(df) < 50:
            return {"action": "hold", "side": 0, "state": "IDLE",
                    "vote_signal": 0, "vote_conf": 0.0, "detail": {}}

        sig_dir, sig_conf, detail = self._vote_once(df)
        out = {"vote_signal": sig_dir, "vote_conf": sig_conf, "detail": detail}

        # ── IDLE: 等信号 ──
        if self.state == "IDLE":
            if sig_dir != 0 and sig_conf > 0.3:
                self.pending_dir = sig_dir
                self.pending_count = 1
                self.state = "ENTERING"
            return {**out, "action": "hold", "side": 0, "state": self.state}

        # ── ENTERING: 等待 N 根确认 ──
        if self.state == "ENTERING":
            if sig_dir == self.pending_dir:
                self.pending_count += 1
            elif sig_dir == -self.pending_dir:
                # 反向 → 切换 pending 方向(只计 1 根)
                self.pending_dir = sig_dir
                self.pending_count = 1
            else:
                # 无信号 → 重置
                self.pending_count = 0
                if self.pending_count == 0 and self.pending_dir == 0:
                    self.state = "IDLE"

            if self.pending_count >= self.p["confirm_bars"]:
                self.state = "HOLDING"
                self.position = self.pending_dir
                self.bars_held = 0
                self.entry_idx = idx
                return {**out, "action": "open", "side": self.position,
                        "state": self.state, "reason": f"vote_confirmed_{self.p['confirm_bars']}bars"}
            return {**out, "action": "hold", "side": 0, "state": self.state}

        # ── HOLDING: 监控反向信号 / 超时 ──
        if self.state == "HOLDING":
            self.bars_held += 1

            # 超时保护
            if self.bars_held >= self.p["max_hold_bars"]:
                self._reset_sm()
                return {**out, "action": "close", "side": 0,
                        "state": "IDLE", "reason": "max_hold_reached"}

            # 反向信号持续 N 根
            if sig_dir == -self.position and sig_conf > 0.2:
                self.opposite_count += 1
            else:
                self.opposite_count = max(0, self.opposite_count - 1)

            if self.opposite_count >= self.p["exit_bars"]:
                self._reset_sm()
                return {**out, "action": "close", "side": 0,
                        "state": "IDLE", "reason": "reverse_confirmed"}

            return {**out, "action": "hold", "side": self.position, "state": self.state}

        return {**out, "action": "hold", "side": 0, "state": self.state}


# ──────────────────────────────────────────────────────────────
# 4. 回测引擎 (复用 BBAC-D 的 precise_pnl)
# ──────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame,
                 vs: VoteSignal = None,
                 fee: float = 0.0005,
                 verbose: bool = True) -> dict:
    """
    df: 含 Close + α 指标列 + realized_vol(可选)
    vs: VoteSignal 实例(默认参数)
    返回 stats dict
    """
    from lib.analysis import precise_pnl, compute_stats

    if vs is None:
        vs = VoteSignal()

    if "realized_vol" not in df.columns and "Close" in df.columns:
        log_ret = np.log(df["Close"] / df["Close"].shift(1))
        df = df.copy()
        df["realized_vol"] = log_ret.rolling(720).std() * np.sqrt(8760)

    n = len(df)
    positions = np.zeros(n)
    actions = []

    for i in range(50, n):  # warmup 50 根
        sub = df.iloc[: i + 1]
        d = vs.next(sub, idx=i)
        if d["action"] == "open":
            positions[i] = d["side"]
            actions.append((i, "open", d["side"], d.get("reason", "")))
        elif d["action"] == "close":
            positions[i] = 0
            actions.append((i, "close", d["side"], d.get("reason", "")))
        elif d["state"] == "HOLDING":
            positions[i] = vs.position
        # else: positions[i] = 0 (默认)

    # 对齐 BBAC-D 仓位计算: w_curr[t] = signal[t-1]
    w_curr = np.zeros(n)
    w_curr[1:] = positions[:-1]
    w_prev = np.zeros(n)
    if n >= 2:
        w_prev[2:] = positions[:-2]

    close_v = df["Close"].values
    open_v = df["Open"].values if "Open" in df.columns else close_v
    exec_at_close = np.zeros(n, dtype=bool)

    pf_ret, _, _, tc_daily = precise_pnl(close_v, open_v, w_curr, w_prev, exec_at_close, fee)
    sharpe, sortino, omega, mdd_raw, _ = compute_stats(pf_ret, df.index)

    total_ret = float(np.prod(1 + np.nan_to_num(pf_ret)) - 1) * 100
    mdd = abs(mdd_raw) * 100
    bench_ret = (close_v[-1] / close_v[0] - 1) * 100

    n_trades = len([a for a in actions if a[1] == "open"])

    stats = {
        "total_return_pct": round(total_ret, 2),
        "benchmark_return_pct": round(bench_ret, 2),
        "sharpe": round(sharpe, 3) if not np.isnan(sharpe) else None,
        "sortino": round(sortino, 3) if not np.isnan(sortino) else None,
        "omega": round(omega, 3) if not np.isnan(omega) else None,
        "mdd_pct": round(mdd, 2),
        "n_trades": n_trades,
        "fee_total_pct": round(float(tc_daily.sum()) * 100, 4),
        "n_bars": n,
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
    }

    if verbose:
        print("=" * 60)
        print(f"VoteStrategy 回测结果 — BTCUSDT 1h")
        print("=" * 60)
        print(f"  区间:        {stats['start'][:10]}  →  {stats['end'][:10]}")
        print(f"  K线数:       {stats['n_bars']:,}")
        print(f"  交易次数:    {stats['n_trades']}")
        print(f"  总收益:      {stats['total_return_pct']:.2f}%")
        print(f"  基准(B&H):   {stats['benchmark_return_pct']:.2f}%")
        print(f"  Sharpe:      {stats['sharpe']}")
        print(f"  Sortino:     {stats['sortino']}")
        print(f"  MDD:         {stats['mdd_pct']:.2f}%")
        print(f"  手续费累计:  {stats['fee_total_pct']:.4f}%")
        print("=" * 60)

    return {"stats": stats, "actions": actions, "vs": vs}
