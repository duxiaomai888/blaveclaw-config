# Param scan for BTC MA(7/14) Long — 1-year window
# Scans fast=[5..30], slow=[20..120] to check robustness around MA(7,14).
# Best params reported to strategy.py if edge holds.

import sys, time, warnings
warnings.filterwarnings('ignore', category=FutureWarning)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import dotenv_values
from lib.data import fetch_kline
from lib.param_scan import scan_grid, find_plateau, plot_heatmap
import strategy as s

env  = dotenv_values()
hdrs = {'api-key': env.get('blave_api_key', ''), 'secret-key': env.get('blave_secret_key', '')}

# ── 資料一次 ──────────────────────────────────────────────────────────────────
t0 = time.time()
base_df = fetch_kline(s.SYMBOL, s.INTERVAL, s.START, s.END)
print(f"資料載入: {time.time()-t0:.1f}s  ({len(base_df):,} bars)\n")

# ── 掃描範圍 ──────────────────────────────────────────────────────────────────
fast_vals = list(range(5,  31, 2))    # 5,7,9,...,29 (含 7)
slow_vals = list(range(20, 121, 10))  # 20,30,...,120 (含 ~14-120)
warmup    = max(slow_vals)

# ── 參數掃描 ──────────────────────────────────────────────────────────────────
t1   = time.time()
grid = scan_grid(
    base_df, s.compute_signals, fast_vals, slow_vals,
    row_param='fast', col_param='slow',
    fee=s.FEE, warmup=warmup,
    valid_fn=lambda f, sl: f < sl,
)
print(f"掃描耗時: {time.time()-t1:.1f}s")

# ── 最佳參數 ──────────────────────────────────────────────────────────────────
best_idx, _, best_fast, best_slow, best_sharpe = find_plateau(grid, fast_vals, slow_vals)
print(f"最佳參數: MA_FAST={best_fast}, MA_SLOW={best_slow}  Sharpe={grid[best_idx]:.3f}")

# 額外:單獨報 MA(7,14) 結果
import numpy as np
i7  = fast_vals.index(7)
i14 = slow_vals.index(14) if 14 in slow_vals else None
if i14 is not None:
    print(f"MA(7,14)   : Sharpe={grid[i7, i14]:.3f}")
else:
    print("(14 不在 slow_vals,額外插入掃)")
    # quick re-eval at slow=14
    from lib.analysis import precise_pnl, compute_stats
    sig = s.compute_signals(base_df, fast=7, slow=14)
    df = base_df.iloc[warmup:].copy()
    sig = sig.iloc[warmup:]
    pos = sig.ffill().fillna(0).values
    pnl = precise_pnl(df, pos, fee=s.FEE)
    st  = compute_stats(pnl)
    print(f"MA(7,14)   : Sharpe={st['sharpe']:.3f}  Ret={st['total_ret']*100:.2f}%  N={st['n_trades']}  MDD={st['mdd']*100:.2f}%")

plot_heatmap(
    grid, fast_vals, slow_vals,
    best_idx=best_idx,
    row_label='MA Fast', col_label='MA Slow',
    title=f'{s.STRATEGY_NAME} — Sharpe Grid',
    output_path='strategies/btc_ma_7_14_long/heatmap.png',
)