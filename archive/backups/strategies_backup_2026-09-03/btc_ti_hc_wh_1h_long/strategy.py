# Strategy: BTC TI+HC+WH 1h Long with Risk Parity
# Type:     A (single symbol, signal-based)
# Symbol:   BTCUSDT
# Interval: 1h
# Logic:    Long when TI>ti_th AND HC>hc_th AND WH>wh_th, flat otherwise
#           Risk parity: scale position by inverse realized volatility
#
# Best params (from scan.py plateau-best):
#   TI_TH=-1.741, HC_TH=1.020, WH_TH=-1.625, 90d BTCUSDT 1h

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ───────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "btc_ti_hc_wh_1h_long"
SYMBOL        = "BTCUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2023-01-01"
END           = "2026-05-21"   # 固定日期保证缓存命中
FEE = 0.0005

# 风险平价参数
TARGET_VOL    = 0.30   # 年化目标波动率 30%
VOL_CAP       = 2.0    # 最大缩放倍数
VOL_LOOKBACK  = 720   # 1h周期，30天 ≈ 720 bars

# 默认阈值（已优化）
TI_TH = -1.741
HC_TH  = 1.020
WH_TH  = -1.625

# WARMUP: 最长滚动窗口
WARMUP = VOL_LOOKBACK   # 720 bars = 30天


# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df):
    from lib.data import (
        fetch_taker_intensity,
        fetch_holder_concentration,
        fetch_whale_hunter,
    )
    df = df.copy()

    # Fetch三个alpha指标（24h timeframe）
    ti = fetch_taker_intensity(SYMBOL, INTERVAL, START, END, timeframe='24h')
    hc = fetch_holder_concentration(SYMBOL, INTERVAL, START, END)
    wh = fetch_whale_hunter(SYMBOL, INTERVAL, START, END, timeframe='24h')

    # Join到kline index，ffill处理缺失
    df = df.join(ti.rename(columns={'alpha': 'TI'}))
    df = df.join(hc.rename(columns={'alpha': 'HC'}))
    df = df.join(wh.rename(columns={'alpha': 'WH'}))

    df['TI'] = df['TI'].ffill()
    df['HC'] = df['HC'].ffill()
    df['WH'] = df['WH'].ffill()

    # 已实现波动率（风险平价用）
    from lib.strategy import add_realized_vol
    add_realized_vol(df, lookback=VOL_LOOKBACK)

    return df


# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline
    df = fetch_kline(SYMBOL, INTERVAL, START, END)
    return _add_indicators(df)


# ── compute_signals ───────────────────────────────────────────────────────────
def compute_signals(df, ti_th=None, hc_th=None, wh_th=None):
    """
    三指标AND组合信号 +风险平价缩放

    参数:
        ti_th: TI入场阈值（默认 module-level TI_TH）
        hc_th: HC入场阈值（默认 module-level HC_TH）
        wh_th: WH入场阈值（默认 module-level WH_TH）

    返回:
        signal: 缩放后的仓位（风险平价）
    """
    import pandas as pd, numpy as np
    from lib.strategy import apply_vol_scaling

    # 使用传入参数或默认值
    ti_t = ti_th if ti_th is not None else TI_TH
    hc_t = hc_th if hc_th is not None else HC_TH
    wh_t = wh_th if wh_th is not None else WH_TH

    # 三指标AND组合：全部高于阈值才入场
    cond_long = (df['TI'] > ti_t) & (df['HC'] > hc_t) & (df['WH'] > wh_t)

    signal = pd.Series(np.nan, index=df.index)
    signal[cond_long] = 1.0    # 入场做多
    signal[~cond_long] = 0.0   # 其他情况平仓

    # 风险平价缩放
    signal = apply_vol_scaling(signal, df, target_vol=TARGET_VOL, vol_cap=VOL_CAP)

    return signal


if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
