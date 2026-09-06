# 3D Parameter Scan: BTC TI+HC+WH Short
# 扫描三个阈值的最佳组合（做空）

import sys, time, warnings
warnings.filterwarnings('ignore', category=FutureWarning)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import itertools
from dotenv import dotenv_values
from lib.analysis import precise_pnl, compute_stats
import strategy as s

env = dotenv_values()
hdrs = {'api-key': env.get('blave_api_key', ''), 'secret-key': env.get('blave_secret_key', '')}

# ── 加载数据 ──────────────────────────────────────────────────────────────────
t0 = time.time()
df = s.fetch_data(hdrs)
print(f"数据加载: {time.time()-t0:.1f}s  ({len(df):,} bars)\n")

# ── 基于百分位确定扫描范围 ───────────────────────────────────────────────────
def percentile_vals(series, n=5):
    """生成n个百分位候选值（5%-95%范围）"""
    lo, hi = np.percentile(series.dropna(), [5, 95])
    return np.round(np.linspace(lo, hi, n), 3)

ti_vals = percentile_vals(df['TI'], n=5)
hc_vals = percentile_vals(df['HC'], n=5)
wh_vals = percentile_vals(df['WH'], n=5)

print(f"TI候选值 ({len(ti_vals)}): {list(ti_vals)}")
print(f"HC候选值 ({len(hc_vals)}): {list(hc_vals)}")
print(f"WH候选值 ({len(wh_vals)}): {list(wh_vals)}")
print(f"总组合数: {len(ti_vals)*len(hc_vals)*len(wh_vals)}\n")

# ── 3D扫描 ──────────────────────────────────────────────────────────────────
t1 = time.time()
results = []

for ti_th, hc_th, wh_th in itertools.product(ti_vals, hc_vals, wh_vals):
    # 计算信号
    sig = s.compute_signals(df, ti_th=ti_th, hc_th=hc_th, wh_th=wh_th)
    if isinstance(sig, tuple):
        sig = sig[0]

    # 提取仓位
    pos = sig.ffill().fillna(0).values
    n = len(df)

    # 跳过warmup
    if n <= s.WARMUP:
        continue

    # 计算PnL
    w_curr = np.empty(n); w_curr[0] = 0.0; w_curr[1:] = pos[:-1]
    w_prev = np.zeros(n)
    if n >= 2: w_prev[2:] = pos[:-2]

    pf_ret, *_ = precise_pnl(
        df['Close'].values, df['Open'].values,
        w_curr, w_prev, np.zeros(n, dtype=bool), s.FEE
    )

    # 计算统计（跳过warmup）
    pf_ret_warmup = pf_ret[s.WARMUP:]
    idx_warmup = df.index[s.WARMUP:]

    if len(pf_ret_warmup) == 0:
        continue

    sharpe, *_ = compute_stats(pf_ret_warmup, idx_warmup)

    results.append({
        'ti_th': ti_th,
        'hc_th': hc_th,
        'wh_th': wh_th,
        'sharpe': sharpe if np.isfinite(sharpe) else -999
    })

scan_time = time.time() - t1
print(f"扫描耗时: {scan_time:.1f}s")

# ── 找最优 ────────────────────────────────────────────────────────────────────
best = max(results, key=lambda x: x['sharpe'])
print(f"\n=== 最优参数 ===")
print(f"  TI_TH = {best['ti_th']:.3f}")
print(f"  HC_TH  = {best['hc_th']:.3f}")
print(f"  WH_TH  = {best['wh_th']:.3f}")
print(f"  Sharpe = {best['sharpe']:.3f}")

# ── Top 10 ───────────────────────────────────────────────────────────────────
print(f"\n=== Top 10 参数组合 ===")
sorted_results = sorted(results, key=lambda x: x['sharpe'], reverse=True)[:10]
for i, r in enumerate(sorted_results, 1):
    print(f"  {i}. TI={r['ti_th']:.3f} HC={r['hc_th']:.3f} WH={r['wh_th']:.3f}  Sharpe={r['sharpe']:.3f}")

# ── 切片热力图（固定WH）────────────────────────────────────────────────────
print("\n生成热力图中...")

wh_vals_list = list(wh_vals)

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    from matplotlib.patches import Rectangle

    n_wh = len(wh_vals_list)
    fig, axes = plt.subplots(1, n_wh, figsize=(5*n_wh, 5))

    if n_wh == 1:
        axes = [axes]

    for idx, wh_fixed in enumerate(wh_vals_list):
        # 构建2D网格
        grid = np.full((len(ti_vals), len(hc_vals)), np.nan)
        for r in results:
            if r['wh_th'] == wh_fixed:
                i = list(ti_vals).index(r['ti_th'])
                j = list(hc_vals).index(r['hc_th'])
                grid[i, j] = r['sharpe']

        ax = axes[idx]
        masked = np.ma.masked_invalid(grid)
        valid = grid[~np.isnan(grid)]

        if len(valid) > 0:
            vmin = min(np.nanmin(valid), -0.01)
            vmax = max(np.nanmax(valid), 0.01)
        else:
            vmin, vmax = -1, 1

        norm = TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
        im = ax.imshow(masked, cmap='RdYlGn', aspect='auto', origin='upper', norm=norm)

        ax.set_xticks(range(len(hc_vals)))
        ax.set_xticklabels([f'{v:.2f}' for v in hc_vals], fontsize=8)
        ax.set_yticks(range(len(ti_vals)))
        ax.set_yticklabels([f'{v:.2f}' for v in ti_vals], fontsize=8)
        ax.set_xlabel('HC_TH')
        ax.set_ylabel('TI_TH')
        ax.set_title(f'WH_TH = {wh_fixed:.2f}')

        # 标注数值
        for i in range(len(ti_vals)):
            for j in range(len(hc_vals)):
                v = grid[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f'{v:.2f}', ha='center', va='center', fontsize=6)

        plt.colorbar(im, ax=ax, label='Sharpe')

        # 高亮最优格
        best_i = list(ti_vals).index(best['ti_th'])
        best_j = list(hc_vals).index(best['hc_th'])
        if best['wh_th'] == wh_fixed:
            ax.add_patch(Rectangle((best_j-0.5, best_i-0.5), 1, 1,
                                   linewidth=2.5, edgecolor='white', facecolor='none'))

    plt.suptitle(f'{s.STRATEGY_NAME} — 3D Parameter Scan (Short)\n'
                 f'Best: TI={best["ti_th"]:.3f} HC={best["hc_th"]:.3f} WH={best["wh_th"]:.3f} Sharpe={best["sharpe"]:.3f}',
                 fontsize=11)
    plt.tight_layout()
    plt.savefig(f'strategies/{s.STRATEGY_NAME}/heatmap_3d.png', dpi=150, bbox_inches='tight')
    plt.close()
    print(f"热力图已保存: strategies/{s.STRATEGY_NAME}/heatmap_3d.png")
except (OSError, ValueError, KeyError, RuntimeError) as e:
    # File write / data shape / matplotlib rendering error
    print(f"热力图生成失败: {e}")
