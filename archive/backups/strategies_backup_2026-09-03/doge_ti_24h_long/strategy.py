# Strategy: DOGE Taker Intensity 24h Long
# Type:     A (single symbol, signal-based)
# Symbol:   DOGEUSDT
# Interval: 1h
# Logic:    Long when Taker Intensity (24h) crosses above threshold, flat below exit threshold
# Position: Risk parity — scaled by inverse realized vol
#
# Best params (from scan.py plateau-best):
#   ENTRY_TH=0.30, EXIT_TH=-1.00, 90d DOGEUSDT 1h

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "doge_ti_24h_long"
SYMBOL        = "DOGEUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2022-01-01"
END           = "2026-05-21"
FEE           = 0.0005

# Taker Intensity thresholds
ENTRY_TH  = 0.30   # TI > ENTRY_TH → long
EXIT_TH   = -1.00  # TI < EXIT_TH  → exit

# Risk parity (vol targeting)
VOL_TARGETING    = True
TARGET_VOL       = 0.30
VOL_LOOKBACK     = 720
PERIODS_PER_YEAR = 8760
VOL_CAP          = 2.0

WARMUP = VOL_LOOKBACK

# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, entry_th=ENTRY_TH, exit_th=EXIT_TH):
    from lib.strategy import add_realized_vol
    df = df.copy()
    if 'alpha' in df.columns:
        df['TI'] = df['alpha']
    add_realized_vol(df, lookback=VOL_LOOKBACK, periods_per_year=PERIODS_PER_YEAR)
    return df

# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_taker_intensity
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    ti = fetch_taker_intensity(SYMBOL, INTERVAL, START, END, timeframe='24h')
    df = kline.join(ti.rename(columns={"alpha": "TI"}))
    df["TI"] = df["TI"].ffill()
    return _add_indicators(df)

# ── compute_signals ────────────────────────────────────────────────────────────
def compute_signals(df, entry_th=ENTRY_TH, exit_th=EXIT_TH):
    from lib.strategy import cross_signal, apply_vol_scaling
    signal = cross_signal(df['TI'], entry_th=entry_th, exit_th=exit_th, side=+1.0)
    return apply_vol_scaling(signal, df, target_vol=TARGET_VOL, vol_cap=VOL_CAP)

if __name__ == '__main__':
    from lib.runner import run
    from lib.notify import make_sender
    run(locals(), fetch_data, compute_signals, make_sender())