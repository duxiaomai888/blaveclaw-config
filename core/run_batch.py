"""
Unified Batch Backtest Framework
=================================
统一入口: 任意币种 × 任意周期 × 任意规则集

用法:
    # 默认 50 币种 90d
    python run_batch.py

    # 单币种(快速测试)
    python run_batch.py --symbols BTCUSDT

    # 多币种(逗号分隔)
    python run_batch.py --symbols BTCUSDT,ETHUSDT,SOLUSDT

    # 从文件读币种列表(500 币种场景)
    python run_batch.py --symbols-file symbols_500.txt

    # 多周期
    python run_batch.py --periods 90,180

    # 增量跑(只跑 cache/csv/ 里没有的)
    python run_batch.py --incremental

    # 完整选项
    python run_batch.py --symbols-file symbols.txt --periods 90,180 --workers 4 --timeout 120 --incremental
"""
import argparse
import sys
import time
import os
import requests
from pathlib import Path
import _bootstrap  # noqa: F401  — sys.path setup
from core.single_symbol_backtest import run_single_symbol  # in-process call (was subprocess before)
from lib.symbols import DEFAULT_BATCH_50 as DEFAULT_SYMBOLS

# DEFAULT_SYMBOLS is imported from lib.symbols above.
# To add a coin, edit lib/symbols.py once.


def run_one(sym, days, hold):
    """In-process call — replaces the previous subprocess+inline-template (faster, no startup overhead)."""
    try:
        df = run_single_symbol(sym, days=days, hold_bars=hold, top_n=0, verbose=False)
        if df is not None and len(df) > 0:
            return f'OK {len(df)}'
        return 'EMPTY'
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        # Network / data / I/O error during single-symbol backtest
        return f'EXC:{type(e).__name__}:{e}'[:120]


def load_symbols(args):
    """决定币种列表: 文件 > 命令行 > 默认"""
    if args.symbols_file:
        with open(args.symbols_file) as f:
            return [s.strip() for s in f if s.strip()]
    elif args.symbols:
        return [s.strip() for s in args.symbols.split(',') if s.strip()]
    else:
        return DEFAULT_SYMBOLS


def load_periods(args):
    if args.periods:
        return [int(p) for p in args.periods.split(',') if p.strip()]
    return [90]


def main():
    parser = argparse.ArgumentParser(description='Unified Batch Backtest')
    parser.add_argument('--symbols', help='逗号分隔币种列表,如 BTCUSDT,ETHUSDT')
    parser.add_argument('--symbols-file', help='从文件读币种列表(每行一个)')
    parser.add_argument('--periods', default='90', help='逗号分隔天数,默认 90')
    parser.add_argument('--hold', type=int, default=12, help='持有 K 线数,默认 12')
    parser.add_argument('--workers', type=int, default=1, help='(已忽略,保留向后兼容) 并行 worker 数')
    parser.add_argument('--timeout', type=int, default=0, help='(已忽略,保留向后兼容) 单币种超时秒数')
    parser.add_argument('--incremental', action='store_true', help='增量跑(跳过已有 CSV 的币种周期)')
    parser.add_argument('--no-merge', action='store_true', help='不合并到 batch_50_summary,只跑单币种')
    args = parser.parse_args()

    symbols = load_symbols(args)
    periods = load_periods(args)

    print(f"=== Unified Batch Backtest ===", flush=True)
    print(f"Symbols: {len(symbols)} | Periods: {periods} | Hold: {args.hold}", flush=True)
    if args.workers != 1 or args.timeout != 0:
        print(f"  Note: --workers / --timeout are deprecated (now in-process, sequential)", flush=True)
    print(flush=True)

    total_jobs = len(symbols) * len(periods)
    print(f"Total jobs: {total_jobs}\n", flush=True)

    t0 = time.time()
    all_results = []
    errors = []
    success = 0

    for period in periods:
        print(f"--- Period: {period}d ---", flush=True)
        for i, sym in enumerate(symbols, 1):
            out_path = f"cache/csv/{sym.lower()}_{period}d.csv"

            # 增量跳过的判断
            if args.incremental and os.path.exists(out_path):
                try:
                    import pandas as pd
                    existing = pd.read_csv(out_path)
                    if len(existing) > 0:
                        print(f"[{i}/{len(symbols)}] {sym} ({period}d) ... SKIP (exists)", flush=True)
                        continue
                except (OSError, ValueError) as e:
                    # Corrupt CSV — re-run backtest
                    print(f"[{i}/{len(symbols)}] {sym} ({period}d) ... corrupt CSV ({e}), re-running", flush=True)

            print(f"[{i}/{len(symbols)}] {sym} ({period}d) ... ", end='', flush=True)
            out = run_one(sym, period, args.hold)

            if out.startswith('OK'):
                print(out, flush=True)
                success += 1
                # 收集 Top 5
                if not args.no_merge:
                    import pandas as pd
                    try:
                        df = pd.read_csv(out_path)
                        top5 = df.sort_values('sharpe', ascending=False).head(5)
                        for _, r in top5.iterrows():
                            all_results.append({
                                'symbol': sym, 'rule': r['rule'], 'name_cn': r['name_cn'],
                                'direction_doc': r['direction_doc'], 'direction_best': r['direction_best'],
                                'best_params': r['best_params'], 'n': r['n'], 'wr': r['wr'],
                                'avg': r['avg'], 'total': r['total'], 'sharpe': r['sharpe'], 'mdd': r['mdd'],
                                'period': period,
                                'other_dir': r['other_dir'], 'other_n': r['other_n'],
                                'other_wr': r['other_wr'], 'other_sharpe': r['other_sharpe'],
                                'other_total': r['other_total'], 'other_avg': r['other_avg'],
                                'other_mdd': r['other_mdd']
                            })
                    except (KeyError, TypeError, ValueError) as e:
                        # top5 dict missing or wrong shape — skip merge for this sym
                        print(f"  [warn] merge top5 for {sym}: {e}", flush=True)
            elif out == 'TIMEOUT':
                print('TIMEOUT', flush=True)
                errors.append((sym, period, 'timeout'))
            elif out == 'EMPTY':
                print('EMPTY', flush=True)
                errors.append((sym, period, 'empty'))
            else:
                print(f'FAIL ({out[:50]})', flush=True)
                errors.append((sym, period, out[:80]))

    elapsed = time.time() - t0

    # 合并到 batch_summary
    if all_results and not args.no_merge:
        import pandas as pd
        df_all = pd.DataFrame(all_results)
        # 默认 50 币种 90d 写到 batch_50_summary
        if 90 in periods and len(periods) == 1 and len(symbols) > 10:
            out_csv = 'cache/csv/batch_50_summary.csv'
        else:
            out_csv = f"cache/csv/batch_{len(symbols)}symbols_{periods[0]}d.csv"
        df_all.to_csv(out_csv, index=False)
        print(f"\n=== Merged: {len(df_all)} rows → {out_csv} ===", flush=True)

    print(f"\n=== DONE ===", flush=True)
    print(f"Success: {success}/{total_jobs} | Errors: {len(errors)} | Elapsed: {elapsed/60:.1f} min", flush=True)
    for sym, p, e in errors[:20]:
        print(f"  {sym} ({p}d): {e}", flush=True)
    if len(errors) > 20:
        print(f"  ... and {len(errors)-20} more", flush=True)


if __name__ == '__main__':
    main()
