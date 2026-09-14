"""
Pattern Match — 跨所有币的历史触发模式匹配
============================================

trigger_store 把每币的规则触发布尔时间序列存成了 parquet。
本模块做"推断接下来动作"的引擎:

  输入: 一个触发指纹(哪几条规则同时触发)
  搜索: 跨 cache/triggers/ 里所有币的所有历史小时
  输出: 该指纹历史上出现 N 次,之后 1/4/12/24h 的收益分布
       → "D01+B04 历史上 312 次,12h +1.8%, 64% 概率上涨"

为什么不按单币搜:单币 90 天 × 1h = 2160 根 bar,稀有组合
(比如同时 4 条规则触发)可能整段就 2-3 次,统计没意义。
跨 539 币池 → 539×2160 ≈ 116 万行,稀有组合也能攒够样本。

口径与 trigger_store 一致:查询规则集 = 子集匹配
  (查询的几条规则都 True 即算命中,其余规则不关心)
  —— 这是"这种信号出现后,价格怎么走"的无偏问法。

用法:
  python core/pattern_match.py --rules D01,B04              # 查这个组合跨所有币的历史
  python core/pattern_match.py --rules D01,B04,F02 --horizon 12
  python core/pattern_match.py --scan-current                 # 扫所有币此刻指纹,按历史 edge 排名
  python core/pattern_match.py --coin BTCUSDT                  # 用某币此刻指纹当查询
"""
import sys, io
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import argparse
import time
from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401  — sys.path setup
from rules_catalog.catalog import get_active_rules, get_rule_by_id

TRIGGER_DIR = Path('cache/triggers')
FWD_COLS = ['fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_12h', 'fwd_ret_24h']
HORIZON_LABELS = {'fwd_ret_1h': '1h', 'fwd_ret_4h': '4h',
                  'fwd_ret_12h': '12h', 'fwd_ret_24h': '24h'}


# ── 数据加载:把所有币的 parquet 拼成一张大表 ────────────────────────────
def load_all_triggers(coin_filter=None):
    """
    读 cache/triggers/*.parquet,拼成一张长表:
      index = DatetimeIndex (各币的时间轴并起来)
      coin 列 = 哪个币
      其余列 = 每条规则 bool + Close + fwd_ret_*

    coin_filter: 可选,只载指定币(列表),省内存。None = 全部。
    """
    files = sorted(TRIGGER_DIR.glob('*.parquet'))
    if coin_filter:
        wanted = {c.upper() for c in coin_filter}
        files = [f for f in files if f.stem.upper() in wanted]
    if not files:
        raise FileNotFoundError(
            f"cache/triggers/ 里没有 parquet。先跑: python core/trigger_store.py")

    frames = []
    for f in files:
        df = pd.read_parquet(f)
        df.insert(0, 'coin', f.stem)
        # 保留 DatetimeIndex 作为时间轴,coin 是列
        frames.append(df)
    big = pd.concat(frames, axis=0)
    big.sort_index(inplace=True)
    return big


def _rule_columns(df):
    """返回 df 里的规则布尔列(排除 coin/Close/fwd_ret_)"""
    return [c for c in df.columns
            if c not in ('coin', 'Close') and not c.startswith('fwd_ret_')]


# ── 模式匹配核心 ─────────────────────────────────────────────────────────
def match_pattern(all_triggers, query_rules, min_extra=0):
    """
    子集匹配:query_rules 里的规则全 True 的行 = 命中。
    其余规则不关心(可以是 True 也可以 False)。

    返回命中行(含 coin/time/Close/fwd_ret_*),供 summarize 用。

    min_extra: 额外要求至少 N 条非查询规则也触发(提高信号强度)。
               0 = 只要查询规则命中即可(默认,样本最大)。
    """
    # 只保留存在的规则列
    rule_cols = _rule_columns(all_triggers)
    present = [r for r in query_rules if r in rule_cols]
    missing = [r for r in query_rules if r not in rule_cols]
    if missing:
        print(f"  [warn] 这些规则不在触发库里(可能 skip 或没存): {missing}")
    if not present:
        print(f"  [warn] 没有可查的规则")
        return pd.DataFrame()

    # 子集匹配:present 里所有规则都 True
    mask = all_triggers[present].all(axis=1)
    hits = all_triggers[mask].copy()

    if min_extra > 0:
        # 额外要求:除查询规则外,至少 min_extra 条规则也触发
        other_cols = [c for c in rule_cols if c not in present]
        if other_cols:
            extra_count = hits[other_cols].sum(axis=1)
            hits = hits[extra_count >= min_extra]

    hits['_query'] = '+'.join(present)
    return hits


def summarize(hits, horizons=FWD_COLS):
    """
    对命中行,算每个前视周期的收益分布。
    返回 dict: {horizon: {n, mean, median, p25, p75, win_rate, ...}}
    """
    if len(hits) == 0:
        return {}
    out = {}
    for col in horizons:
        rets = hits[col].dropna()
        n = len(rets)
        if n == 0:
            out[col] = {'n': 0}
            continue
        out[col] = {
            'n': n,
            'mean': float(rets.mean()),
            'median': float(rets.median()),
            'p25': float(rets.quantile(0.25)),
            'p75': float(rets.quantile(0.75)),
            'win_rate': float((rets > 0).mean()),     # 正收益占比
            'std': float(rets.std()),
        }
    return out


def print_summary(query_rules, hits, stats):
    """人话打印"""
    print(f"\n{'='*64}")
    qstr = '+'.join(query_rules)
    print(f"触发指纹: {qstr}")
    print(f"命中样本: {len(hits)} 次(跨所有币 × 所有历史小时)")
    if len(hits) == 0:
        print("  (无命中,检查规则 ID 或先跑 trigger_store 扩样本)")
        return
    # 跨多少个币
    n_coins = hits['coin'].nunique() if 'coin' in hits else 0
    print(f"涉及币种: {n_coins}")
    print(f"时间跨度: {hits.index.min()} ~ {hits.index.max()}")
    print(f"{'='*64}")
    print(f"{'周期':<6} {'样本':>6} {'均值':>8} {'中位':>8} {'p25':>8} {'p75':>8} {'胜率':>7} {'std':>7}")
    print('-' * 64)
    for col in FWD_COLS:
        if col not in stats or stats[col].get('n', 0) == 0:
            continue
        s = stats[col]
        label = HORIZON_LABELS.get(col, col)
        print(f"{label:<6} {s['n']:>6} {s['mean']:>+7.2%} {s['median']:>+7.2%} "
              f"{s['p25']:>+7.2%} {s['p75']:>+7.2%} {s['win_rate']:>6.1%} {s['std']:>7.2%}")
    print('-' * 64)
    # 一句话结论
    best = None
    for col in FWD_COLS:
        s = stats.get(col, {})
        if s.get('n', 0) >= 5:
            if best is None or s['mean'] * s['win_rate'] > best[1] * best[2]:
                best = (HORIZON_LABELS[col], s['mean'], s['win_rate'], s['n'])
    if best:
        h, mean, wr, n = best
        direction = '涨' if mean > 0 else '跌'
        print(f"→ 最显著:{h} 内平均 {mean:+.2%}, {wr:.0%} 概率{direction}, "
              f"样本 {n} 次")


# ── 当前全市场扫描:每个币此刻指纹 → 查历史 edge ─────────────────────────
def scan_current(all_triggers, min_rules=1, top_n=20):
    """
    取每币最后一根 bar(此刻状态),看它的触发指纹,
    然后跨所有历史查"这个指纹之后怎么走",按 edge 排名。

    edge 简版 = 12h 均值收益 × 胜率(样本<10 的丢弃)
    """
    rule_cols = _rule_columns(all_triggers)
    # 每币最后一行
    last_rows = []
    for coin, g in all_triggers.groupby('coin'):
        last = g.tail(1)
        if len(last):
            last_rows.append(last)
    if not last_rows:
        print("  无数据")
        return None
    current = pd.concat(last_rows)

    # 对每币此刻的触发规则集,查历史
    results = []
    for _, row in current.iterrows():
        hits_rules = [r for r in rule_cols if row[r]]
        if len(hits_rules) < min_rules:
            continue
        # 查这个指纹的历史(跨所有币)
        hits = match_pattern(all_triggers, hits_rules)
        stats = summarize(hits)
        s12 = stats.get('fwd_ret_12h', {})
        n, mean, wr = s12.get('n', 0), s12.get('mean', 0), s12.get('win_rate', 0)
        if n < 10:
            continue   # 样本太少不可信
        edge = mean * wr
        results.append({
            'coin': row['coin'],
            'current_rules': '+'.join(hits_rules),
            'n_rules': len(hits_rules),
            'hist_n': n,
            'fwd_12h_mean': mean,
            'fwd_12h_win': wr,
            'edge': edge,
            'close': row['Close'],
        })
    if not results:
        print("  无可排名的币(样本都不足 10,或此刻无触发)")
        return None
    res = pd.DataFrame(results).sort_values('edge', ascending=False)
    print(f"\n{'='*80}")
    print(f"当前全市场扫描 — 按历史 edge 排名(12h 均值×胜率,样本≥10)")
    print(f"{'='*80}")
    print(f"{'币种':<12} {'此刻触发':<28} {'样本':>6} {'12h均':>8} {'胜率':>6} {'edge':>8} {'价':>10}")
    print('-' * 80)
    for _, r in res.head(top_n).iterrows():
        print(f"{r['coin']:<12} {r['current_rules']:<28} {r['hist_n']:>6} "
              f"{r['fwd_12h_mean']:>+7.2%} {r['fwd_12h_win']:>6.1%} "
              f"{r['edge']:>+7.4f} {r['close']:>10.4g}")
    print('-' * 80)
    return res


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Pattern Match — 跨所有币的历史触发模式匹配 + 前视收益分布')
    parser.add_argument('--rules', default=None,
                        help='查询规则组合,逗号分隔, e.g. D01,B04 (留空走 --scan-current)')
    parser.add_argument('--coin', default=None,
                        help='用某币此刻的触发指纹当查询(留空用 --rules)')
    parser.add_argument('--scan-current', action='store_true',
                        help='扫所有币此刻指纹,按历史 edge 排名')
    parser.add_argument('--min-extra', type=int, default=0,
                        help='额外要求至少 N 条非查询规则也触发(提高信号强度)')
    parser.add_argument('--min-rules', type=int, default=1,
                        help='--scan-current 时,币至少触发几条规则才上榜')
    parser.add_argument('--top', type=int, default=20)
    parser.add_argument('--horizon', default=None,
                        choices=['1', '4', '12', '24'],
                        help='只看某个前视周期(默认全看)')
    args = parser.parse_args()

    # 载入所有触发数据
    print("载入所有币的触发序列...")
    t0 = time.time()
    all_trig = load_all_triggers(
        coin_filter=[args.coin] if args.coin else None)
    print(f"  {len(all_trig)} 行, {all_trig['coin'].nunique()} 币, "
          f"{time.time()-t0:.1f}s")

    horizons = FWD_COLS
    if args.horizon:
        horizons = [f'fwd_ret_{args.horizon}h']

    # 模式 A:扫当前全市场
    if args.scan_current:
        scan_current(all_trig, min_rules=args.min_rules, top_n=args.top)
        return 0

    # 模式 B:用某币此刻指纹当查询
    if args.coin:
        coin_df = all_trig[all_trig['coin'] == args.coin.upper()]
        if len(coin_df) == 0:
            print(f"  [ERR] {args.coin} 不在触发库")
            return 1
        last = coin_df.tail(1).iloc[0]
        rule_cols = _rule_columns(all_trig)
        query = [r for r in rule_cols if last[r]]
        if not query:
            print(f"  {args.coin} 此刻无任何规则触发")
            return 0
        print(f"\n{args.coin} 此刻触发: {query}")
        # 去掉 coin 列再查(避免 match 误用)
        hits = match_pattern(all_trig, query, min_extra=args.min_extra)
        stats = summarize(hits, horizons=horizons)
        print_summary(query, hits, stats)
        return 0

    # 模式 C:直接给规则组合
    if args.rules:
        query = [r.strip().upper() for r in args.rules.split(',') if r.strip()]
        hits = match_pattern(all_trig, query, min_extra=args.min_extra)
        stats = summarize(hits, horizons=horizons)
        print_summary(query, hits, stats)
        return 0

    # 没给参数
    parser.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
