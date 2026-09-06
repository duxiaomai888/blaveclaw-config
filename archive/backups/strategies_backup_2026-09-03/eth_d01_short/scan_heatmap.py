"""
D01 SHORT 90d heatmap - 用 lib/param_scan.plot_heatmap 生成
ETHUSDT 1h, 90d, hold=12h
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from lib.data import fetch_holder_concentration, fetch_market_sentiment, fetch_kline
from lib.param_scan import plot_heatmap, find_plateau

end = datetime.now().strftime('%Y-%m-%d')
start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
HOLD_BARS = 12
FEE = 0.0005
SYMBOL = 'ETHUSDT'

print(f"Fetching {SYMBOL} 1h 90d...")
hc = fetch_holder_concentration(SYMBOL, '1h', start, end)
ms = fetch_market_sentiment(SYMBOL, '1h', start, end)
kl = fetch_kline(SYMBOL, '1h', start, end)
df = kl.join(hc.rename(columns={'alpha': 'HC'})).join(ms.rename(columns={'alpha': 'MS'}))
df['HC'] = df['HC'].ffill()
df['MS'] = df['MS'].ffill()

# 阈值网格(分位数)
HC_THS = np.round(np.percentile(df['HC'].dropna(), [70, 75, 80, 85, 90, 95]), 3)
MS_THS = np.round(np.percentile(df['MS'].dropna(), [5, 10, 15, 20, 30, 40, 50]), 3)
print(f"HC thresholds (p70-95): {list(HC_THS)}")
print(f"MS thresholds (p5-50): {list(MS_THS)}")

# 扫描
n = len(df)
close = df['Close'].values
grid = np.full((len(HC_THS), len(MS_THS)), np.nan)

for i, hc_t in enumerate(HC_THS):
    for j, ms_t in enumerate(MS_THS):
        entry = (df['HC'] > hc_t) & (df['MS'] < ms_t)
        pos = np.zeros(n)
        k = 0
        while k < n - HOLD_BARS - 1:
            if entry.iloc[k]:
                pos[k+1:k+1+HOLD_BARS] = -1.0
                k += HOLD_BARS
            else:
                k += 1
        ret = np.zeros(n)
        for t in range(1, n):
            if pos[t] != 0 and close[t-1] > 0:
                ret[t] = pos[t] * (close[t] - close[t-1]) / close[t-1] - FEE * abs(pos[t] - pos[t-1])
        ret_trim = ret[1:]
        if len(ret_trim) > 10 and np.std(ret_trim) > 0:
            grid[i, j] = np.mean(ret_trim) / np.std(ret_trim) * np.sqrt(365 * 24)

# 找最佳
best_idx, _, best_row, best_col, best_sharpe = find_plateau(grid, HC_THS, MS_THS)
print(f"\nBest (plateau): HC>{best_row:.3f} & MS<{best_col:.3f} (Sharpe={best_sharpe:.3f})")

# 生成 heatmap
out = f"strategies/eth_d01_short/heatmap.png"
plot_heatmap(
    grid,
    HC_THS,
    MS_THS,
    best_idx,
    row_label="HC threshold (>)",
    col_label="MS threshold (<)",
    title=f"D01 SHORT Sharpe Heatmap | {SYMBOL} 1h 90d",
    output_path=out
)
print(f"Heatmap saved: {out}")
