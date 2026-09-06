"""
BTC 联动规则回测框架
=====================
跑 E05 / E06 / J01 / J02 四条需要 BTC 数据的规则
(J03 需要板块成员列表,暂 skip)

数据需求:
  - BTCUSDT 1h kline
  - altcoin 1h kline

判定:
  E05: BTC 1h 涨 > 0.5% AND 该币 MS 强 (>1)  -> long
       (文档原: BTC 涨 + 板块冷; 用 MS 近似"该币强")
  E06: BTC 1h 跌 < -0.5% AND 该币 MS 强        -> short
       (文档原: BTC 跌 + 板块热)
  J01: BTC 1h 涨 + 该币 1h 涨但 < BTC 涨       -> long (补涨)
  J02: BTC 1h 涨 + 该币 1h 跌                 -> long (背离补涨)
"""
import sys
import os
import time
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from dotenv import dotenv_values
import _bootstrap  # noqa: F401  — sys.path setup
from lib.data import fetch_kline
from lib.analysis import backtest
from lib.symbols import BTC_CORR_14 as SYMBOLS

# Config
DAYS = 90
HOLD_BARS = 12
FEE = 0.0005
BTC_THRESH_PCT = 0.005  # 0.5%
MIN_TRADES = 5
PERIODS_PER_YEAR = 8760


def load_btc_alt(symbol, start, end):
    """拉 BTC + 该币 1h 数据, 合并"""
    btc = fetch_kline('BTCUSDT', '1h', start, end)
    alt = fetch_kline(symbol, '1h', start, end)
    if btc is None or alt is None:
        return None
    btc_ret = btc['Close'].pct_change()
    alt_ret = alt['Close'].pct_change()
    return {
        'btc_ret_1h': btc_ret,
        'alt_ret_1h': alt_ret,
        'alt_close': alt['Close'],
    }


def make_cond(rid, btc_ret, alt_ret):
    """Compute the entry condition for one of E05/E06/J01/J02."""
    if   rid == 'E05': return (btc_ret >  BTC_THRESH_PCT) & (alt_ret > 0)
    elif rid == 'E06': return (btc_ret < -BTC_THRESH_PCT) & (alt_ret > 0)
    elif rid == 'J01': return (btc_ret > 0) & (alt_ret > 0) & (alt_ret < btc_ret)
    elif rid == 'J02': return (btc_ret >  BTC_THRESH_PCT) & (alt_ret < -BTC_THRESH_PCT)
    raise ValueError(f"unknown rule id: {rid}")


# 4 条规则定义
RULES = [
    ('E05', 'long',  'BTC 涨 + 该币强',  'btc_ret_1h > BTC_THRESH AND alt_ret_1h > 0'),
    ('E06', 'short', 'BTC 跌 + 该币强',  'btc_ret_1h < -BTC_THRESH AND alt_ret_1h > 0'),
    ('J01', 'long',  'BTC 涨 + 该币补涨',  'btc_ret_1h > 0 AND alt_ret_1h > 0 AND alt_ret_1h < btc_ret_1h'),
    ('J02', 'long',  'BTC 涨 + 该币反向',  'btc_ret_1h > BTC_THRESH AND alt_ret_1h < -BTC_THRESH'),
]


def main():
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=DAYS)).strftime('%Y-%m-%d')

    # 1) 拉 BTC
    print(f"Loading BTCUSDT 1h {DAYS}d...")
    btc_full = fetch_kline('BTCUSDT', '1h', start, end)
    btc_ret_1h = btc_full['Close'].pct_change()
    print(f"BTC: {len(btc_full)} bars, BH: {(btc_full['Close'].iloc[-1]/btc_full['Close'].iloc[0]-1)*100:+.2f}%")

    # 2) 逐币种跑
    results = []
    print(f"\nTesting {len(SYMBOLS)} symbols × {len(RULES)} rules...")

    for sym in SYMBOLS:
        sys.stdout.write(f"  {sym}... ")
        sys.stdout.flush()
        try:
            alt = fetch_kline(sym, '1h', start, end)
        except (requests.RequestException, ValueError) as e:
            sys.stdout.write(f"FAIL: {e}\n")
            sys.stdout.flush()
            continue
        if alt is None or len(alt) < 100:
            sys.stdout.write("SKIP\n")
            sys.stdout.flush()
            continue

        alt_ret_1h = alt['Close'].pct_change()
        close = alt['Close']
        n = len(alt)

        for rid, direction, name, cond_str in RULES:
            try:
                if rid == 'E05':
                    cond = (btc_ret_1h > BTC_THRESH_PCT) & (alt_ret_1h > 0)
                elif rid == 'E06':
                    cond = (btc_ret_1h < -BTC_THRESH_PCT) & (alt_ret_1h > 0)
                elif rid == 'J01':
                    cond = (btc_ret_1h > 0) & (alt_ret_1h > 0) & (alt_ret_1h < btc_ret_1h)
                elif rid == 'J02':
                    cond = (btc_ret_1h > BTC_THRESH_PCT) & (alt_ret_1h < -BTC_THRESH_PCT)
                # 对齐索引
                cond = cond.reindex(alt.index).fillna(False)
            except (ValueError, KeyError, TypeError) as e:
                # cond_builder produced wrong shape or columns missing
                print(f"  [skip] {sym}/{rid}: {e}")
                continue

            cond = make_cond(rid, btc_ret_1h, alt_ret_1h).reindex(alt.index).fillna(False)
            r = backtest(cond, close, direction, HOLD_BARS,
                         fee=FEE, min_trades=MIN_TRADES, periods_per_year=PERIODS_PER_YEAR)
            if r is None:
                continue
            results.append({
                'rule': rid, 'name_cn': name, 'symbol': sym,
                'direction': direction,
                **r
            })
        sys.stdout.write("OK\n")
        sys.stdout.flush()

    # 3) 保存结果 + 双方向对比(复用 btc_ret_1h,不重新 fetch)
    df = pd.DataFrame(results)
    if len(df) > 0:
        print(f"\nGenerating dual-direction comparison...")
        opposite_results = []
        for sym in SYMBOLS:
            try:
                alt = fetch_kline(sym, '1h', start, end)
            except:
                continue
            if alt is None or len(alt) < 100:
                continue
            alt_ret_1h2 = alt['Close'].pct_change()
            close = alt['Close']
            for rid, orig_dir, name, cond_str in RULES:
                try:
                    cond = make_cond(rid, btc_ret_1h, alt_ret_1h2).reindex(alt.index).fillna(False)
                except (ValueError, KeyError, TypeError) as e:
                    # cond_builder produced wrong shape or columns missing
                    print(f"  [skip] {sym}/{rid} dual-dir: {e}")
                    continue
                opp_dir = 'short' if orig_dir == 'long' else 'long'
                r = backtest(cond, close, opp_dir, HOLD_BARS,
                             fee=FEE, min_trades=MIN_TRADES, periods_per_year=PERIODS_PER_YEAR)
                if r: opposite_results.append({'rule': rid, 'symbol': sym, **r})
        if opposite_results:
            df_opp = pd.DataFrame(opposite_results).rename(columns={
                'n': 'other_n', 'wr': 'other_wr', 'total': 'other_total',
                'avg': 'other_avg', 'sharpe': 'other_sharpe', 'mdd': 'other_mdd'
            })
            df = df.merge(df_opp, on=['rule', 'symbol'], how='left')
            df['other_dir'] = df['direction'].apply(lambda d: 'short' if d == 'long' else 'long')

    out_path = 'cache/csv/btc_corr_results.csv'
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path} ({len(df)} rows)")

    # 4) 报告
    if len(df) > 0:
        print(f"\n=== E05/E06/J01-J02 Results (14 symbols × 4 rules) ===")
        print(f"{'Rule':<5} {'Symbol':<10} {'Dir':<6} {'N':>3} {'WR%':>6} {'Avg%':>7} {'Total%':>8} {'Sharpe':>7}")
        print("-" * 75)
        top = df.sort_values('sharpe', ascending=False).head(20)
        for _, r in top.iterrows():
            sign = '+' if r['total'] >= 0 else ''
            print(f"{r['rule']:<5} {r['symbol']:<10} {r['direction']:<6} {r['n']:>3} {r['wr']:>5.1f}% {r['avg']:>+6.2f}% {sign}{r['total']:>6.2f}% {r['sharpe']:>7.3f}")

        # 按规则汇总
        print(f"\n=== By Rule ===")
        for rid in ['E05', 'E06', 'J01', 'J02']:
            sub = df[df['rule'] == rid]
            if len(sub) == 0: continue
            print(f"  {rid}: {len(sub)} sym, avg Sharpe={sub['sharpe'].mean():.2f}, "
                  f"win rate {(sub['wr']>50).sum()}/{len(sub)}, "
                  f"avg ret={sub['total'].mean():.1f}%")


if __name__ == '__main__':
    main()
