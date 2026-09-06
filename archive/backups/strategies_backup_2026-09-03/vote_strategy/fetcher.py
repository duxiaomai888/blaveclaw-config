"""
fetcher.py — Blave API 实时拉取 5 个 α 指标 (1h 周期)
复用 BBAC-D lib/data.py 的 fetch 函数, 但用我们自己的缓存路径避免和 BBAC-D 冲突
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from lib.data import (
    fetch_kline, fetch_holder_concentration, fetch_market_sentiment,
    fetch_whale_hunter, fetch_taker_intensity, fetch_liquidation,
    get_headers,
)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _save_cache(symbol: str, indicator: str, df: pd.DataFrame):
    """保存到本策略目录的 cache/"""
    fp = CACHE_DIR / f"{indicator}_{symbol}.parquet"
    df.to_parquet(fp)


def _load_cache(symbol: str, indicator: str) -> pd.DataFrame:
    """从本策略目录的 cache/ 加载"""
    fp = CACHE_DIR / f"{indicator}_{symbol}.parquet"
    if fp.exists():
        return pd.read_parquet(fp)
    return pd.DataFrame()


def fetch_indicators(symbol: str = "BTCUSDT",
                     start: str = "2024-01-01",
                     end: str = None) -> Dict[str, pd.DataFrame]:
    """
    拉取 5 个指标 + K 线
    返回 dict: {"kline": df, "HC": df, "MS": df, "WH": df, "TI": df, "LM": df}
    每个 df: index=time, cols=alpha
    """
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print(f"[fetcher] 拉取 {symbol} 1h 指标 ({start} ~ {end})")
    out = {}

    # K 线
    kline = fetch_kline(symbol, "1h", start, end)
    out["kline"] = kline
    _save_cache(symbol, "kline", kline)
    print(f"  kline: {len(kline)} bars")

    # 5 个 α 指标
    fetchers = {
        "HC": lambda: fetch_holder_concentration(symbol, "1h", start, end),
        "MS": lambda: fetch_market_sentiment(symbol, "1h", start, end),
        "WH": lambda: fetch_whale_hunter(symbol, "1h", start, end, timeframe="24h"),
        "TI": lambda: fetch_taker_intensity(symbol, "1h", start, end, timeframe="24h"),
        "LM": lambda: fetch_liquidation(symbol, "1h", start, end, timeframe="24h"),
    }
    for name, fn in fetchers.items():
        try:
            df = fn()
            out[name] = df
            _save_cache(symbol, name, df)
            print(f"  {name}: {len(df)} bars")
        except Exception as e:
            print(f"  {name}: 失败 {e}, 加载本地缓存")
            cached = _load_cache(symbol, name)
            out[name] = cached
            print(f"  {name}: cache {len(cached)} bars")

    return out


def fetch_indicators_incremental(symbol: str = "BTCUSDT",
                                 lookback_days: int = 7) -> Dict[str, pd.DataFrame]:
    """
    增量拉取: 只拉最近 lookback_days 的数据
    用于每 1h 定时调用
    """
    start = (datetime.now(timezone.utc) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return fetch_indicators(symbol, start, end)
