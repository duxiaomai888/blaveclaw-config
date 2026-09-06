# Strategy: BTC Taker Intensity 24h Long
# Type:     A (single symbol, signal-based)
# Symbol:   BTCUSDT
# Interval: 1h
# Logic:    Long when Taker Intensity (24h) crosses above threshold, flat below exit threshold
# Position: Risk parity — scaled by inverse realized vol to equalize portfolio contribution
#
# Best params (from scan.py plateau-best):
#   ENTRY_TH=-0.50, EXIT_TH=-1.00, 90d BTCUSDT 1h

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "btc_ti_24h_long"
SYMBOL        = "BTCUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2022-01-01"
END           = "2026-05-21"      # fixed for cache-hit reproducibility
FEE           = 0.0005

# Taker Intensity thresholds
ENTRY_TH  = -0.50   # TI > ENTRY_TH → long
EXIT_TH   = -1.00   # TI < EXIT_TH  → exit

# Risk parity (vol targeting)
VOL_TARGETING    = True
TARGET_VOL       = 0.30           # 30% annualized portfolio vol target
VOL_LOOKBACK     = 720            # 720 × 1h bars ≈ 30 days
PERIODS_PER_YEAR = 8760           # 1h bars per year
VOL_CAP          = 2.0            # max vol-scaling multiplier

WARMUP = VOL_LOOKBACK            # bars to skip (warm-up for realized_vol)


# ── indicators ──────────────────────────────────────────────────────────────────
def _add_indicators(df, entry_th=ENTRY_TH, exit_th=EXIT_TH):
    from lib.strategy import add_realized_vol
    df = df.copy()
    # Taker Intensity 24h stored as 'alpha'
    if 'alpha' in df.columns:
        df['TI'] = df['alpha']
    add_realized_vol(df, lookback=VOL_LOOKBACK, periods_per_year=PERIODS_PER_YEAR)
    return df


# ── fetch_data ─────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline, fetch_taker_intensity
    # 1h kline
    kline = fetch_kline(SYMBOL, INTERVAL, START, END)
    # Taker Intensity with 24h timeframe on 1h kline
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