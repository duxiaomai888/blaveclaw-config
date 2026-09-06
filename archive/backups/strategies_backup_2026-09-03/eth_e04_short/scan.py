"""
E04 SHORT 30d heatmap - 用 lib/param_scan 精扫 ti_th × ms_th
ETHUSDT 1h, 30d, hold=12h
Rule E04 (TI+板块共振):
  - 文档方向 long:  TI > ti_th & ti_sign > 0 & MS > 1
  - 短方向(实际最优): TI < -ti_th & ti_sign < 0 & MS < -ms_th
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.data import (
    fetch_kline, fetch_holder_concentration, fetch_taker_intensity,
    fetch_market_sentiment,
)
from lib.param_scan import scan_grid, find_plateau, plot_heatmap

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL   = 'ETHUSDT'
INTERVAL = '1h'
HOLD     = 12
FEE      = 0.0005
DAYS     = 30
END      = datetime(2026, 6, 7)
START    = END - timedelta(days=DAYS)
START_S  = START.strftime('%Y-%m-%d')
END_S    = END.strftime('%Y-%m-%d')

load_dotenv()
hdrs = {'api-key': os.environ['blave_api_key'], 'secret-key': os.environ['blave_secret_key']}

# ── Load data ────────────────────────────────────────────────────────────────
print(f"Fetching {SYMBOL} {INTERVAL} {DAYS}d ({START_S} ~ {END_S})...")
kl = fetch_kline(SYMBOL, INTERVAL, START_S, END_S)
ti = fetch_taker_intensity(SYMBOL, INTERVAL, START_S, END_S)
ms = fetch_market_sentiment(SYMBOL, INTERVAL, START_S, END_S)

df = kl.copy()
df['TI'] = ti['alpha'].ffill()
df['MS'] = ms['alpha'].ffill()
df['ti_sign'] = np.sign(df['TI'])
print(f"Bars: {len(df)}, BH: {((df['Close'].iloc[-1]/df['Close'].iloc[0]-1)*100):+.2f}%")

# ── Define short-direction compute_signals ───────────────────────────────────
def compute_signals_short(df, ti_th, ms_th=1):
    """
    E04 短方向:
      触发(catalog 原条件,不改): TI > ti_th & ti_sign > 0 & MS > ms_th
      仓位方向: -1 (short,反向持仓)
      入场: 下一根 K 线
      持仓: HOLD 根
    """
    n = len(df)
    pos = np.zeros(n)
    trigger = (df['TI'] > ti_th) & (df['ti_sign'] > 0) & (df['MS'] > ms_th)
    i = 0
    while i < n - HOLD - 1:
        if trigger.iloc[i]:
            s = min(i + 1, n - 1)
            e = min(s + HOLD, n)
            pos[s:e] = -1.0  # short = -1
            i = e
        else:
            i += 1
    return pd.Series(pos, index=df.index)

# ── Threshold grid ───────────────────────────────────────────────────────────
# ti_th: 用 |TI| 的分位点(catalog 里 quantile_50_95 的实际值范围)
# ms_th: MS 阈值(catalog 写死 1,这里扫一下)
TI_THS = np.round(np.percentile(df['TI'].dropna(), [50, 60, 70, 80, 85, 90, 95]), 3)
MS_THS = np.round(np.percentile(df['MS'].dropna(), [50, 60, 70, 80, 85, 90, 95]), 3)
print(f"TI thresholds (p50-95): {list(TI_THS)}")
print(f"MS thresholds (p50-95): {list(MS_THS)}")
print(f"Total combos: {len(TI_THS)} × {len(MS_THS)} = {len(TI_THS)*len(MS_THS)}")

# ── Scan: 2D grid via lib.param_scan.scan_grid ──────────────────────────────
# scan_grid 需要 compute_signals 接受 entry_th / exit_th kwargs,我们适配一下
def compute_for_grid(df, entry_th, exit_th):
    # 在 E04 短方向: entry_th=ti_th, exit_th=ms_th
    return compute_signals_short(df, entry_th, exit_th)

print("\nScanning 2D Sharpe grid...")
grid = scan_grid(
    df, compute_for_grid,
    list(TI_THS), list(MS_THS),
    row_param='entry_th', col_param='exit_th',
    valid_fn=lambda r, c: True,  # E04 没有 row>col 约束
    fee=FEE,
)

# ── Find plateau-best ────────────────────────────────────────────────────────
best_idx, _, best_row, best_col, best_sharpe = find_plateau(grid, list(TI_THS), list(MS_THS))
print(f"\n=== Plateau-Best ===")
print(f"  TI_TH = {best_row:.3f}  (触发: TI > {best_row:.3f})")
print(f"  MS_TH = {best_col:.3f}  (触发: MS > {best_col:.3f})")
print(f"  Sharpe = {best_sharpe:.3f}")

# ── Heatmap ──────────────────────────────────────────────────────────────────
out = f"strategies/eth_e04_short/heatmap.png"
plot_heatmap(
    grid,
    list(TI_THS),
    list(MS_THS),
    best_idx,
    row_label="|TI threshold| (TI < -th)",
    col_label="|MS threshold| (MS < -th)",
    title=f"E04 SHORT Sharpe Heatmap | {SYMBOL} {INTERVAL} {DAYS}d | hold={HOLD}",
    output_path=out
)
print(f"\nHeatmap saved: {out}")

# ── Print grid ───────────────────────────────────────────────────────────────
print("\nGrid (Sharpe, row=TI, col=MS):")
print("       " + " ".join(f"{m:>6.2f}" for m in MS_THS))
for i, ti_v in enumerate(TI_THS):
    row_str = " ".join(f"{grid[i,j]:>6.2f}" if not np.isnan(grid[i,j]) else "  n/a " for j in range(len(MS_THS)))
    print(f"  {ti_v:>5.2f} | {row_str}")
