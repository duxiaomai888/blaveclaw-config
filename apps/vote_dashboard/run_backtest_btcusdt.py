"""
run_backtest_btcusdt.py — 用 BBAC-D 缓存数据跑 BTCUSHT 1h 回测
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from apps.vote_dashboard.vote_strategy import VoteSignal, run_backtest

CACHE = ROOT / "cache"


def load_btcusdt_1h() -> pd.DataFrame:
    """合并 K 线 + 5 个 α 指标 (HC/MS/WH/TI/LM)"""
    # 找最新最全的 K 线缓存
    kline_files = sorted(CACHE.glob("kline_1h_BTCUSDT_2022-01-01.parquet"))
    assert kline_files, "没找到 kline_1h_BTCUSDT 缓存"
    df = pd.read_parquet(kline_files[0])
    print(f"[KLINE] {df.shape}, {df.index[0]} ~ {df.index[-1]}")

    # α 指标
    indicators = {
        "HC": "holder_concentration_1h_BTCUSDT_2022-01-01.parquet",
        "MS": "market_sentiment_1h_BTCUSDT_2022-01-01.parquet",
        "WH": "wh_1h_BTCUSDT_2022-01-01.parquet",  # 1h WH (alpha 列)
        "TI": None,
        "LM": "liquidation_1h_BTCUSDT_24h_2025-12-09.parquet",
    }
    # 自动找 TI
    for f in CACHE.iterdir():
        if "taker_intensity" in f.name and "btcusdt" in f.name.lower() and "1h" in f.name and "2022" in f.name:
            indicators["TI"] = f.name

    for col, fname in indicators.items():
        if fname is None:
            print(f"[{col}] no file found, skip")
            continue
        fp = CACHE / fname
        if not fp.exists():
            print(f"[{col}] missing {fname}")
            continue
        ind = pd.read_parquet(fp)
        if "alpha" in ind.columns:
            ind = ind.rename(columns={"alpha": col})
        else:
            ind.columns = [col]
        df = df.join(ind[[col]], how="left")
        nulls = df[col].isna().sum()
        print(f"[{col:3s}] joined {ind.shape}, nulls={nulls} ({nulls/len(df)*100:.1f}%)")

    # ffill 缺失值(1h K 线, α 偶尔缺失)
    for col in ["HC", "MS", "WH", "TI", "LM"]:
        if col in df.columns:
            df[col] = df[col].ffill().bfill()

    # realized_vol
    log_ret = np.log(df["Close"] / df["Close"].shift(1))
    df["realized_vol"] = log_ret.rolling(720).std() * np.sqrt(8760)
    df["realized_vol"] = df["realized_vol"].bfill()

    print(f"\n[FINAL] {df.shape}, columns: {df.columns.tolist()}")
    print(f"  range: {df.index[0]} ~ {df.index[-1]}")
    return df


def main():
    print("=" * 60)
    print("VoteStrategy — BTCUSDT 1h 回测")
    print("=" * 60)
    df = load_btcusdt_1h()

    # ── 参数扫描 ──
    param_grid = [
        # (hc_long, hc_strong, ms_long, ms_strong, ti_th, ti_log, wh_long, wh_strong, min_agree, score_th, confirm, exit, max_hold)
        # 默认参数
        {"name": "default",
         "hc_th_long": 0.5, "hc_th_strong": 1.5,
         "ms_th_long": 0.5, "ms_th_strong": 1.5,
         "ti_th": 1.0, "ti_strong_log": 0.5,
         "wh_th_long": 0.3, "wh_th_strong": 1.0,
         "min_agree": 3, "score_threshold": 0.2,
         "confirm_bars": 3, "exit_bars": 5, "max_hold_bars": 48},
        # 保守: 更严的确认, 更长的持仓
        {"name": "conservative",
         "hc_th_long": 0.7, "hc_th_strong": 2.0,
         "ms_th_long": 0.7, "ms_th_strong": 2.0,
         "ti_th": 1.1, "ti_strong_log": 0.6,
         "wh_th_long": 0.5, "wh_th_strong": 1.5,
         "min_agree": 4, "score_threshold": 0.3,
         "confirm_bars": 5, "exit_bars": 7, "max_hold_bars": 72},
        # 激进: 更宽松, 更快进出
        {"name": "aggressive",
         "hc_th_long": 0.3, "hc_th_strong": 1.0,
         "ms_th_long": 0.3, "ms_th_strong": 1.0,
         "ti_th": 0.9, "ti_strong_log": 0.4,
         "wh_th_long": 0.2, "wh_th_strong": 0.7,
         "min_agree": 2, "score_threshold": 0.15,
         "confirm_bars": 2, "exit_bars": 3, "max_hold_bars": 24},
    ]

    results = []
    for cfg in param_grid:
        name = cfg.pop("name")
        print(f"\n>>> 配置: {name}")
        vs = VoteSignal(**cfg)
        result = run_backtest(df, vs=vs, verbose=True)
        result["stats"]["config_name"] = name
        results.append(result["stats"])

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("汇总对比 (按 Sharpe 排序)")
    print("=" * 60)
    rdf = pd.DataFrame(results).sort_values("sharpe", ascending=False, na_position="last")
    cols = ["config_name", "total_return_pct", "benchmark_return_pct",
            "sharpe", "sortino", "mdd_pct", "n_trades", "fee_total_pct"]
    print(rdf[cols].to_string(index=False))

    # 保存
    rdf.to_csv(ROOT / "apps" / "vote_dashboard" / "backtest_results.csv", index=False)
    print(f"\n结果已保存: apps/vote_dashboard/backtest_results.csv")


if __name__ == "__main__":
    main()
