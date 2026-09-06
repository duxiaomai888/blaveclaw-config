# Strategy: ETH A08 SHORT
# Type:     A (single symbol, signal-based)
# Symbol:   ETHUSDT
# Interval: 1h
# Logic:    Short when |HC| > abs_hc_th AND hc_delta < 0
#           业务逻辑: A08 "HC 高位下降" - 主力筹码在极端区持续减仓
#                     趋势力量衰竭,反向持仓(short)
# Position: 满仓 -1(简单反向,未做 vol scaling)
#
# Best params (from scan.py plateau-best):
#   abs_hc_th = 1.10, 180d ETHUSDT 1h, hold=12h, fee=5bp
#
# ── VALIDATION STATUS: ✅ 3-LAYER PASSED (2026-06-07) ──────────────────────
#   IS   (180d 1h, ETH)  : Sharpe 3.086   Ret +61.21%  MDD -13.95%  N=123
#   OOS  ( 90d 1h, ETH)  : Sharpe 2.751   Ret +20.66%  MDD -10.45%  N=61   (drop 10.9%)
#   MCPT (2000 perm)     : Actual 2.698   p=0.029                            (95% 显著)
#   >>> 跨币种验证: BTCUSDT 同参数 1.10 也通过(IS 2.05, OOS 2.03, MCPT 0.019)
#   >>> 推荐: 0.3x 仓位实盘,MDD 硬止损 -10%
#   validation.json: strategies/eth_a08_short/validation.json
# ──────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "eth_a08_short"
SYMBOL        = "ETHUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2025-12-09"
END           = "2026-06-07"   # 180 天回测窗口(跟粗筛/精扫一致)
FEE           = 0.0005

# A08 阈值(来自精扫 plateau-best)
ABS_HC_TH = 1.10   # |HC| 阈值

HOLD_BARS = 12     # 持有 12h 平仓(跟粗筛一致)

# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, abs_hc_th=ABS_HC_TH):
    """A08 不需要 rolling 指标,直接返回 df 副本。"""
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
                     (df['HC'] > df['HC'].shift(1)).astype(int) * 1   # sign of diff,0 if equal
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
# A08 短方向: 触发 |HC| > abs_hc_th & hc_delta < 0,持仓 -1 满仓 12 根
# 签名遵循 AGENTS.md 约定: 接受 entry_th / exit_th kwargs(由 scan_grid 传入)
def compute_signals(df, entry_th=ABS_HC_TH, exit_th=None, hold_bars=HOLD_BARS):
    from lib.strategy import hold_n_bars
    trigger = (df['abs_HC'] > entry_th) & (df['hc_delta'] < 0)
    return hold_n_bars(trigger, df.index, hold_bars, side=-1.0)

if __name__ == '__main__':
    import numpy as np
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
