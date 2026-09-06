# Parameter scan for DOGE Taker Intensity 24h Short

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import numpy as np
from lib.param_scan import scan_grid, find_plateau, plot_heatmap

def main():
    from strategies.doge_ti_24h_short.strategy import fetch_data, compute_signals, WARMUP
    df = fetch_data(None)
    print(f"Data shape: {df.shape}, date range: {df.index[0]} → {df.index[-1]}")

    ROW_VALS = [-0.5, -0.3, -0.1, 0.0, 0.1, 0.2, 0.3]
    COL_VALS = [0.0, 0.3, 0.5, 0.7, 1.0]
    valid_fn = lambda r, c: c > r

    print(f"\nScanning {len(ROW_VALS)} × {len(COL_VALS)} combos...")
    grid = scan_grid(df, compute_signals, ROW_VALS, COL_VALS,
                     row_param='entry_th', col_param='exit_th', valid_fn=valid_fn)

    best_idx, nbr_mean, best_row, best_col = find_plateau(grid, ROW_VALS, COL_VALS)
    best_sharpe = grid[best_idx]

    print(f"\nBest params: entry_th={best_row:.2f}, exit_th={best_col:.2f}")
    print(f"Best Sharpe: {best_sharpe:.3f}")
    print(f"\nGrid (Sharpe):")
    for i, r in enumerate(ROW_VALS):
        row_str = " ".join(f"{s:6.3f}" if not np.isnan(s) else "  n/a " for s in grid[i])
        print(f"  entry={r:5.2f} | {row_str}")

    plot_heatmap(grid, ROW_VALS, COL_VALS, best_idx,
                 row_label='entry_th', col_label='exit_th',
                 title='DOGE Taker Intensity 24h Short — Sharpe by Entry/Exit',
                 output_path='strategies/doge_ti_24h_short/heatmap.png')

if __name__ == '__main__':
    main()