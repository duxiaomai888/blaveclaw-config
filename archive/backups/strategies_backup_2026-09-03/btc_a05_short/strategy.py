# Strategy: BTC A05 SHORT
# Type:     A (single symbol, signal-based)
# Symbol:   BTCUSDT
# Interval: 1h
# Logic:    Short when HC < -hc_th AND hc_delta > 0
#           业务逻辑: A05 "大户顺势推空" - 空头推升 + HC 持续增,空头力量在加码
# Position: 满仓 -1(简单反向,未做 vol scaling)
#
# Best params (from scan.py plateau-best):
#   hc_th = 1.545, 180d BTCUSDT 1h, hold=12h, fee=5bp
#
# ── VALIDATION STATUS: ✅ 3-LAYER PASSED (2026-06-07) ──────────────────────
#   IS   (180d 1h)  : Sharpe 2.756   Ret +16.47%  MDD -3.52%   N=32
#   OOS  ( 90d 1h)  : Sharpe 4.073   Ret +16.12%  MDD -3.52%   N=27  (drop -47.8%, OOS>IS)
#   MCPT (2000 perm): Actual 2.684   p=0.0260                          (95% 显著)
#   >>> 推荐: 0.3x 仓位实盘,MDD 硬止损 -10%
#   validation.json: strategies/btc_a05_short/validation.json
# ──────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "btc_a05_short"
SYMBOL        = "BTCUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2025-12-09"
END           = "2026-06-07"   # 180 天回测窗口(跟粗筛一致)
FEE           = 0.0005

# A05 阈值(来自精扫 plateau-best,与 argmax 一致)
HC_TH = 1.545   # |HC| 阈值,触发: HC < -1.545 & hc_delta > 0

HOLD_BARS = 12     # 持有 12h 平仓(跟粗筛一致)

# ── indicators ────────────────────────────────────────────────────────────────
import numpy as np

def _add_indicators(df, hc_th=HC_TH):
    """A05 不需要 rolling 指标,直接返回 df 副本。"""
    return df.copy()

# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_holder_concentration
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    hc    = fetch_holder_concentration(SYMBOL, INTERVAL, START, END)
    df = kline.copy()
    df = df.join(hc.rename(columns={"alpha": "HC"}), how='left')
    df["HC"] = df["HC"].ffill()
    # hc_delta: sign(HC.diff()),首根置 0
    df['hc_delta'] = np.sign(df['HC'].diff()).fillna(0)
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
# A05 短方向: 触发 HC < -hc_th & hc_delta > 0,持仓 -1 满仓 12 根
# 签名遵循 AGENTS.md 约定: 接受 entry_th / exit_th kwargs
def compute_signals(df, entry_th=HC_TH, exit_th=None, hold_bars=HOLD_BARS):
    from lib.strategy import hold_n_bars
    trigger = (df['HC'] < -entry_th) & (df['hc_delta'] > 0)
    return hold_n_bars(trigger, df.index, hold_bars, side=-1.0)

if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
