"""
J03 板块内联动规则回测
=======================
"同板块 ≥ 3 币 1h 涨 > 1% + 该币未涨 -> 板块补涨"

板块代理:用已有币种按类型分(因为 Blave API 不返回板块成员)
- AI: FET, AKT, TAO, ICP, AGIX, RENDER
- L2: ARB, OP, IMX, MNT, STRK
- Meme: DOGE, SHIB, PEPE, FLOKI, BONK, WIF, MEME, TURBO
- DeFi: UNI, AAVE, CRV, MKR, COMP, SUSHI, CAKE, SNX
- Privacy: XMR, ZEC, DASH, LTC, BCH
- Storage: FIL, STX, RNDR
"""
import sys
import os
import time
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import _bootstrap  # noqa: F401  — sys.path setup
from lib.data import fetch_kline
from lib.analysis import backtest
from lib.symbols import SECTORS

# Config
DAYS = 90
HOLD_BARS = 12
FEE = 0.0005
PCT_THRESH = 0.01  # 1%
MIN_TRADES = 5
MIN_SAME_SECTOR = 3  # 至少 3 币同板块动
PERIODS_PER_YEAR = 8760


def main():
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

    # 加载所有币种 1h 收益
    print("Loading 1h data for all sectors...")
    rets = {}
    all_syms = sorted({s for syms in SECTORS.values() for s in syms})
    for sym in all_syms:
        try:
            kl = fetch_kline(sym, '1h', start, end)
            if kl is not None and len(kl) > 100:
                rets[sym] = kl['Close'].pct_change()
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"  [skip] {sym}: {e}")

    print(f"Loaded {len(rets)} symbols")

    # 跑 J03: 同板块 ≥ 3 币 1h 涨 > 1% + 该币未涨 -> long
    results = []
    for sector, symbols in SECTORS.items():
        for sym in symbols:
            if sym not in rets:
                continue
            sys.stdout.write(f"  {sector} {sym}... ")
            sys.stdout.flush()
            try:
                close = fetch_kline(sym, '1h', start, end)['Close']
            except (requests.RequestException, ValueError, KeyError) as e:
                print(f"  [skip] {sector}/{sym}: {e}")
                sys.stdout.write("SKIP\n")
                sys.stdout.flush()
                continue

            # 计算同板块其他币种动量均值
            other_syms = [s for s in symbols if s != sym and s in rets]
            if len(other_syms) < MIN_SAME_SECTOR - 1:
                sys.stdout.write("SKIP(少币)\n")
                sys.stdout.flush()
                continue

            other_avg = pd.concat([rets[s] for s in other_syms], axis=1).mean(axis=1)

            # 对齐索引 + 去除 NaN
            df_aligned = pd.DataFrame({
                'other_avg': other_avg,
                'self_ret': rets[sym],
                'close': close
            }).dropna()
            if len(df_aligned) < 50:
                sys.stdout.write("SKIP(数据少)\n")
                sys.stdout.flush()
                continue
            other_avg = df_aligned['other_avg']
            self_ret = df_aligned['self_ret']
            close = df_aligned['close']

            # J03 long: 同板块均涨 > 1% + 该币未涨(< 0)
            cond_long = (other_avg > PCT_THRESH) & (self_ret < 0)
            # J03 short: 同板块均跌 < -1% + 该币未跌(> 0)
            cond_short = (other_avg < -PCT_THRESH) & (self_ret > 0)

            r_long  = backtest(cond_long,  close, 'long',  HOLD_BARS,
                               fee=FEE, min_trades=MIN_TRADES, periods_per_year=PERIODS_PER_YEAR)
            r_short = backtest(cond_short, close, 'short', HOLD_BARS,
                               fee=FEE, min_trades=MIN_TRADES, periods_per_year=PERIODS_PER_YEAR)

            for direction, r in [('long', r_long), ('short', r_short)]:
                if r is None:
                    continue
                results.append({
                    'rule': 'J03', 'name_cn': f'板块内联动({sector})',
                    'symbol': sym, 'direction': direction,
                    **r
                })
            sys.stdout.write("OK\n")
            sys.stdout.flush()

    df = pd.DataFrame(results)
    out_path = 'cache/csv/j03_sector_results.csv'
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path} ({len(df)} rows)")

    # 报告
    if len(df) > 0:
        print(f"\n=== J03 板块内联动 (按板块汇总) ===")
        df['sector'] = df['name_cn'].str.extract(r'\((.+?)\)')
        for sector, grp in df.groupby('sector'):
            print(f"\n--- {sector} ---")
            for direction, grp_dir in grp.groupby('direction'):
                if len(grp_dir) == 0: continue
                print(f"  {direction}: {len(grp_dir)} sym, "
                      f"avg Sharpe={grp_dir['sharpe'].mean():.2f}, "
                      f"WR={(grp_dir['wr']>50).sum()}/{len(grp_dir)}, "
                      f"avg ret={grp_dir['total'].mean():.1f}%")


if __name__ == '__main__':
    main()
