# Parameter scan for BTC A05 SHORT
# Scans hc_th for A05 rule (HC < -th & hc_delta > 0)
# 模板: 参考 strategies/btc_ti_24h_long/scan.py
# 单参数扫描:ROW = hc_th 候选,COL = 占位 [-1.0](A05 没有 exit 阈值)

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

    # Import strategy(模板: 照 btc_ti_24h_long/scan.py)
    from strategies.btc_a05_short.strategy import fetch_data, compute_signals

    df = fetch_data(hdrs)
    print(f"Data shape: {df.shape}, date range: {df.index[0]} → {df.index[-1]}")

    # Scan grid: hc_th (row) vs 占位 (col)
    # 围绕粗筛最优 1.545 加密
    ROW_VALS = [1.0, 1.2, 1.3, 1.4, 1.5, 1.545, 1.6, 1.7, 1.8, 2.0, 2.5]
    COL_VALS = [-1.0]   # 占位,A05 没有 exit 阈值

    # A05 没有 row>col 约束(单参数)
    valid_fn = lambda r, c: True

    print(f"\nScanning {len(ROW_VALS)} × {len(COL_VALS)} = {len(ROW_VALS)*len(COL_VALS)} combos...")
    grid = scan_grid(
        df, compute_signals, ROW_VALS, COL_VALS,
        row_param='entry_th', col_param='exit_th',
        valid_fn=valid_fn,
        fee=0.0005,
    )

    best_idx, nbr_mean, best_row, best_col, best_sharpe = find_plateau(grid, ROW_VALS, COL_VALS, window=1)

    print(f"\n=== Plateau-Best ===")
    print(f"  hc_th    = {best_row:.3f}")
    print(f"  Sharpe     = {best_sharpe:.3f}")
    # best_idx 是 (row, col) tuple,nbr_mean 是 2D 数组
    print(f"  (邻域平均 = {nbr_mean[best_idx[0], best_idx[1]]:.3f})")
    print(f"\n=== Argmax (单点最高) ===")
    argmax_flat = np.nanargmax(grid)
    argmax_i, argmax_j = argmax_flat // len(COL_VALS), argmax_flat % len(COL_VALS)
    argmax_row = ROW_VALS[argmax_i]
    print(f"  hc_th    = {argmax_row:.3f}")
    print(f"  Sharpe     = {float(grid[argmax_i, argmax_j]):.3f}")

    # 打印 1D grid
    print(f"\nGrid (Sharpe, 单参数维度):")
    print(f"  hc_th    | Sharpe")
    print(f"  ----------+--------")
    for i, r in enumerate(ROW_VALS):
        s = grid[i, 0]
        marker = "  <-- plateau-best" if r == best_row else ("  <-- argmax" if r == argmax_row else "")
        print(f"  {r:>8.3f} | {s:>+7.3f}{marker}")

    # Heatmap(单列走标准 plot_heatmap)
    output_path = 'strategies/btc_a05_short/heatmap.png'
    plot_heatmap(
        grid, ROW_VALS, COL_VALS, best_idx,
        row_label='hc_th', col_label='(placeholder)',
        title=f'BTC A05 SHORT — Sharpe by hc_th | 180d 1h',
        output_path=output_path
    )
    print(f"\nHeatmap saved: {output_path}")


if __name__ == '__main__':
    main()
