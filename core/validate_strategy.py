"""
Validate Strategy - 3-Layer Validation Pipeline
================================================
One-stop validation tool for any strategy in `strategies/<name>/`.
Runs three layers of validation in sequence and prints a verdict:

  Layer 1 (IS)  : In-sample backtest on the strategy's own START/END window
                  - Loads strategy.fetch_data() and strategy.compute_signals()
                  - Runs precise PnL via lib.analysis.precise_pnl
                  - Verdict: Sharpe > 2 and n_trades >= 5

  Layer 2 (OOS) : Out-of-sample backtest on a fresh window (default 90 days
                  preceding the IS window)
                  - Same PnL pipeline
                  - Verdict: OOS Sharpe drop < 30% AND OOS Sharpe > 0

  Layer 3 (MCPT): Monte Carlo Permutation Test on IS returns
                  - Calls lib.validation.mcpt(close, position, n=2000)
                  - Verdict: p-value < 0.05

Usage
-----
    # From project root:
    python core/validate_strategy.py strategies/eth_a08_short

    # Custom OOS window length (default 90 days):
    python core/validate_strategy.py strategies/eth_a08_short --oos-days 120

    # Custom MCPT permutations (default 2000):
    python core/validate_strategy.py strategies/eth_a08_short --mcpt-n 5000

Output
------
A 3-row verdict table printed to stdout, plus a JSON file
`<strategy_dir>/validation.json` with the full numerical results.

Reference: AGENTS.md "Step 5 — 3-Layer Validation (强制)"
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import argparse
import importlib.util
from pathlib import Path

import _bootstrap  # noqa: F401  — sys.path setup

from dotenv import load_dotenv
import numpy as np
from datetime import datetime, timedelta

from lib.analysis import precise_pnl, compute_stats
from lib.validation import mcpt

load_dotenv()
HDRS = {'api-key': os.environ['blave_api_key'], 'secret-key': os.environ['blave_secret_key']}


# ── Strategy loader ────────────────────────────────────────────────────────
def load_strategy(strategy_dir):
    """Load strategy.py as a module. Returns the module object."""
    strategy_path = Path(strategy_dir) / 'strategy.py'
    if not strategy_path.exists():
        raise FileNotFoundError(f"strategy.py not found in {strategy_dir}")
    spec = importlib.util.spec_from_file_location('strategy_mod', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Backtest helper (precise PnL on a given window) ────────────────────────
def run_backtest_window(strategy_mod, start, end):
    """
    Run the strategy over [start, end] using its fetch_data/compute_signals.
    Returns dict with sharpe, total_ret, mdd, n_trades, win_rate, pos_array, df.
    """
    # Patch strategy's START/END temporarily to the requested window
    # (most strategies read these as module-level constants in fetch_data)
    original_start = getattr(strategy_mod, 'START', None)
    original_end   = getattr(strategy_mod, 'END', None)
    strategy_mod.START = start
    strategy_mod.END   = end

    try:
        df = strategy_mod.fetch_data(HDRS)
        sig = strategy_mod.compute_signals(df)
        if isinstance(sig, tuple):
            sig = sig[0]
    finally:
        # Restore
        if original_start is not None: strategy_mod.START = original_start
        if original_end is not None:   strategy_mod.END   = original_end

    pos = sig.ffill().fillna(0).values
    n = len(df)
    close_v = df['Close'].values
    open_v  = df['Open'].values

    w_curr = np.zeros(n); w_curr[1:] = pos[:-1]
    w_prev = np.zeros(n)
    if n >= 2: w_prev[2:] = pos[:-2]

    fee = getattr(strategy_mod, 'FEE', 0.0005)
    pf_ret, *_ = precise_pnl(close_v, open_v, w_curr, w_prev,
                              np.zeros(n, dtype=bool), fee)

    sharpe, sortino, omega, mdd_raw, ann_ret = compute_stats(pf_ret, df.index)
    total_ret = float(np.prod(1 + np.nan_to_num(pf_ret)) - 1) * 100
    mdd = abs(mdd_raw) * 100

    n_pos_bars = int((pos != 0).sum())
    hold_bars  = getattr(strategy_mod, 'HOLD_BARS', 12)
    n_trades   = n_pos_bars // max(1, hold_bars)

    # Win rate (trade-level, hold-based)
    trade_rets = []
    for i in range(n - hold_bars - 1):
        if pos[i] != 0:
            ep = close_v[i + 1]
            xp = close_v[i + 1 + hold_bars]
            r = ((xp - ep) / ep - fee) * 100 if pos[i] > 0 else ((ep - xp) / ep - fee) * 100
            trade_rets.append(r)
    win_rate = sum(1 for r in trade_rets if r > 0) / len(trade_rets) * 100 if trade_rets else 0

    return {
        'sharpe': float(sharpe),
        'sortino': float(sortino),
        'omega': float(omega),
        'mdd': float(mdd),
        'total_ret': float(total_ret),
        'ann_ret': float(ann_ret * 100),
        'n_trades': int(n_trades),
        'win_rate': float(win_rate),
        'pos_array': pos,
        'df_index': df.index,
        'close_v': close_v,
        'open_v': open_v,
    }


# ── Layer 1: IS ────────────────────────────────────────────────────────────
def layer_is(strategy_mod):
    start = getattr(strategy_mod, 'START', None)
    end   = getattr(strategy_mod, 'END', None)
    # END=None is the live-strategy idiom (数据拉到当前): treat it as today
    if not start:
        return {'error': 'strategy.py must define START as a YYYY-MM-DD string'}
    if not end:
        from datetime import date
        end = date.today().strftime('%Y-%m-%d')
        print(f"  END=None → using today {end}")
    print(f"  IS window: {start} ~ {end}")
    r = run_backtest_window(strategy_mod, start, end)
    return {
        'window': f"{start} ~ {end}",
        'sharpe': r['sharpe'],
        'sortino': r['sortino'],
        'omega': r['omega'],
        'mdd': r['mdd'],
        'total_ret': r['total_ret'],
        'ann_ret': r['ann_ret'],
        'n_trades': r['n_trades'],
        'win_rate': r['win_rate'],
        'pass_sharpe':  r['sharpe'] > 2.0,
        'pass_trades':  r['n_trades'] >= 5,
        'pass_mdd':     r['mdd'] < 30.0,
        'verdict': '[OK]' if (r['sharpe'] > 2.0 and r['n_trades'] >= 5 and r['mdd'] < 30.0) else '[FAIL]',
        'pos_array': r['pos_array'],
        'close_v':   r['close_v'],
    }


# ── Layer 2: OOS ───────────────────────────────────────────────────────────
def layer_oos(strategy_mod, is_end_date, oos_days):
    """OOS = oos_days ending at is_end_date (uses data strictly before IS start)."""
    is_end_dt   = datetime.strptime(is_end_date, '%Y-%m-%d')
    # IS uses [start, end], so OOS uses [oos_end - oos_days, oos_end]
    # where oos_end = is_end_dt - 1 day (to avoid touching IS data)
    oos_end_dt   = is_end_dt  # use is_end as the boundary
    oos_start_dt = oos_end_dt - timedelta(days=oos_days)
    oos_start = oos_start_dt.strftime('%Y-%m-%d')
    oos_end   = oos_end_dt.strftime('%Y-%m-%d')
    print(f"  OOS window: {oos_start} ~ {oos_end} ({oos_days} days)")
    r = run_backtest_window(strategy_mod, oos_start, oos_end)
    return {
        'window': f"{oos_start} ~ {oos_end}",
        'sharpe': r['sharpe'],
        'sortino': r['sortino'],
        'omega': r['omega'],
        'mdd': r['mdd'],
        'total_ret': r['total_ret'],
        'ann_ret': r['ann_ret'],
        'n_trades': r['n_trades'],
        'win_rate': r['win_rate'],
    }


# ── Layer 3: MCPT ──────────────────────────────────────────────────────────
def layer_mcpt(is_result, n_perm=2000):
    pos = is_result['pos_array']
    close = is_result['close_v']
    print(f"  MCPT: {n_perm} permutations...")
    actual, p_value, dist = mcpt(
        close=close, position=pos, n=n_perm,
        fee=0.0005, target_vol=0.30, max_lev=2.0,
        vol_window=720, periods_per_year=8760,
    )
    return {
        'actual_sharpe': float(actual),
        'p_value': float(p_value),
        'median_dist': float(np.median(dist)),
        'p95_dist':    float(np.percentile(dist, 95)),
        'p99_dist':    float(np.percentile(dist, 99)),
        'n_perm':      n_perm,
        'pass':        p_value < 0.05,
        'verdict':     '[OK]' if p_value < 0.05 else '[FAIL]',
    }


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='3-Layer Strategy Validation (IS + OOS + MCPT)')
    parser.add_argument('strategy_dir', help='策略目录路径, e.g. strategies/eth_a08_short')
    parser.add_argument('--oos-days', type=int, default=90, help='OOS 窗口天数 (default 90)')
    parser.add_argument('--mcpt-n', type=int, default=2000, help='MCPT 置换次数 (default 2000)')
    args = parser.parse_args()

    strategy_dir = Path(args.strategy_dir)
    name = strategy_dir.name

    print("=" * 80)
    print(f"=== 3-Layer Validation | {name} ===")
    print("=" * 80)

    # ── Load ──
    print(f"\n[1/4] Loading strategy: {strategy_dir / 'strategy.py'}")
    mod = load_strategy(strategy_dir)
    print(f"  Strategy: {getattr(mod, 'STRATEGY_NAME', name)}")
    print(f"  Symbol:   {getattr(mod, 'SYMBOL', '?')}")
    print(f"  Interval: {getattr(mod, 'INTERVAL', '?')}")

    # ── Layer 1: IS ──
    print(f"\n[2/4] Layer 1: In-Sample (IS) ...")
    is_r = layer_is(mod)
    if 'error' in is_r:
        print(f"  [FAIL] {is_r['error']}")
        return 1
    print(f"  Sharpe     : {is_r['sharpe']:>+7.3f}  (pass > 2.0: {is_r['pass_sharpe']})")
    print(f"  MDD        : {is_r['mdd']:>+7.2f}%  (pass < 30:  {is_r['pass_mdd']})")
    print(f"  Total ret  : {is_r['total_ret']:>+7.2f}%")
    print(f"  Trades     : {is_r['n_trades']:>4}      (pass >= 5: {is_r['pass_trades']})")
    print(f"  Win rate   : {is_r['win_rate']:>6.1f}%")
    print(f"  Verdict    : {is_r['verdict']}")

    # ── Layer 2: OOS ──
    print(f"\n[3/4] Layer 2: Out-of-Sample (OOS) ...")
    oos_r = layer_oos(mod, is_r['window'].split(' ~ ')[1], args.oos_days)
    is_sharpe = is_r['sharpe']
    oos_sharpe = oos_r['sharpe']
    sharpe_drop_pct = (is_sharpe - oos_sharpe) / is_sharpe * 100 if is_sharpe > 0 else float('inf')
    oos_pass = (oos_sharpe > 0) and (sharpe_drop_pct < 30)
    print(f"  Sharpe     : {oos_sharpe:>+7.3f}  (drop {sharpe_drop_pct:+.1f}% vs IS)")
    print(f"  MDD        : {oos_r['mdd']:>+7.2f}%")
    print(f"  Total ret  : {oos_r['total_ret']:>+7.2f}%")
    print(f"  Trades     : {oos_r['n_trades']:>4}")
    print(f"  Win rate   : {oos_r['win_rate']:>6.1f}%")
    print(f"  Verdict    : {'[OK]' if oos_pass else '[FAIL]'}  (pass: OOS>0 & drop<30%)")

    # ── Layer 3: MCPT ──
    print(f"\n[4/4] Layer 3: MCPT (statistical significance) ...")
    mcpt_r = layer_mcpt(is_r, n_perm=args.mcpt_n)
    print(f"  Actual Sharpe   : {mcpt_r['actual_sharpe']:>+7.3f}")
    print(f"  p-value         : {mcpt_r['p_value']:.4f}  (pass < 0.05: {mcpt_r['pass']})")
    print(f"  Median of dist  : {mcpt_r['median_dist']:>+7.3f}")
    print(f"  95th pct of dist: {mcpt_r['p95_dist']:>+7.3f}")
    print(f"  99th pct of dist: {mcpt_r['p99_dist']:>+7.3f}")
    print(f"  Verdict         : {mcpt_r['verdict']}")

    # ── Summary ──
    print("\n" + "=" * 80)
    print("=== 3-Layer Validation Summary ===")
    print("=" * 80)
    print(f"  {'Layer':<10} {'Status':<8} {'Metric':<22} {'Value':<12}")
    print("  " + "-" * 60)
    print(f"  {'IS':<10} {is_r['verdict']:<8} {'Sharpe':<22} {is_r['sharpe']:>+7.3f}")
    print(f"  {'':<10} {'':<8} {'Total Return %':<22} {is_r['total_ret']:>+7.2f}")
    print(f"  {'':<10} {'':<8} {'N Trades':<22} {is_r['n_trades']:>7}")
    print(f"  {'OOS':<10} {'[OK]' if oos_pass else '[FAIL]':<8} {'Sharpe (drop)':<22} {oos_sharpe:>+7.3f} ({sharpe_drop_pct:+.1f}%)")
    print(f"  {'':<10} {'':<8} {'Total Return %':<22} {oos_r['total_ret']:>+7.2f}")
    print(f"  {'MCPT':<10} {mcpt_r['verdict']:<8} {'p-value':<22} {mcpt_r['p_value']:>9.4f}")
    print(f"  {'':<10} {'':<8} {'Actual Sharpe':<22} {mcpt_r['actual_sharpe']:>+7.3f}")

    all_pass = (is_r['verdict'] == '[OK]') and oos_pass and (mcpt_r['verdict'] == '[OK]')
    print()
    if all_pass:
        print("  >>> ALL 3 LAYERS PASSED. Strategy is statistically validated.")
        print("  >>> Recommend: 0.3x position for live trading, monitor MDD < 10%.")
    else:
        print("  >>> AT LEAST ONE LAYER FAILED. Do NOT deploy live without further analysis.")
        if is_r['verdict'] != '[OK]':
            print("      - IS failed: check params, n_trades, MDD")
        if not oos_pass:
            print("      - OOS failed: params may be curve-fit to IS window")
        if mcpt_r['verdict'] != '[OK]':
            print("      - MCPT failed: Sharpe may be luck, not real edge")
    print("=" * 80)

    # ── Save JSON ──
    out_json = strategy_dir / 'validation.json'
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({
            'strategy': name,
            'oos_days': args.oos_days,
            'mcpt_n':   args.mcpt_n,
            'IS':  {k: v for k, v in is_r.items() if k not in ('pos_array', 'close_v')},
            'OOS': oos_r,
            'MCPT': {k: v for k, v in mcpt_r.items() if k != 'dist'},
            'sharpe_drop_pct': sharpe_drop_pct,
            'all_pass': all_pass,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {out_json}")
    return 0 if all_pass else 2


if __name__ == '__main__':
    sys.exit(main())
