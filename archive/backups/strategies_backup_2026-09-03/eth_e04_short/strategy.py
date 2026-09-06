# Strategy: ETH E04 SHORT
# Type:     A (single symbol, signal-based)
# Symbol:   ETHUSDT
# Interval: 1h
# Logic:    Short when TI > ti_th AND ti_sign > 0 AND MS > ms_th
#           业务逻辑: E04 "TI+板块共振" - 动能 + 板块情绪同步,反向持仓
#           注: 文档方向是 long,但 30 天下跌市场反向有效,所以持仓 short
# Position: 满仓 -1(简单反向,未做 vol scaling)
#
# Best params (from scan.py plateau-best):
#   TI > -0.180, MS > 1.254, 30d ETHUSDT 1h, hold=12h, fee=5bp
#
# ── VALIDATION STATUS: ❌ NOT VALIDATED — 过拟合教训 (2026-06-07) ─────────
#   IS   ( 30d 1h)  : Sharpe 6.69   Ret +16.07%  MDD -2.84%   ← 看起来很好
#   OOS  (120d 1h)  : Sharpe -0.38  Ret -4.12%   MDD -19.53%  ← 崩了
#   MCPT            : 未跑(已被 OOS 否定)
#   >>> 失败原因: 30 天精扫样本太短,过拟合到 ETH 单边 -29% 大跌市
#   >>> 教训: 精扫样本必须 >= 90 天,优先用 180 天
#   >>> 保留原因: 作为"小窗口过拟合"的反例,警示后续
# ──────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "eth_e04_short"
SYMBOL        = "ETHUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2026-05-08"
END           = "2026-06-07"   # 30 天回测窗口(跟粗筛/精扫一致)
FEE           = 0.0005

# E04 阈值(来自精扫 plateau-best)
TI_TH = -0.180   # TI 分位点 p50
MS_TH =  1.254   # MS 分位点 p50

HOLD_BARS = 12   # 持有 12h 平仓(跟粗筛一致)

# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, ti_th=TI_TH, ms_th=MS_TH):
    """E04 不需要 rolling 指标,直接返回 df 副本。"""
    return df.copy()

# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_taker_intensity, fetch_market_sentiment
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    ti    = fetch_taker_intensity(SYMBOL, INTERVAL, START, END)
    ms    = fetch_market_sentiment(SYMBOL, INTERVAL, START, END)
    df = kline.copy()
    df = df.join(ti.rename(columns={"alpha": "TI"}), how='left')
    df = df.join(ms.rename(columns={"alpha": "MS"}), how='left')
    df["TI"] = df["TI"].ffill()
    df["MS"] = df["MS"].ffill()
    df['ti_sign'] = (df['TI'] > 0).astype(int) - (df['TI'] < 0).astype(int)  # ±1
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
# E04 短方向: 触发 TI > ti_th & ti_sign > 0 & MS > ms_th,持仓 -1 满仓 12 根
def compute_signals(df, ti_th=TI_TH, ms_th=MS_TH, hold_bars=HOLD_BARS):
    from lib.strategy import hold_n_bars
    trigger = (df['TI'] > ti_th) & (df['ti_sign'] > 0) & (df['MS'] > ms_th)
    return hold_n_bars(trigger, df.index, hold_bars, side=-1.0)

if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
