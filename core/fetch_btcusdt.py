"""
fetch_btcusdt.py — BTCUSDT 数据拉取(Blave API)
============================================
从 Blave API 拉 BTCUSDT 在指定频率下的 K 线数据,保存到 cache/,
供 chan_plot.py 严格匹配使用。

Usage:
    python core/fetch_btcusdt.py --freq 1d
    python core/fetch_btcusdt.py --freq 4h
    python core/fetch_btcusdt.py --freq 1w
    python core/fetch_btcusdt.py --freq 1d --start 2018-01-01   # 自定义起始
    python core/fetch_btcusdt.py --all                          # 拉所有支持频率

目前 cache/ 已有:
  - 1h  (4.5 年)
本脚本支持拉取: 1d / 4h / 1w / 1m / 5m / 15m / 30m
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import _bootstrap  # noqa
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from lib.data import _fetch_kline_raw


# ── 配置 ─────────────────────────────────────────
DEFAULT_START = "2018-01-01"   # 默认起始日期(覆盖多个完整牛熊周期)
SUPPORTED_FREQS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def normalize_tz(df: pd.DataFrame) -> pd.DataFrame:
    """Blave 返回 tz-aware,统一转 tz-naive(与已有 1h 缓存一致)。"""
    if df.index.tz is not None:
        df.index = df.index.tz_convert('UTC').tz_localize(None)
    df = df[~df.index.duplicated(keep='first')].sort_index()
    return df


def fetch_one(freq: str, start: str, end: str) -> pd.DataFrame:
    """拉单个频率的 K 线数据。"""
    print(f"\n[fetch] freq={freq}  range={start} → {end}")
    df = _fetch_kline_raw("BTCUSDT", freq, start, end)
    df = normalize_tz(df)
    print(f"        rows={len(df)}  range={df.index.min()} → {df.index.max()}")
    return df


def save_parquet(df: pd.DataFrame, freq: str) -> Path:
    """按已有命名规范保存到 cache/。"""
    # 命名规则:kline_<freq>_<SYMBOL>_<start>.parquet(模仿 1h 文件名)
    start_str = df.index.min().strftime("%Y-%m-%d")
    fname = f"kline_{freq}_BTCUSDT_{start_str}.parquet"
    path = Path(_bootstrap._ROOT) / "cache" / fname
    df.to_parquet(path, compression='snappy')
    size_mb = path.stat().st_size / 1024 / 1024
    print(f"        saved: {fname} ({size_mb:.2f} MB)")
    return path


def has_cache(freq: str) -> bool:
    """检查 cache/ 是否已有该 freq 的 BTCUSDT 文件。"""
    cache_dir = Path(_bootstrap._ROOT) / "cache"
    files = list(cache_dir.glob(f"kline_{freq}_BTCUSDT_*.parquet"))
    return len(files) > 0


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 数据拉取(Blave API)")
    ap.add_argument("--freq", choices=SUPPORTED_FREQS,
                    help="目标 K 线频率")
    ap.add_argument("--start", default=DEFAULT_START,
                    help=f"起始日期(默认 {DEFAULT_START})")
    ap.add_argument("--end", default=None,
                    help="结束日期(默认 = 今天)")
    ap.add_argument("--all", action="store_true",
                    help="拉取所有支持的频率(除了已缓存的)")
    ap.add_argument("--force", action="store_true",
                    help="强制重新拉取(即使已有缓存)")
    ap.add_argument("--list", action="store_true",
                    help="列出 cache/ 已有频率和缺失频率")
    args = ap.parse_args()

    if args.list:
        print("cache/ 当前状态:")
        for f in SUPPORTED_FREQS:
            mark = "✓" if has_cache(f) else "✗"
            print(f"  {mark} {f}")
        return

    # 决定要拉哪些频率
    if args.all:
        targets = [f for f in SUPPORTED_FREQS if args.force or not has_cache(f)]
        if not targets:
            print("✓ 所有支持的频率都已缓存(用 --force 强制重新拉取)")
            return
    elif args.freq:
        if not args.force and has_cache(args.freq):
            print(f"✓ {args.freq} 已缓存(用 --force 强制重新拉取)")
            return
        targets = [args.freq]
    else:
        print("✗ 必须传 --freq <FREQ> 或 --all")
        sys.exit(1)

    print(f"将拉取 {len(targets)} 个频率: {targets}")

    end = args.end or datetime.now().strftime("%Y-%m-%d")
    for freq in targets:
        try:
            df = fetch_one(freq, args.start, end)
            save_parquet(df, freq)
        except Exception as e:
            print(f"  ✗ {freq} 拉取失败: {e}")
            continue

    print("\n✓ 全部完成。可用 `python core/batch_btcusdt.py` 重新出图。")


if __name__ == "__main__":
    main()