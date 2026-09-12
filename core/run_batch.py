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

    # 并行(网络 IO 是瓶颈,线程池足够;worker 数 > key 数无益)
    python run_batch.py --symbols-file symbols.txt --periods 90,180 --workers 6

    # 完整选项
    python run_batch.py --symbols-file symbols.txt --periods 90,180 --workers 6 --incremental
"""
import argparse
import time
import os
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import _bootstrap  # noqa: F401  — sys.path setup
from core.single_symbol_backtest import run_single_symbol  # in-process call (was subprocess before)
from lib.symbols import DEFAULT_BATCH_50 as DEFAULT_SYMBOLS
from lib.data import get_all_headers, set_global_limiter, _KeyAwareRateLimiter

# DEFAULT_SYMBOLS is imported from lib.symbols above.
# To add a coin, edit lib/symbols.py once.

# 限流默认值(总预算 = n_keys × rps_per_key;服务器 500/5min ≈ 1.67 req/s/key,
# 6 keys → ~10 req/s。低于此预算的 worker 数不会触发 429 指数退避。)
DEFAULT_RPS_PER_KEY = 2.0


def run_one(sym, days, hold):
    """In-process call — replaces the previous subprocess+inline-template (faster, no startup overhead)."""
    try:
        df = run_single_symbol(sym, days=days, hold_bars=hold, top_n=0, verbose=False)
        if df is not None and len(df) > 0:
            return f'OK {len(df)}'
        return 'EMPTY'
    except (requests.RequestException, ValueError, KeyError, OSError, RuntimeError) as e:
        # Network / data / I/O error during single-symbol backtest; RuntimeError = dead symbol (HTTP 400)
        return f'EXC:{type(e).__name__}:{e}'[:120]


def load_symbols(args):
    """决定币种列表: 文件 > 命令行 > 默认"""
    if args.symbols_file:
        with open(args.symbols_file) as f:
            syms = [s.strip() for s in f if s.strip()]
        # 跳过表头行(如 'symbol')与非 USDT 的说明行
        return [s for s in syms if s.upper().endswith('USDT')]
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
    parser.add_argument('--workers', type=int, default=0,
                        help='并行 worker 数(默认 = API key 数;0 或 1 = 串行)'
                             '网络 IO 是瓶颈,线程池足够;worker 数 > key 数无益')
    parser.add_argument('--rps-per-key', type=float, default=DEFAULT_RPS_PER_KEY,
                        help=f'每 key 每秒请求数(默认 {DEFAULT_RPS_PER_KEY};总预算 = n_keys × 此值)')
    parser.add_argument('--timeout', type=int, default=0, help='(已忽略,保留向后兼容) 单币种超时秒数')
    parser.add_argument('--incremental', action='store_true', help='增量跑(跳过已有 CSV 的币种周期)')
    parser.add_argument('--no-merge', action='store_true', help='不合并到 batch_50_summary,只跑单币种')
    args = parser.parse_args()

    symbols = load_symbols(args)
    periods = load_periods(args)

    # worker 数:默认 = key 数(每 key 一个并发预算);上限不过 8(再多只是抢锁)
    n_keys = max(1, len(get_all_headers()))
    workers = args.workers if args.workers > 1 else min(n_keys, 8)
    parallel = workers > 1

    # 全局限流器:run_single_symbol 内部并发 fetch(kline + 5 alpha,各自再开 chunk 池),
    # 没有注入点。装一个进程级 _KeyAwareRateLimiter(单 bucket)让 _retry_get 在 transport
    # 层 acquire —— 包括内部 chunk 池的工作线程(它们在自己线程上跑,看不到 thread-local 钩子)。
    # 单 bucket + 锁外睡眠,不会像 _RateLimiter 那样把所有 chunk worker 串行化。
    # 串行模式下也装上,行为与并行一致(都是 transport 层限流)。
    total_rps = DEFAULT_RPS_PER_KEY * n_keys if args.rps_per_key == DEFAULT_RPS_PER_KEY else args.rps_per_key * n_keys
    set_global_limiter(_KeyAwareRateLimiter(rps_per_key=total_rps))

    print(f"=== Unified Batch Backtest ===", flush=True)
    print(f"Symbols: {len(symbols)} | Periods: {periods} | Hold: {args.hold}", flush=True)
    print(f"Keys: {n_keys} | Workers: {workers}{' (parallel)' if parallel else ' (sequential)'}"
          f" | Rate: {total_rps:.0f} req/s total ({args.rps_per_key}/key)", flush=True)
    if args.timeout != 0:
        print(f"  Note: --timeout is deprecated (now in-process, no per-symbol timeout)", flush=True)
    print(flush=True)

    total_jobs = len(symbols) * len(periods)
    print(f"Total jobs: {total_jobs}\n", flush=True)

    t0 = time.time()
    all_results = []
    errors = []
    success = 0
    done = 0
    print_lock = threading.Lock()
    merge_lock = threading.Lock()

    def _merge_top5(sym, period):
        """Read this symbol's CSV and append its top-5 rows to all_results."""
        out_path = f"cache/csv/{sym.lower()}_{period}d.csv"
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
        except (KeyError, TypeError, ValueError, OSError) as e:
            # top5 dict missing or wrong shape — skip merge for this sym
            with print_lock:
                print(f"  [warn] merge top5 for {sym}: {e}", flush=True)

    def _job(sym, period):
        """Run one symbol-period job, return (sym, period, out_str)."""
        out = run_one(sym, period, args.hold)
        return sym, period, out

    for period in periods:
        print(f"--- Period: {period}d ---", flush=True)

        # 增量预过滤:跳过已有且非空的 CSV,避免给 worker 池塞空任务
        jobs = []
        for sym in symbols:
            out_path = f"cache/csv/{sym.lower()}_{period}d.csv"
            if args.incremental and os.path.exists(out_path):
                try:
                    import pandas as pd
                    existing = pd.read_csv(out_path)
                    if len(existing) > 0:
                        with print_lock:
                            print(f"[{len(jobs)+1}/{len(symbols)}] {sym} ({period}d) ... SKIP (exists)", flush=True)
                        continue
                except (OSError, ValueError) as e:
                    with print_lock:
                        print(f"[{len(jobs)+1}/{len(symbols)}] {sym} ({period}d) ... corrupt CSV ({e}), re-running", flush=True)
            jobs.append(sym)

        if parallel and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_job, sym, period): sym for sym in jobs}
                for fut in as_completed(futures):
                    sym = futures[fut]
                    try:
                        _, _, out = fut.result()
                    except Exception as e:
                        out = f'EXC:{type(e).__name__}:{e}'[:120]
                    with merge_lock:
                        done += 1
                        idx = done
                    if out.startswith('OK'):
                        with print_lock:
                            print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... {out}", flush=True)
                        success += 1
                        if not args.no_merge:
                            _merge_top5(sym, period)
                    elif out == 'EMPTY':
                        with print_lock:
                            print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... EMPTY", flush=True)
                        errors.append((sym, period, 'empty'))
                    else:
                        with print_lock:
                            print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... FAIL ({out[:50]})", flush=True)
                        errors.append((sym, period, out[:80]))
        else:
            for sym in jobs:
                out = run_one(sym, period, args.hold)
                done += 1
                idx = done
                if out.startswith('OK'):
                    print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... {out}", flush=True)
                    success += 1
                    if not args.no_merge:
                        _merge_top5(sym, period)
                elif out == 'EMPTY':
                    print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... EMPTY", flush=True)
                    errors.append((sym, period, 'empty'))
                else:
                    print(f"[{idx}/{total_jobs}] {sym} ({period}d) ... FAIL ({out[:50]})", flush=True)
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
