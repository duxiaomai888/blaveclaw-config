# Parameter scan for BTC Taker Intensity 24h Long
# Scans entry/exit thresholds for Taker Intensity

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.param_scan import scan_grid, find_plateau, plot_heatmap
import numpy as np


def main():
    from dotenv import load_dotenv
    import os
    load_dotenv()
    hdrs = {'api-key': os.environ['blave_api_key'], 'secret-key': os.environ['blave_secret_key']}

    # Import strategy
    from strategies.btc_ti_24h_long.strategy import fetch_data, compute_signals, WARMUP

    df = fetch_data(hdrs)
    print(f"Data shape: {df.shape}, date range: {df.index[0]} → {df.index[-1]}")

    # Scan grid: entry_th (row) vs exit_th (col)
    ROW_VALS = [-0.5, -0.3, -0.1, 0.0, 0.1, 0.2, 0.3]
    COL_VALS = [-1.0, -0.7, -0.5, -0.3, -0.1]

    # Valid: entry_th must be > exit_th
    valid_fn = lambda r, c: r > c

    print(f"\nScanning {len(ROW_VALS)} × {len(COL_VALS)} = {len(ROW_VALS)*len(COL_VALS)} combos...")
    grid = scan_grid(
        df, compute_signals, ROW_VALS, COL_VALS,
        row_param='entry_th', col_param='exit_th',
        valid_fn=valid_fn,
    )

    best_idx, nbr_mean, best_row, best_col = find_plateau(grid, ROW_VALS, COL_VALS)
    best_sharpe = grid[best_idx]

    print(f"\nBest params: entry_th={best_row:.2f}, exit_th={best_col:.2f}")
    print(f"Best Sharpe: {best_sharpe:.3f}")
    print(f"\nGrid (Sharpe):")
    for i, r in enumerate(ROW_VALS):
        row_str = " ".join(f"{s:6.3f}" if not np.isnan(s) else "  n/a " for s in grid[i])
        print(f"  entry={r:5.2f} | {row_str}")

    # Plot heatmap
    output_path = 'strategies/btc_ti_24h_long/heatmap.png'
    plot_heatmap(grid, ROW_VALS, COL_VALS, best_idx,
                  row_label='entry_th', col_label='exit_th',
                  title='BTC Taker Intensity 24h — Sharpe by Entry/Exit Threshold',
                  output_path=output_path)


if __name__ == '__main__':
    main()