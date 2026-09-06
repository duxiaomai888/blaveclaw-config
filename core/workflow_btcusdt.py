"""
workflow_btcusdt.py — BTCUSDT 一键工作流
========================================
拉取缺失数据 → 出 4 视图 → 出联立图,一条命令搞定。

Usage:
    python core/workflow_btcusdt.py            # 拉数据 + 出图(增量)
    python core/workflow_btcusdt.py --force   # 强制重新拉数据 + 重跑所有视图
    python core/workflow_btcusdt.py --skip-fetch   # 跳过拉数据,直接出图
    python core/workflow_btcusdt.py --skip-plot    # 只拉数据,不出图
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import argparse
import subprocess
from pathlib import Path

import _bootstrap  # noqa


def run(cmd: list, cwd: str = None) -> int:
    """运行子命令,返回 exit code。"""
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or str(Path(_bootstrap._ROOT)))
    return result.returncode


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 一键工作流")
    ap.add_argument("--force", action="store_true",
                    help="强制重新拉数据(覆盖已有 cache)")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="跳过数据拉取,直接出图")
    ap.add_argument("--skip-plot", action="store_true",
                    help="只拉数据,跳过出图")
    args = ap.parse_args()

    ROOT = _bootstrap._ROOT
    steps = []

    # Step 1: 拉数据(可选)
    if not args.skip_fetch:
        print("="*60)
        print("STEP 1/3: 拉取数据(Blave API)")
        print("="*60)
        cmd = [sys.executable, str(Path(ROOT) / "core" / "fetch_btcusdt.py"),
               "--all", "--start", "2018-01-01"]
        if args.force:
            cmd.append("--force")
        rc = run(cmd)
        if rc != 0:
            print(f"✗ 拉数据失败(exit={rc}),终止")
            sys.exit(rc)
        steps.append("fetch")
    else:
        print("(skip-fetch:跳过数据拉取)")

    # Step 2: 批量出 4 视图
    if not args.skip_plot:
        print("\n" + "="*60)
        print("STEP 2/3: 批量出 4 视图")
        print("="*60)
        cmd = [sys.executable, str(Path(ROOT) / "core" / "batch_btcusdt.py")]
        if args.force:
            cmd.append("--force")
        rc = run(cmd)
        if rc != 0:
            print(f"✗ batch 出图失败(exit={rc})")
            sys.exit(rc)
        steps.append("batch")

        # Step 3: 多级别联立图
        print("\n" + "="*60)
        print("STEP 3/3: 多级别联立图")
        print("="*60)
        cmd = [sys.executable, str(Path(ROOT) / "core" / "chan_plot_multi.py")]
        rc = run(cmd)
        if rc != 0:
            print(f"✗ 联立图失败(exit={rc})")
            sys.exit(rc)
        steps.append("multi")
    else:
        print("(skip-plot:跳过出图)")

    # 总结
    print("\n" + "="*60)
    print("✓ 工作流完成")
    print("="*60)
    print(f"执行步骤: {' → '.join(steps) if steps else '(无)'}")
    print()
    print("产出 (chan_view/):")
    view_dir = Path(ROOT) / "chan_view"
    for f in sorted(view_dir.glob("chan_BTCUSDT_*.png")):
        size_kb = f.stat().st_size / 1024
        print(f"  📊 {f.name:40s}  {size_kb:>6.1f} KB")
    print()
    print("统计 (chan_view/stats_BTCUSDT_*.json):")
    for f in sorted(view_dir.glob("stats_BTCUSDT_*.json")):
        print(f"  📋 {f.name}")
    print()
    print("详细解读见 chan_view/BTCUSDT.md")


if __name__ == "__main__":
    main()