"""
Unified Entry: 50/50 规则一站式回测
====================================
一个命令跑完:
  1. 单币种 45 条 (45+ 币种 × 2 周期)
  2. BTC 联动 4 条 (E05/E06/J01/J02)
  3. 板块内联动 1 条 (J03)
  4. 反向工程 → v4.3 报告 → 文档

用法:
  python core/run_all.py                 # 默认 50 币种 90d
  python core/run_all.py --symbols-file btc.txt
  python core/run_all.py --periods 90,180
  python core/run_all.py --skip-validate  # 跳过 BTC/J03(快速模式)
"""
import sys
import os
import time
import argparse
import subprocess
import _bootstrap  # noqa: F401  — sys.path setup


def run_step(name, script, args=None):
    """跑一个步骤, 显示进度"""
    print(f"\n{'='*60}")
    print(f"=== {name} ===")
    print(f"{'='*60}")
    cmd = [sys.executable, os.path.join(_ROOT, 'core', script)]
    if args:
        cmd.extend(args)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=_ROOT, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"!!! {name} FAILED in {elapsed/60:.1f} min (returncode={result.returncode})")
        return False
    print(f"--- {name} done in {elapsed/60:.1f} min ---")
    return True


def main():
    parser = argparse.ArgumentParser(description='50/50 规则一站式回测')
    parser.add_argument('--symbols', help='币种列表(逗号)')
    parser.add_argument('--symbols-file', help='币种文件')
    parser.add_argument('--periods', default='90', help='周期天数(逗号)')
    parser.add_argument('--hold', type=int, default=12, help='持有 K 线数')
    parser.add_argument('--skip-batch', action='store_true', help='跳过单币种批(45 条)')
    parser.add_argument('--skip-btc', action='store_true', help='跳过 BTC 联动(E05/E06/J01/J02)')
    parser.add_argument('--skip-sector', action='store_true', help='跳过板块内(J03)')
    parser.add_argument('--skip-analyze', action='store_true', help='跳过 v4.3 报告生成')
    args = parser.parse_args()

    print(f"=== 50/50 Unified Backtest ===")
    print(f"Symbols: {args.symbols or args.symbols_file or '(default 50)'}")
    print(f"Periods: {args.periods}, Hold: {args.hold}")

    t0 = time.time()
    steps = []

    # Step 1: 单币种 45 条
    if not args.skip_batch:
        batch_args = []
        if args.symbols:
            batch_args += ['--symbols', args.symbols]
        elif args.symbols_file:
            batch_args += ['--symbols-file', args.symbols_file]
        batch_args += ['--periods', args.periods, '--hold', str(args.hold)]
        steps.append(('run_batch.py', batch_args))

    # Step 2: BTC 联动 4 条
    if not args.skip_btc:
        steps.append(('run_btc_corr.py', []))

    # Step 3: 板块内 J03
    if not args.skip_sector:
        steps.append(('run_j03_sector.py', []))

    # Step 4: 反向工程
    if not args.skip_analyze:
        steps.append(('analyze_results.py', []))

    # Step 5: 把 v4.3 报告追加进 文档模板.md
    steps.append(('update_doc_v43.py', []))

    total = len(steps)
    for i, (script, script_args) in enumerate(steps, 1):
        print(f"\n[Step {i}/{total}] {script}")
        if not run_step(script.replace('.py', ''), script, script_args):
            print(f"\n!!! Stopped at step {i} due to failure.")
            return 1

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"=== ALL DONE: {total} steps in {elapsed/60:.1f} min ===")
    print(f"{'='*60}")
    print(f"\nDeliverables:")
    print(f"  - cache/csv/{{sym}}_{{args.periods}}d.csv     (单币种)")
    print(f"  - cache/csv/btc_corr_results.csv   (BTC 联动)")
    print(f"  - cache/csv/j03_sector_results.csv  (板块)")
    print(f"  - cache/v4.3_calibration.md        (整合报告)")
    print(f"  - 文档模板.md (已含 v4.3 附录)")

    return 0


if __name__ == '__main__':
    sys.exit(main())
