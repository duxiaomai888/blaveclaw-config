"""
Single-Symbol Backtest Framework v2
===================================
单币种回测框架 - 50 条规则结构化版

调用 rules_catalog.catalog 作为唯一真理之源
- 规则定义/业务逻辑/参数空间统一在 catalog
- 这里只做数据加载 + 回测 + 报告
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import requests
import _bootstrap  # noqa: F401  — sys.path setup
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from lib.data import (
    fetch_kline, fetch_holder_concentration, fetch_whale_hunter,
    fetch_taker_intensity, fetch_squeeze_momentum, fetch_liquidation,
    fetch_market_sentiment
)
from lib.analysis import backtest
from rules_catalog.catalog import (
    ALL_RULES, get_active_rules, get_rules_by_category,
    resolve_param_space, get_total_count, get_active_count
)

# ── Defaults ────────────────────────────────────────────
FEE = 0.0005
MIN_TRADES = 5
PERIODS_PER_YEAR = 8760


# ── 1. 数据加载 ──────────────────────────────────────
def load_data(symbol, start, end, interval='1h'):
    """加载单币种所有指标"""
    try:
        kl = fetch_kline(symbol, interval, start, end)
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
        # Dead symbol (HTTP 400) / network error — empty frame lets caller skip
        print(f"  [skip] kline {symbol}: {e}")
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
    df = kl.copy()
    log_ret = np.log(df['Close'] / df['Close'].shift(1))
    df['realized_vol'] = log_ret.rolling(168).std() * np.sqrt(PERIODS_PER_YEAR)

    def _load(name, fn, **kw):
        try:
            d = fn(symbol, interval, start, end, **kw)
            if d is not None and len(d) > 0 and 'alpha' in d.columns:
                return d['alpha'].rename(name)
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
            # Network / data shape error / dead symbol (HTTP 400 RuntimeError) fetching alpha indicator
            print(f"  [skip] {name}: {e}")
        return None

    parts = {
        'HC': _load('HC', fetch_holder_concentration),
        'WH': _load('WH', fetch_whale_hunter, timeframe='24h', score_type='score_oi'),
        'TI': _load('TI', fetch_taker_intensity, timeframe='24h'),
        'LM': _load('LM', fetch_liquidation, timeframe='24h'),
        'MS': _load('MS', fetch_market_sentiment),
    }
    try:
        sm = fetch_squeeze_momentum(symbol, start, end)
        parts['SM'] = sm['alpha']
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  [skip] SM: {e}")

    for name, s in parts.items():
        df[name] = s.ffill() if s is not None else np.nan

    # 派生
    df['abs_HC'] = df['HC'].abs()
    df['abs_TI'] = df['TI'].abs()
    df['hc_sign'] = np.sign(df['HC'])
    df['ti_sign'] = np.sign(df['TI'])
    df['lm_sign'] = np.sign(df['LM'])
    df['hc_delta'] = np.sign(df['HC'].diff())
    df['ret_1h'] = df['Close'].pct_change(1)
    df['ret_24h'] = df['Close'].pct_change(24)
    df['new_high_24h'] = df['Close'] >= df['Close'].rolling(24).max().shift(1)
    df['new_low_24h'] = df['Close'] <= df['Close'].rolling(24).min().shift(1)
    return df


# ── 2. 单规则扫描(基于 catalog) ────────────────────
def scan_single_rule(rule, df, hold_bars, fee=FEE, min_trades=MIN_TRADES):
    """
    对单条规则做完整参数扫描,返回 (long_best, short_best)。
    每个 best 是 (direction, params, n, wr, sharpe, total, avg, mdd) 或 None。
    """
    if rule.get('skip', False):
        return None, None

    param_space = resolve_param_space(rule, df)
    param_names = list(param_space.keys())
    param_values = [param_space[p] for p in param_names]
    from itertools import product

    long_best, short_best = None, None
    for combo in product(*param_values):
        p = dict(zip(param_names, combo))
        try:
            cond = rule['cond_builder'](df, p)
            if not isinstance(cond, pd.Series):
                cond = pd.Series(cond, index=df.index)
            if cond.dtype != bool:
                cond = cond.astype(bool)
        except (ValueError, KeyError, TypeError) as e:
            # cond_builder produced wrong shape or columns missing
            print(f"  [skip] {rule['id']} {p}: {e}")
            continue
        if cond.sum() < min_trades:
            continue
        for direction, holder_name in [('long', 'long_best'), ('short', 'short_best')]:
            r = backtest(cond, df['Close'], direction, hold_bars,
                         fee=fee, min_trades=min_trades, periods_per_year=PERIODS_PER_YEAR)
            if r is None:
                continue
            cand = (direction, p, r['n'], r['wr'], r['sharpe'],
                    r['total'], r['avg'], r['mdd'])
            cur = long_best if direction == 'long' else short_best
            if cur is None or r['sharpe'] > cur[4]:
                if direction == 'long':
                    long_best = cand
                else:
                    short_best = cand
    return long_best, short_best


# ── 3. 主入口 ──────────────────────────────────────
def _assemble_row(rule, best, other):
    """Pack (long_best|short_best) into the report row schema, with the other side for comparison."""
    direction, params, n, wr, sharpe, total, avg, mdd = best
    other_dir = 'short' if direction == 'long' else 'long'
    if other is None:
        other_n = other_wr = other_sharpe = other_total = other_avg = other_mdd = 0
    else:
        _, _, other_n, other_wr, other_sharpe, other_total, other_avg, other_mdd = other
    return {
        'rule':           rule['id'],
        'name_cn':        rule['name_cn'],
        'name_short':     rule['name_short'],
        'category':       rule['category'],
        'direction_doc':  rule['direction_doc'],
        'direction_best': direction,
        'best_params':    str(params),
        'n':              n,
        'wr':             wr,
        'avg':            avg,
        'total':          total,
        'sharpe':         sharpe,
        'mdd':            mdd,
        'other_dir':      other_dir,
        'other_n':        other_n,
        'other_wr':       other_wr,
        'other_sharpe':   other_sharpe,
        'other_total':    other_total,
        'other_avg':      other_avg,
        'other_mdd':      other_mdd,
    }


def _print_report(df_res, top_n, bh):
    """Render the per-rule top-N + summary tables to stdout."""
    if len(df_res) == 0:
        return
    df_sorted = df_res.sort_values('sharpe', ascending=False)
    print("=" * 110)
    print(f"=== Top {top_n} (by Sharpe) ===")
    print("=" * 110)
    print(f"{'Rule':<5} {'Name':<25} {'Doc→Best':<11} {'Params':<25} {'N':>4} {'WR%':>6} {'Avg%':>7} {'Total%':>8} {'Sharpe':>7} {'OtherSharpe':>11}")
    print("-" * 110)
    for _, r in df_sorted.head(top_n).iterrows():
        doc2best = f"{r['direction_doc']}→{r['direction_best']}" if r['direction_doc'] != r['direction_best'] else r['direction_doc']
        params_short = r['best_params'][:24]
        print(f"{r['rule']:<5} {r['name_cn'][:24]:<25} {doc2best:<11} {params_short:<25} "
              f"{r['n']:>4} {r['wr']:>5.1f}% {r['avg']:>+6.2f}% {r['total']:>+7.2f}% {r['sharpe']:>7.3f} {r['other_sharpe']:>11.3f}")

    print()
    print(f"=== SUMMARY ===")
    print(f"Catalog total: {get_total_count()} (active: {get_active_count()}, skipped: {get_total_count() - get_active_count()})")
    print(f"Scanned: {len(df_res)} | Positive Sharpe: {(df_res['sharpe']>0).sum()} | "
          f"Positive return: {(df_res['total']>0).sum()}")
    n_flip = (df_res['direction_doc'] != df_res['direction_best']).sum()
    print(f"Direction flipped (doc vs best): {n_flip}/{len(df_res)}")
    if len(df_res) > 0:
        best_r = df_sorted.iloc[0]
        print(f"Best: {best_r['rule']} {best_r['name_cn']} | {best_r['direction_best']} | "
              f"params={best_r['best_params']} | Sharpe={best_r['sharpe']:.3f} | ret={best_r['total']:+.2f}%")
    print(f"BH return: {bh:+.2f}%")


def run_single_symbol(symbol, days=90, hold_bars=12, categories=None, output_csv=None, top_n=20, verbose=True):
    """
    单币种完整回测: 加载数据 → 扫描 catalog 规则 → 取最佳参数 → 输出报告
    """
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    if verbose:
        print(f"=== {symbol} | {days}d | hold={hold_bars} ===")
        print(f"Range: {start} ~ {end}")

    if verbose: print("Loading data...")
    df = load_data(symbol, start, end)
    if df is None or len(df) == 0:
        if verbose:
            print(f"  [skip] {symbol}: no data (dead symbol or network)")
        return None
    bh = (df['Close'].iloc[-1] / df['Close'].iloc[0] - 1) * 100
    if verbose: print(f"Bars: {len(df)}, BH: {bh:+.2f}%")

    if categories:
        rules = []
        for c in categories:
            rules.extend(get_rules_by_category(c))
    else:
        rules = get_active_rules()
    if verbose: print(f"Rules: {len(rules)} (from catalog)\n")

    if verbose: print("Scanning + backtesting...")
    results = []
    for rule in rules:
        long_best, short_best = scan_single_rule(rule, df, hold_bars)
        if long_best is None and short_best is None:
            continue
        # Pick higher-Sharpe direction as the "main" result; the other is for comparison.
        best  = long_best if (long_best and (not short_best or long_best[4] >= short_best[4])) else short_best
        other = short_best if best is long_best else long_best
        results.append(_assemble_row(rule, best, other))

    df_res = pd.DataFrame(results)
    if output_csv is None:
        output_csv = f"cache/csv/{symbol.lower()}_{days}d.csv"
    df_res.to_csv(output_csv, index=False)
    if verbose: print(f"Saved: {output_csv}\n")

    if verbose:
        _print_report(df_res, top_n, bh)
    return df_res


# ── CLI 入口 ─────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Single-Symbol Backtest v2 (uses rules catalog)')
    parser.add_argument('symbol', help='交易对 e.g. ETHUSDT')
    parser.add_argument('--days', type=int, default=90, help='回测天数 (default 90)')
    parser.add_argument('--hold', type=int, default=12, help='持有 K 线数 (default 12)')
    parser.add_argument('--cats', nargs='+', default=None, help='规则类别 e.g. A B D')
    parser.add_argument('--top', type=int, default=20, help='报告 Top N (default 20)')
    parser.add_argument('--out', default=None, help='输出 CSV 文件名')
    args = parser.parse_args()

    run_single_symbol(
        symbol=args.symbol,
        days=args.days,
        hold_bars=args.hold,
        categories=args.cats,
        output_csv=args.out,
        top_n=args.top
    )
