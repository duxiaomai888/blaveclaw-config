# Strategy: BTC MA(7/14) Long
# Type:     A (single symbol, signal-based)
# Symbol:   BTCUSDT
# Interval: 1h
# Logic:    Long when MA7 > MA14 (golden cross), flat otherwise.
#           Position: +1 when long, 0 when flat.
# Window:   1-year backtest (2025-06-25 → 2026-06-25)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
MODE          = "backtest"
STRATEGY_NAME = "btc_ma_7_14_long"
SYMBOL        = "BTCUSDT"
EXCHANGE      = "binance"
INTERVAL      = "1h"
START         = "2025-06-25"
END           = "2026-06-25"
FEE           = 0.0005

MA_FAST = 7
MA_SLOW = 14
WARMUP  = MA_SLOW


# ── indicators ────────────────────────────────────────────────────────────────
def _add_indicators(df, fast=MA_FAST, slow=MA_SLOW):
    df = df.copy()
    df['MA_F'] = df['Close'].rolling(fast).mean()
    df['MA_S'] = df['Close'].rolling(slow).mean()
    return df


# ── fetch_data ────────────────────────────────────────────────────────────────
def fetch_data(hdrs):
    from lib.data import fetch_kline
    return fetch_kline(SYMBOL, INTERVAL, START, END)


# ── compute_signals ───────────────────────────────────────────────────────────
def compute_signals(df, fast=MA_FAST, slow=MA_SLOW):
    import pandas as pd
    import numpy as np
    df     = _add_indicators(df, fast, slow)
    signal = pd.Series(np.nan, index=df.index)
    signal[df['MA_F'] > df['MA_S']] = 1.0   # long
    signal[df['MA_F'] <= df['MA_S']] = 0.0  # flat
    return signal


if __name__ == '__main__':
    from lib.runner import run
    # Telegram optional — skip if openclaw config missing
    send = None
    try:
        from lib.notify import make_sender
        send = make_sender()
    except FileNotFoundError:
        pass
    run(locals(), fetch_data, compute_signals, send)