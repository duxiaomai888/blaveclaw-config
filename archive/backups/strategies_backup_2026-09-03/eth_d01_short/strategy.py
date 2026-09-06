# Strategy: ETH D01 SHORT
# Type:     A (single symbol, signal-based)
# Symbol:   ETHUSDT
# Interval: 1h
# Logic:    Short when HC > 1.703 (主力集中做多) AND MS < -1.369 (散户极度恐慌)
#           业务逻辑: D01 "主力在散户恐慌时反向吸筹" - 主力接盘失败 -> 做空反弹
# Position: Risk parity — scaled by inverse realized vol
#
# Best params (from scan_heatmap.py plateau-best):
#   HC > 1.703, MS < -1.369, 90d ETHUSDT 1h

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "eth_d01_short"
SYMBOL        = "ETHUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2026-03-08"
END           = "2026-06-06"   # 90 天回测窗口
FEE           = 0.0005

# D01 阈值(来自 90 天稳健扫描 - 平台最佳)
HC_TH = 1.703    # 主力筹码集中度 > p95
MS_TH = -1.369   # 市场情绪 < p15 (散户恐慌)

HOLD_BARS = 12   # 持有 12h 平仓

# 风险平价
VOL_TARGETING    = True
TARGET_VOL       = 0.30
VOL_LOOKBACK     = 168     # 7 天(缩短以保留更多交易信号)
PERIODS_PER_YEAR = 8760
VOL_CAP          = 2.0

WARMUP = VOL_LOOKBACK

# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, hc_th=HC_TH, ms_th=MS_TH):
    from lib.strategy import add_realized_vol
    df = df.copy()
    add_realized_vol(df, lookback=VOL_LOOKBACK, periods_per_year=PERIODS_PER_YEAR)
    return df

# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_holder_concentration, fetch_market_sentiment
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    hc = fetch_holder_concentration(SYMBOL, INTERVAL, START, END)
    ms = fetch_market_sentiment(SYMBOL, INTERVAL, START, END)
    df = kline.join(hc.rename(columns={"alpha": "HC"})).join(ms.rename(columns={"alpha": "MS"}))
    df["HC"] = df["HC"].ffill()
    df["MS"] = df["MS"].ffill()
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
# D01 规则: HC > hc_th AND MS < ms_th -> 主力偷偷买 + 散户恐慌 -> 主力接盘失败 -> 做空
# 出场: 持有 hold_bars 根 K 线后平仓
def compute_signals(df, hc_th=HC_TH, ms_th=MS_TH, hold_bars=12):
    from lib.strategy import hold_n_bars, apply_vol_scaling
    trigger = (df['HC'] > hc_th) & (df['MS'] < ms_th)
    pos = hold_n_bars(trigger, df.index, hold_bars, side=-1.0)
    return apply_vol_scaling(pos, df, target_vol=TARGET_VOL, vol_cap=VOL_CAP)

if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())
