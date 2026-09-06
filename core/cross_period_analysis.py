"""
Cross-Period Analysis (Simplified)
====================================
直接用已生成的 180d CSV + 90d baseline 做分析
不重新跑任何数据
"""
import sys
import pandas as pd
import _bootstrap  # noqa: F401  — sys.path setup

# 已成功的 180d 币种(15 个,RENDER skip, AVAX 卡住)
SYMBOLS_180 = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'DOGEUSDT', 'SHIBUSDT',
               'PEPEUSDT', 'UNIUSDT', 'AAVEUSDT', 'CRVUSDT', 'ARBUSDT', 'OPUSDT',
               'DASHUSDT', 'FETUSDT']


def load_180_data(symbols):
    """Read each symbol's 180d CSV; return flat row list + per-symbol counts."""
    sys.stdout.write("=== Loading 180d data ===\n")
    results, per_sym = [], {}
    for sym in symbols:
        try:
            df = pd.read_csv(f"cache/csv/{sym.lower()}_180d.csv")
            per_sym[sym] = len(df)
            top5 = df.sort_values('sharpe', ascending=False).head(5)
            for _, r in top5.iterrows():
                results.append({
                    'symbol': sym, 'rule': r['rule'], 'name_cn': r['name_cn'],
                    'direction_doc': r['direction_doc'], 'direction_best': r['direction_best'],
                    'best_params': r['best_params'], 'n': r['n'], 'wr': r['wr'],
                    'avg': r['avg'], 'total': r['total'], 'sharpe': r['sharpe'], 'mdd': r['mdd'],
                })
            sys.stdout.write(f"  {sym}: {len(df)} rules\n")
        except FileNotFoundError:
            sys.stdout.write(f"  {sym}: NOT FOUND\n")
    sys.stdout.flush()
    return results, per_sym


def build_rule_summary(df_90, df_180):
    """Merge 90d/180d per-(rule, symbol), aggregate to per-rule summary."""
    df_90['key']  = df_90['symbol'].astype(str)  + '_' + df_90['rule'].astype(str)
    df_180['key'] = df_180['symbol'].astype(str) + '_' + df_180['rule'].astype(str)
    merged = pd.merge(
        df_90[['key', 'symbol', 'rule', 'name_cn', 'sharpe', 'total', 'wr', 'n', 'direction_best']].rename(
            columns={'sharpe': 'sharpe_90', 'total': 'total_90', 'wr': 'wr_90',
                     'n': 'n_90', 'direction_best': 'dir_90'}),
        df_180[['key', 'sharpe', 'total', 'wr', 'n', 'direction_best']].rename(
            columns={'sharpe': 'sharpe_180', 'total': 'total_180', 'wr': 'wr_180',
                     'n': 'n_180', 'direction_best': 'dir_180'}),
        on='key', how='outer',
    )
    both = merged.dropna(subset=['sharpe_90', 'sharpe_180'])
    sys.stdout.write(f"Paired in both periods: {len(both)}\n\n")
    sys.stdout.flush()
    both['both_positive']  = (both['sharpe_90'] > 0) & (both['sharpe_180'] > 0)
    both['dir_consistent'] = both['dir_90'] == both['dir_180']
    rule_summary = both.groupby('rule').agg(
        n_symbols       = ('symbol',          'count'),
        both_pos        = ('both_positive',   'sum'),
        dir_consistent  = ('dir_consistent',  'sum'),
        avg_sharpe_90   = ('sharpe_90',       'mean'),
        avg_sharpe_180  = ('sharpe_180',      'mean'),
        avg_total_90    = ('total_90',        'mean'),
        avg_total_180   = ('total_180',       'mean'),
    ).reset_index()
    rule_summary['stability'] = rule_summary['both_pos'] / rule_summary['n_symbols']
    rule_summary = rule_summary.sort_values('stability', ascending=False)
    return both, rule_summary


def print_stability_table(rule_summary):
    """Per-rule stability + sharpe/return table."""
    sys.stdout.write("=== STABILITY BY RULE (>= 50% positive both periods) ===\n")
    sys.stdout.write(f"{'Rule':<5} {'#Sym':>5} {'Both+':>6} {'Stab%':>6} {'DirOK':>5} {'S90':>7} {'S180':>8} {'T90':>8} {'T180':>9}\n")
    sys.stdout.write("-" * 75 + "\n")
    for _, r in rule_summary.iterrows():
        sys.stdout.write(f"{r['rule']:<5} {r['n_symbols']:>5.0f} {r['both_pos']:>6.0f} {r['stability']*100:>5.1f}% "
                         f"{r['dir_consistent']:>5.0f} {r['avg_sharpe_90']:>7.2f} {r['avg_sharpe_180']:>8.2f} "
                         f"{r['avg_total_90']:>+8.2f} {r['avg_total_180']:>+9.2f}\n")
    sys.stdout.flush()


def print_stable_rules(rule_summary, both):
    """Stable rules (>=50%) — for human scanning."""
    stable = rule_summary[rule_summary['stability'] >= 0.5]
    sys.stdout.write(f"\n=== STABLE RULES (>= 50% symbols positive both periods) ===\n")
    sys.stdout.write(f"Found {len(stable)} rules\n\n")
    for _, r in stable.iterrows():
        name_match = both[both['rule'] == r['rule']]['name_cn']
        name_cn    = name_match.iloc[0] if len(name_match) else ''
        sys.stdout.write(f"  {r['rule']:<5} stab={r['stability']*100:>4.0f}% | "
                         f"90d:  sh={r['avg_sharpe_90']:>5.2f}/ret={r['avg_total_90']:>+6.0f}% | "
                         f"180d: sh={r['avg_sharpe_180']:>5.2f}/ret={r['avg_total_180']:>+6.0f}% | "
                         f"{name_cn}\n")
    sys.stdout.flush()


def print_direction_flip(df_90, df_180):
    """Compare direction-flip rate between 90d and 180d."""
    flip_90  = (df_90['direction_doc']  != df_90['direction_best']).sum()  / len(df_90)  * 100
    flip_180 = (df_180['direction_doc'] != df_180['direction_best']).sum() / len(df_180) * 100
    sys.stdout.write(f"\n=== DIRECTION FLIP COMPARISON ===\n")
    sys.stdout.write(f"  90d  flip rate: {flip_90:.1f}%  ({(df_90['direction_doc']  != df_90['direction_best']).sum()}/{len(df_90)})\n")
    sys.stdout.write(f"  180d flip rate: {flip_180:.1f}%  ({(df_180['direction_doc'] != df_180['direction_best']).sum()}/{len(df_180)})\n")
    sys.stdout.write(f"  Delta: {flip_180-flip_90:+.1f}%\n\n")
    sys.stdout.flush()


def print_overall(df_90, df_180):
    """Overall mean Sharpe / positive rate per period."""
    sys.stdout.write(f"=== OVERALL ===\n")
    sys.stdout.write(f"  90d  mean Sharpe: {df_90['sharpe'].mean():.2f}, positive: {(df_90['sharpe']>0).sum()}/{len(df_90)} ({(df_90['sharpe']>0).sum()/len(df_90)*100:.1f}%)\n")
    sys.stdout.write(f"  180d mean Sharpe: {df_180['sharpe'].mean():.2f}, positive: {(df_180['sharpe']>0).sum()}/{len(df_180)} ({(df_180['sharpe']>0).sum()/len(df_180)*100:.1f}%)\n")
    sys.stdout.flush()


def main():
    results_180, _ = load_180_data(SYMBOLS_180)
    df_180 = pd.DataFrame(results_180)
    df_180.to_csv('cache/csv/batch_180d_partial.csv', index=False)
    sys.stdout.write(f"\nTotal 180d rows: {len(df_180)} (from {len(SYMBOLS_180)} symbols)\n\n")
    sys.stdout.flush()

    # 90d baseline
    df_90 = pd.read_csv('cache/csv/batch_50_summary.csv')
    df_90 = df_90[df_90['symbol'].isin(SYMBOLS_180)]
    sys.stdout.write(f"90d baseline: {len(df_90)} rows (filtered to {len(SYMBOLS_180)} symbols)\n\n")
    sys.stdout.flush()

    both, rule_summary = build_rule_summary(df_90, df_180)
    rule_summary.to_csv('cache/csv/cross_period_rule_summary.csv', index=False)

    print_stability_table(rule_summary)
    print_stable_rules(rule_summary, both)
    print_direction_flip(df_90, df_180)
    print_overall(df_90, df_180)


if __name__ == '__main__':
    main()
