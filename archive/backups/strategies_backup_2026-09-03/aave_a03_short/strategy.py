# Strategy: AAVE A03 SHORT
# Type:     A (single symbol, signal-based)
# Symbol:   AAVEUSDT
# Interval: 1h
# Logic:    Short when HC < -hc_th AND hc_delta < 0
#           业务逻辑: A03 "大户开空仓" - 主力筹码在空头侧极端区持续加仓
#                     趋势力量确认,反向持仓(short)
# Position: 满仓 -1(简单反向,未做 vol scaling)
#
# Best params (from scan.py plateau-best):
#   hc_th = 0.40, 30d AAVEUSDT 1h, hold=12h, fee=5bp
#   Scan 结果: 0.40 ~ 0.50 区间 Sharpe 都在 7.3+,0.40 处于稳健 plateau
#   粗筛阶段给的 0.443 也落在同一 plateau
#
# ── VALIDATION STATUS: ❌ NOT VALIDATED — 30d 精扫过拟合 (2026-06-11) ─────
#   IS   ( 30d 1h, AAVE)  : Sharpe 7.335   Ret +23.22%  MDD -5.19%   N=18
#   OOS  ( 90d 1h, AAVE)  : Sharpe -0.128  Ret -4.14%   MDD +36.16%  N=64
#   MCPT (2000 perm)      : Actual 7.490   p=0.071
#   >>> 失败原因: 30 天精扫样本太短,过拟合到 AAVE 单边 -38% 大跌市
#   >>> 翻车模式与 strategies/eth_e04_short/ 几乎一样(都是 30d 精扫 short 方向)
#   >>> 教训: 精扫样本必须 >= 90 天,优先用 180 天
#   >>> 保留原因: 作为"30d 精扫过拟合"的反例,警示后续
# ──────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "aave_a03_short"
SYMBOL        = "AAVEUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2026-05-12"      # 30 天回测窗口起点(粗筛 END 倒推)
END           = "2026-06-11"      # 30 天回测窗口终点
FEE           = 0.0005

# A03 阈值(来自精扫 plateau-best,scan.py 跑完回填)
HC_TH = 0.40     # |HC| 阈值: HC < -HC_TH 触发

HOLD_BARS = 12   # 持有 12h 平仓(跟粗筛一致)


# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, hc_th=HC_TH):
    """A03 不需要 rolling 指标,直接返回 df 副本。"""
    return df.copy()


# ── fetch_data ────────────────────────────────────────────────────────────────
import numpy as np

def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_holder_concentration
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    hc    = fetch_holder_concentration(SYMBOL, INTERVAL, START, END)
    df = kline.copy()
    df = df.join(hc.rename(columns={"alpha": "HC"}), how='left')
    df["HC"] = df["HC"].ffill()
    df['abs_HC']  = df['HC'].abs()
    df['hc_delta'] = (df['HC'] < df['HC'].shift(1)).astype(int) * -1 + \
                     (df['HC'] > df['HC'].shift(1)).astype(int) * 1   # sign of diff, 0 if equal
    return _add_indicators(df)


# ── compute_signals ────────────────────────────────────────────────────────────
# A03 短方向: 触发 HC < -hc_th & hc_delta < 0, 持仓 -1 满仓 12 根
# 签名遵循 AGENTS.md 约定: 接受 hc_th kwargs(由 scan_grid 传入)
def compute_signals(df, hc_th=HC_TH, exit_th=None, hold_bars=HOLD_BARS):
    """A03 是单参数规则,exit_th 是 scan_grid 强制传的占位参数,这里忽略。"""
    from lib.strategy import hold_n_bars
    trigger = (df['HC'] < -hc_th) & (df['hc_delta'] < 0)
    return hold_n_bars(trigger, df.index, hold_bars, side=-1.0)


if __name__ == '__main__':
    import numpy as np
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
