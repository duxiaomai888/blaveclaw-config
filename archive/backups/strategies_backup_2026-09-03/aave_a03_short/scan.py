# Parameter scan for AAVE A03 SHORT
# Scans hc_th for A03 rule (HC < -hc_th & hc_delta < 0)
# 模板: 参考 strategies/eth_a08_short/scan.py
# 单参数扫描: ROW = hc_th 候选, COL = 占位 [-1.0]
#
# ⚠️ 过拟合警告: 本策略窗口 30d(2026-05-12 ~ 2026-06-11),BH -38.18%
#    单边下跌市场 → short 方向天然占优,精扫极可能过拟合
#    对比参考 strategies/eth_e04_short/ 同样是 30d 精扫 → OOS 崩盘
#    建议: 精扫完后必须跑 validate_strategy.py(90d OOS)看是否过关

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

    from strategies.aave_a03_short.strategy import fetch_data, compute_signals

    df = fetch_data(hdrs)
    print(f"Data shape: {df.shape}, date range: {df.index[0]} → {df.index[-1]}")

    # hc_th 候选: 围绕粗筛最优 0.443 加密(quantile 范围 0.05~0.95)
    # 用绝对阈值扫描,避免 quantile 在小样本上不稳定
    ROW_VALS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.443, 0.50, 0.60, 0.70, 0.80, 1.00]
    COL_VALS = [-1.0]   # 占位,A03 没有 exit 阈值

    valid_fn = lambda r, c: True   # 单参数无约束

    print(f"\nScanning {len(ROW_VALS)} × {len(COL_VALS)} = {len(ROW_VALS)*len(COL_VALS)} combos...")
    grid = scan_grid(
        df, compute_signals, ROW_VALS, COL_VALS,
        row_param='hc_th', col_param='exit_th',
        valid_fn=valid_fn,
        fee=0.0005,
    )

    best_idx, nbr_mean, best_row, best_col, best_sharpe = find_plateau(grid, ROW_VALS, COL_VALS, window=1)

    print(f"\n=== Plateau-Best ===")
    print(f"  hc_th   = {best_row:.3f}")
    print(f"  Sharpe  = {best_sharpe:.3f}")
    print(f"  (邻域平均 = {nbr_mean[best_idx[0], best_idx[1]]:.3f})")
    print(f"\n=== Argmax (单点最高) ===")
    argmax_flat = np.nanargmax(grid)
    argmax_i, argmax_j = argmax_flat // len(COL_VALS), argmax_flat % len(COL_VALS)
    argmax_row = ROW_VALS[argmax_i]
    print(f"  hc_th   = {argmax_row:.3f}")
    print(f"  Sharpe  = {float(grid[argmax_i, argmax_j]):.3f}")

    print(f"\nGrid (Sharpe, 单参数维度):")
    print(f"  hc_th | Sharpe")
    print(f"  ------+--------")
    for i, r in enumerate(ROW_VALS):
        s = grid[i, 0]
        marker = "  <-- plateau-best" if r == best_row else ("  <-- argmax" if r == argmax_row else "")
        print(f"  {r:>6.3f} | {s:>+7.3f}{marker}")

    output_path = 'strategies/aave_a03_short/heatmap.png'
    plot_heatmap(
        grid, ROW_VALS, COL_VALS, best_idx,
        row_label='hc_th', col_label='(placeholder)',
        title=f'AAVE A03 SHORT - Sharpe by hc_th | 30d 1h',
        output_path=output_path
    )
    print(f"\nHeatmap saved: {output_path}")


if __name__ == '__main__':
    main()
