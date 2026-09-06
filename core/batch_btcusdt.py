"""
batch_btcusdt.py — 一键重跑 BTCUSDT 4 视图
==========================================
集中定义 4 个时间维度的参数,避免手动跑 4 次 chan_plot.py。

视图定义:
  - 30d  1h  短线
  - 180d 4h  中线
  - 3y   1d  长线
  - 2y   1h  完整周期

Usage:
    python core/batch_btcusdt.py           # 跑全部 4 张
    python core/batch_btcusdt.py --only 1h_30d 4h_180d   # 跑指定几张
    python core/batch_btcusdt.py --auto    # 只跑 cache 比 stats.json 新的视图
    python core/batch_btcusdt.py --force   # 强制重跑全部,忽略时间戳
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import argparse
import json
import subprocess
import sys
from pathlib import Path


# ── 路径常量 ───────────────────────────────────
_THIS_DIR  = Path(__file__).parent
_ROOT      = _THIS_DIR.parent
CACHE_DIR  = _ROOT / "cache"
VIEW_DIR   = _ROOT / "chan_view"


# ── 4 视图定义 ───────────────────────────────────
VIEWS = {
    "1h_30d":   dict(freq="1h", days=30,  x_range=500, tag="30d",  desc="短线"),
    "4h_180d":  dict(freq="4h", days=180, x_range=200, tag="180d", desc="中线"),
    "1d_3y":    dict(freq="1d", days=1095, x_range=200, tag="3y",  desc="长线"),
    "1h_2y":    dict(freq="1h", days=730,  x_range=600, tag="2y",  desc="完整周期"),
}


def cache_mtime(symbol: str = "BTCUSDT") -> float:
    """cache/ 里所有 BTCUSDT parquet 的最新 mtime(秒)。"""
    files = list(CACHE_DIR.glob(f"kline_*_{symbol}_*.parquet"))
    if not files:
        return 0.0
    return max(f.stat().st_mtime for f in files)


def stats_mtime(view: str, view_cfg: dict, symbol: str = "BTCUSDT") -> float:
    """对应 stats.json 的 mtime,无则返回 0。"""
    path = VIEW_DIR / f"stats_{symbol}_{view_cfg['freq']}_{view_cfg['tag']}.json"
    return path.stat().st_mtime if path.exists() else 0.0


def is_cache_newer(view: str, view_cfg: dict) -> bool:
    """cache 是否比 stats.json 新(→ 需要重跑)"""
    return cache_mtime() > stats_mtime(view, view_cfg)


def run_view(view_name: str, view_cfg: dict) -> bool:
    """调用 chan_plot.py 跑单张图。返回是否成功。"""
    cmd = [
        sys.executable,
        str(_THIS_DIR / "chan_plot.py"),
        "--freq", view_cfg["freq"],
        "--days", str(view_cfg["days"]),
        "--x-range", str(view_cfg["x_range"]),
        "--tag", view_cfg["tag"],
    ]
    print(f"\n{'='*60}")
    print(f"[{view_name}] {view_cfg['desc']}  {view_cfg['freq']} × {view_cfg['days']}d")
    print(f"  cmd: {' '.join(cmd)}")
    print('='*60)
    result = subprocess.run(cmd, cwd=str(_ROOT))
    return result.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 4 视图批量重跑")
    ap.add_argument("--only", nargs="+", default=None,
                    choices=list(VIEWS.keys()),
                    help="只跑指定视图(默认全部)")
    ap.add_argument("--list", action="store_true",
                    help="只列出视图定义,不实际跑")
    ap.add_argument("--auto", action="store_true",
                    help="自动模式:只跑 cache 比 stats.json 新的视图(其余跳过)")
    ap.add_argument("--force", action="store_true",
                    help="强制重跑全部,忽略时间戳")
    args = ap.parse_args()

    if args.list:
        print("可用视图:")
        for k, v in VIEWS.items():
            print(f"  {k:10s}  {v['freq']:3s} × {v['days']:>4d}d  "
                  f"x_range={v['x_range']:>4d}  tag={v['tag']:5s}  ({v['desc']})")
        return

    # 自动模式:过滤出需要跑的视图
    if args.auto and not args.force:
        cache_t = cache_mtime()
        cache_str = pd_format_time(cache_t)
        print(f"[auto] cache 最新 mtime: {cache_str}")
        need_run = []
        skip = []
        for name, cfg in VIEWS.items():
            stats_t = stats_mtime(name, cfg)
            if stats_t == 0:
                need_run.append(name)
                print(f"  {name}: 无 stats.json → 需跑")
            elif cache_t > stats_t:
                need_run.append(name)
                print(f"  {name}: cache 比 stats 新 → 需跑")
            else:
                skip.append(name)
                print(f"  {name}: cache ≤ stats → 跳过")
        if not need_run:
            print("\n✓ 所有视图都是最新,无需重跑")
            return
        targets = args.only if args.only else need_run
        # 用户显式 --only 时不再过滤(尊重用户意图)
        if args.only:
            targets = [t for t in targets if t in need_run] or targets
            print(f"[auto] --only 过滤后实际跑: {targets}")
    else:
        targets = args.only if args.only else list(VIEWS.keys())

    if not targets:
        print("[auto] 没有需要跑的视图")
        return

    print(f"\n将跑 {len(targets)} 个视图: {targets}")

    results = {}
    for name in targets:
        results[name] = run_view(name, VIEWS[name])

    # 总结
    print(f"\n{'='*60}")
    print("结果汇总:")
    for name in targets:
        status = "✓" if results[name] else "✗"
        print(f"  {status} {name}")
    if all(results.values()):
        print(f"\n✓ {len(targets)} 个视图生成成功 → chan_view/")
    else:
        print(f"\n✗ {sum(1 for v in results.values() if not v)} 个失败")
        sys.exit(1)


def pd_format_time(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
