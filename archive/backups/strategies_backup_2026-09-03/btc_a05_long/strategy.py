# Strategy: BTC A05 LONG
# Type:     A (single symbol, signal-based)
# Symbol:   BTCUSDT (Binance USDT-M perp)
# Interval: 1h
# Logic:    Long when HC > hc_th AND hc_delta < 0
#           业务逻辑: A05 "大户顺势推多" 镜像 — 大户筹码集中且集中度转向下行(顺势推多),多头力量在加码
# Position: 满仓 +1(简单反向,未做 vol scaling)
#
# 本策略为 btc_a05_short(筹码集中度做空)的对称做多镜像版,
# 阈值沿用 a05_short 的 plateau-best hc_th = 1.545,持有 12 根 K 线平仓。
# 参数未经本方向扫参,仅供测试参考。

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "btc_a05_long"
DISPLAY_NAME  = "BTC 筹码集中度做多(A05 镜像)"
DESCRIPTION   = "BTC 当大户筹码集中度(HC)高于阈值且转向下行时做多,持有 12 小时"
SYMBOL        = "BTCUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2026-08-03"
END           = None
FEE           = 0.0005         # Binance 永续 taker ≈ 0.05%

# A05 阈值(沿用 btc_a05_short plateau-best;做多方向为对称镜像,未单独扫参)
HC_TH = 1.545   # 触发: HC > 1.545 & hc_delta < 0

HOLD_BARS = 12     # 持有 12h 平仓

PLOT_SERIES = {"HC": ("HC", {"overlay": False}),
               "trigger_long": ("HC", {"levels": [HC_TH]})}


# ── indicators ────────────────────────────────────────────────────────────────
import numpy as np

def _add_indicators(df, hc_th=HC_TH):
    """A05 不需要 rolling 指标,直接返回 df 副本。"""
    return df.copy()

# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_holder_concentration
    kline = fetch_kline(SYMBOL, INTERVAL, START, END, hdrs)
    hc    = fetch_holder_concentration(SYMBOL, INTERVAL, START, END, hdrs)
    df = kline.copy()
    df = df.join(hc.rename(columns={"alpha": "HC"}), how='left')
    df["HC"] = df["HC"].ffill()
    # hc_delta: sign(HC.diff()),首根置 0
    df['hc_delta'] = np.sign(df['HC'].diff()).fillna(0)
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
# A05 做多方向: 触发 HC > hc_th & hc_delta < 0,持仓 +1 满仓 12 根后平仓
def compute_signals(df):
    import numpy as np
    import pandas as pd
    trigger = (df['HC'] > HC_TH) & (df['hc_delta'] < 0)
    # hold_n_bars: 触发后持有 HOLD_BARS 根,信号值 +1,其余平 0
    signal = pd.Series(0.0, index=df.index)
    for i, hit in enumerate(trigger):
        if hit:
            signal.iloc[i:i + HOLD_BARS] = 1.0
    return signal


if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
