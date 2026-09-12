"""
fetch_blave_alpha.py — BTCUSDT 7 维 alpha 数据拉取
================================================
把 Blave 的 7 维 Blave alpha 拉到 cache/,供 chan.py 集成用。

7 维:
  MS  market_sentiment      市场情绪
  CS  capital_shortage      资金稀缺
  HC  holder_concentration  筹码集中度
  TI  taker_intensity       多空力道
  WH  whale_hunter          巨鲸警报
  SM  squeeze_momentum      挤压动能(固定 1d)
  BT  top_trader_exposure   顶尖交易员(只 BTC,无 symbol)

Usage:
    python core/fetch_blave_alpha.py --indicator cs wh sm bt --freq 1h
    python core/fetch_blave_alpha.py --all --start 2022-01-01
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import _bootstrap  # noqa
import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from lib.data import (
    fetch_market_sentiment, fetch_capital_shortage, fetch_holder_concentration,
    fetch_taker_intensity, fetch_whale_hunter, fetch_squeeze_momentum,
    fetch_top_trader_exposure,
)


# ── 频率 → Blave API 周期字符串映射 ─────────────
FREQ_PERIOD = {
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "2h":  "2h",
    "4h":  "4h",
    "1d":  "1d",
    "1w":  "1w",
}


# ── 每个指标的特殊处理 ──────────────────────────
# 返回的 DataFrame 有 'alpha' 列,可能含 'score_oi' / 'score_volume'(WH)
FETCHERS = {
    "ms": lambda freq, s, e: fetch_market_sentiment("BTCUSDT", freq, s, e),
    "cs": lambda freq, s, e: fetch_capital_shortage(freq, s, e),
    "hc": lambda freq, s, e: fetch_holder_concentration("BTCUSDT", freq, s, e),
    "ti": lambda freq, s, e: fetch_taker_intensity("BTCUSDT", freq, s, e),
    "wh": lambda freq, s, e: fetch_whale_hunter("BTCUSDT", freq, s, e),
    "sm": lambda freq, s, e: fetch_squeeze_momentum("BTCUSDT", s, e),  # 固定 1d
    "bt": lambda freq, s, e: fetch_top_trader_exposure(freq, s, e),  # 只 BTC,无 symbol
}


# ── 文件名规范 ─────────────────────────────────
def cache_path(ind: str, freq: str, start: str) -> Path:
    """类似 kline: <ind>_<freq>_BTCUSDT_<start>.parquet"""
    # bt / cs 没有 symbol,简化命名
    if ind in ("bt", "cs"):
        return Path(_bootstrap._ROOT) / "cache" / f"{ind}_{freq}_{start}.parquet"
    return Path(_bootstrap._ROOT) / "cache" / f"{ind}_{freq}_BTCUSDT_{start}.parquet"


def has_cache(ind: str, freq: str) -> bool:
    """检查 cache/ 是否有该指标+频率的 BTCUSDT 数据"""
    cache_dir = Path(_bootstrap._ROOT) / "cache"
    if ind in ("bt", "cs"):
        return any(cache_dir.glob(f"{ind}_{freq}_*.parquet"))
    return any(cache_dir.glob(f"{ind}_{freq}_BTCUSDT_*.parquet"))


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一为 tz-naive, 保留 alpha 列(可能含 score_oi/score_volume)"""
    if df is None or len(df) == 0:
        return df
    if df.index.tz is not None:
        df.index = df.index.tz_convert('UTC').tz_localize(None)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    return df


def fetch_one(ind: str, freq: str, start: str, end: str, force: bool = False) -> bool:
    """拉取单个 (indicator, freq) 数据"""
    if not force and has_cache(ind, freq):
        print(f"  ✓ {ind}_{freq} 已缓存(用 --force 强制重拉)")
        return True
    print(f"  ↓ 拉取 {ind}_{freq} ({start} → {end})...")
    try:
        df = FETCHERS[ind](freq, start, end)
        df = normalize(df)
        if df is None or len(df) == 0:
            print(f"    ✗ 返回空数据,跳过")
            return False
        path = cache_path(ind, freq, start)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, compression='snappy')
        print(f"    ✓ {len(df)} 行, saved: {path.name}")
        return True
    except Exception as e:
        print(f"    ✗ 失败: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 7 维 Blave alpha 拉取")
    ap.add_argument("--indicator", nargs="+",
                    choices=list(FETCHERS.keys()),
                    help="要拉取的指标(可多选): ms cs hc ti wh sm bt")
    ap.add_argument("--all", action="store_true", help="拉取全部 7 个指标")
    ap.add_argument("--freq", nargs="+", default=["1h"],
                    choices=list(FREQ_PERIOD.keys()),
                    help="K 线频率(默认 1h)")
    ap.add_argument("--start", default="2022-01-01",
                    help=f"起始日期(默认 2022-01-01)")
    ap.add_argument("--end", default=None,
                    help="结束日期(默认 = 今天)")
    ap.add_argument("--force", action="store_true",
                    help="强制重新拉取(覆盖已有 cache)")
    args = ap.parse_args()

    if args.all:
        indicators = list(FETCHERS.keys())
    elif args.indicator:
        indicators = args.indicator
    else:
        print("✗ 必须传 --indicator 或 --all")
        sys.exit(1)

    end = args.end or datetime.now().strftime("%Y-%m-%d")

    print(f"将拉取 {len(indicators)} 个指标 × {len(args.freq)} 个频率")
    print(f"  indicators: {indicators}")
    print(f"  freqs:      {args.freq}")
    print(f"  range:      {args.start} → {end}")
    print()

    success = 0
    total = 0
    for ind in indicators:
        for freq in args.freq:
            total += 1
            if fetch_one(ind, freq, args.start, end, force=args.force):
                success += 1

    print()
    print(f"完成: {success}/{total} 成功")
    if success < total:
        sys.exit(1)


if __name__ == "__main__":
    main()