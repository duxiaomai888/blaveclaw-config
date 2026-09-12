"""
Coin Screener — 批量币种 × 规则信号扫描
========================================
对一组币种(默认 539 真币 from symbols.xlsx)应用 catalog 规则,
统计每(币, 规则)组合的触发率,输出 top N 候选埋伏币。

典型用法:
  python core/coin_screener.py --rules D01 --top 20 --direction long
  python core/coin_screener.py --rules D01,F03,A06 --top 20
  python core/coin_screener.py --rules A05,A08 --direction short --top 10
  python core/coin_screener.py --rules D01 --days 60 --top 30

数据源: lib/data.py 的 fetch_kline / fetch_holder_concentration /
        fetch_market_sentiment / fetch_taker_intensity / fetch_whale_hunter
规则源: rules_catalog/catalog.py (50 条规则)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

import _bootstrap  # noqa: F401  — sys.path setup
load_dotenv()
from lib.data import (
    fetch_kline, fetch_holder_concentration, fetch_market_sentiment,
    fetch_taker_intensity, fetch_whale_hunter, fetch_liquidation,
    fetch_squeeze_momentum, get_all_headers, drop_unsettled_bar,
    _KeyAwareRateLimiter as _KeyRateLimiter,  # re-export to keep public name stable
)
from rules_catalog.catalog import (
    get_rule_by_id, resolve_param_space,
)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_COINS_FILE = 'symbols.xlsx'
DEFAULT_OUT_DIR    = 'cache/csv'
DEFAULT_INTERVAL   = '1h'
DEFAULT_WORKERS    = 20
DEFAULT_DAYS       = 30
# 限流默认值(per-key):每秒 2 个请求,key 切换间隔 0.3s,批 100 币 sleep 5s
DEFAULT_RPS_PER_KEY    = 2.0
DEFAULT_KEY_COOLDOWN   = 0.3
DEFAULT_BATCH_SIZE     = 100
DEFAULT_BATCH_SLEEP    = 5.0


# ── 数据加载(每币并行,每 worker 用不同 key) ──────────────────────────────
# _KeyRateLimiter is imported from lib.data — single source of truth.
def fetch_coin_data(coin, start, end, headers, interval=DEFAULT_INTERVAL, key_idx=None, limiter=None):
    """
    Fetch kline + 4 个 alpha for one coin.
    失败返回 None(被筛掉)。

    key_idx: 当前 key 索引,用于限流
    limiter: _KeyRateLimiter 实例
    """
    # 限流:每个请求前 acquire。每币 7 个串行请求(kline + 5 alpha + SM),
    # 若只在开头 acquire 一次,key 实际请求率 = 7 × rps_per_key ≈ 8.4 req/s,
    # 远超服务器 500/5min (≈1.67 req/s) 预算,必然系统性 429 + 指数退避。
    if limiter and key_idx is not None:
        limiter.acquire(key_idx)
    try:
        hdrs = headers
        kl   = fetch_kline(coin, interval, start, end, hdrs)
        kl   = drop_unsettled_bar(kl, interval)   # 丢弃未收盘的当前 bar

        if len(kl) < 50:
            return None

        df = kl.copy()

        # 缺哪个 alpha 只废那一个,不让整币失败 —— 与回测侧
        # single_symbol_backtest.load_data 的处理一致。曾只拉 HC/MS/TI/WH,
        # 导致 C 类(SM)和 H 类(LM)9 条规则 KeyError 静默失效。
        def _alpha(name, fn, **kw):
            try:
                if limiter and key_idx is not None:
                    limiter.acquire(key_idx)
                return fn(coin, interval, start, end, hdrs, **kw)['alpha'].ffill()
            except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
                print(f"  [skip] {coin} {name}: {type(e).__name__}")
                return None

        parts = {
            'HC': _alpha('HC', fetch_holder_concentration),
            'MS': _alpha('MS', fetch_market_sentiment),
            'TI': _alpha('TI', fetch_taker_intensity, timeframe='24h'),
            'WH': _alpha('WH', fetch_whale_hunter, timeframe='24h', score_type='score_oi'),
            'LM': _alpha('LM', fetch_liquidation, timeframe='24h'),
        }
        # fetch_squeeze_momentum 签名是 (symbol, start, end, headers) —— 没有 interval,
        # 与其余 5 个不同,单独调。
        try:
            if limiter and key_idx is not None:
                limiter.acquire(key_idx)
            parts['SM'] = fetch_squeeze_momentum(coin, start, end, hdrs)['alpha'].ffill()
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
            print(f"  [skip] {coin} SM: {type(e).__name__}")
            parts['SM'] = None
        for col, s in parts.items():
            df[col] = s if s is not None else np.nan

        df['abs_HC']  = df['HC'].abs()
        df['abs_TI']  = df['TI'].abs()
        df['abs_SM']  = df['SM'].abs()
        df['abs_LM']  = df['LM'].abs()
        df['hc_sign'] = np.sign(df['HC'])
        df['ti_sign'] = np.sign(df['TI'])
        df['lm_sign'] = np.sign(df['LM'])
        df['hc_delta'] = np.sign(df['HC'].diff()).fillna(0)
        df['ret_1h']  = df['Close'].pct_change()
        df['ret_24h'] = df['Close'].pct_change(24)
        # 与 single_symbol_backtest.load_data 的派生列保持同名同义,否则依赖这些列的
        # 规则(D04/F02/F04/F05/G01)在扫描侧静默失效。
        df['new_high_24h'] = df['Close'] >= df['Close'].rolling(24).max().shift(1)
        df['new_low_24h']  = df['Close'] <= df['Close'].rolling(24).min().shift(1)
        return df
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as e:
        # Network / empty data / missing column / dead symbol (HTTP 400 RuntimeError)
        return None


def fetch_all_coins_parallel(coins, start, end, workers=DEFAULT_WORKERS,
                             rps_per_key=DEFAULT_RPS_PER_KEY,
                             key_cooldown=DEFAULT_KEY_COOLDOWN,
                             batch_size=DEFAULT_BATCH_SIZE,
                             batch_sleep=DEFAULT_BATCH_SLEEP):
    """
    并行 fetch 所有币的 kline + 4 个 alpha。
    用 get_all_headers() 给每个 worker 分配不同 API key,避免单 key 限流。

    限流参数:
      rps_per_key: 每秒每 key 允许的请求数(默认 2/s,= 6 keys 合计 12/s)
      key_cooldown: 同一 key 连续请求间隔(秒)
      batch_size:   每批跑多少币;批间 sleep batch_sleep 秒,等限流冷却
      batch_sleep:  批间 sleep 秒数
    """
    all_headers = get_all_headers()
    n_keys = len(all_headers)
    print(f"  Using {n_keys} API keys, {workers} workers")
    print(f"  Rate limit: {rps_per_key} req/s/key (= {rps_per_key * n_keys:.0f} req/s total),"
          f" key_cooldown={key_cooldown}s, batch={batch_size}/{batch_sleep}s")

    limiter = _KeyRateLimiter(rps_per_key=rps_per_key)
    results = {}
    t0 = time.time()

    # 分批
    for batch_start in range(0, len(coins), batch_size):
        batch = coins[batch_start: batch_start + batch_size]
        batch_no = batch_start // batch_size + 1
        total_batches = (len(coins) + batch_size - 1) // batch_size
        print(f"  Batch {batch_no}/{total_batches} ({len(batch)} coins)...")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, coin in enumerate(batch):
                key_idx = (batch_start + i) % n_keys
                hdrs    = all_headers[key_idx]
                futures[pool.submit(
                    fetch_coin_data, coin, start, end, hdrs,
                    key_idx=key_idx, limiter=limiter,
                )] = coin

            for fut in as_completed(futures):
                coin = futures[fut]
                try:
                    df = fut.result()
                except (requests.RequestException, ValueError, KeyError, RuntimeError):
                    # Worker raised network/data/column/dead-symbol error — skip this coin
                    df = None
                results[coin] = df

        done = batch_start + len(batch)
        ok   = sum(1 for v in results.values() if v is not None)
        elapsed = time.time() - t0
        print(f"    [{done}/{len(coins)}] ok={ok} failed={done-ok} ({elapsed:.1f}s)", flush=True)

        # 批间冷却
        if batch_start + batch_size < len(coins) and batch_sleep > 0:
            print(f"    Sleeping {batch_sleep}s to let rate limit cool down...")
            time.sleep(batch_sleep)

    elapsed = time.time() - t0
    ok = sum(1 for v in results.values() if v is not None)
    print(f"  Fetch done: {ok}/{len(coins)} coins OK in {elapsed:.1f}s")
    return results


# ── 评分 ──────────────────────────────────────────────────────────────────
def score_coin_rule(coin_df, rule, direction_override=None, threshold_mode='default'):
    """
    对一个币在一条规则下评分。
    返回 dict {trigger_rate, n_triggers, score, alpha_avg, ...}
    threshold_mode:
      - 'default': 用 rule['param_space'] 的中位值/默认值
      - 'adaptive': 用每币 p90(更公平,小币也能触发)
    """
    if coin_df is None or len(coin_df) < 50:
        return None

    # 解决参数 — 复用 catalog.resolve_param_space
    # quantile_* 必须先解析成数值:不解析的话 cond_builder 收到字符串
    # 'quantile_50_95',pandas 数值 Series 比字符串直接 TypeError,规则静默返回
    # None。default 模式曾因此漏掉 23/45 条规则且无任何警告。
    try:
        param_space = resolve_param_space(
            rule, coin_df, mode='adaptive' if threshold_mode == 'adaptive' else 'default')
        # 扫描取单档(中位),不做 OR 合并 —— 与文档"单一判定条件"的写法对齐。
        # 多档参数扫描是回测侧的职责(core/single_symbol_backtest.py)。
        param_space = {
            k: [v[len(v) // 2]] if isinstance(v, list) and v else v
            for k, v in param_space.items()}
        # 列全 NaN 时 resolve_param_space 返回原始字符串(如 'quantile_50_95'),
        # 取中位会退化成单个字符,cond_builder 收到后抛 TypeError 被下面的
        # except 静默跳过。这里显式检测并单独报出,与上方"全灭警告"同口径。
        if any(isinstance(v, str) for v in param_space.values()):
            print(f"  [skip] {rule['id']}: alpha 列全 NaN,阈值无法解析")
            return None
    except (ValueError, KeyError, TypeError) as e:
        print(f"  [skip] {rule['id']}: param_space resolve failed: {e}")
        return None

    # 跑 cond_builder
    from itertools import product
    keys = list(param_space.keys())
    if not keys:
        return None
    values_list = [param_space[k] for k in keys]

    triggers_combined = None
    for combo in product(*values_list):
        p = dict(zip(keys, combo))
        try:
            cond = rule['cond_builder'](coin_df, p)
            if not isinstance(cond, pd.Series):
                cond = pd.Series(cond, index=coin_df.index)
            cond = cond.fillna(False).astype(bool)
        except (ValueError, KeyError, TypeError):
            # cond_builder produced non-bool / wrong shape — skip this rule
            continue
        if triggers_combined is None:
            triggers_combined = cond
        else:
            triggers_combined = triggers_combined | cond   # union across param combos

    if triggers_combined is None:
        return None

    n_triggers = int(triggers_combined.sum())
    trigger_rate = n_triggers / len(triggers_combined)

    # 业务逻辑:检查方向
    direction_doc = rule.get('direction_doc', 'long')
    direction = direction_override or direction_doc

    # 信号强度 alpha 平均(对相关 alpha 字段)
    alpha_fields = []
    for k in keys:
        if k.startswith('hc_') or k == 'abs_hc_th':
            alpha_fields.append('HC')
        elif k.startswith('ti_'):
            alpha_fields.append('TI')
        elif k.startswith('ms_'):
            alpha_fields.append('MS')
        elif k.startswith('wh_'):
            alpha_fields.append('WH')
    alpha_avgs = {f: float(coin_df[f].mean()) for f in alpha_fields if f in coin_df.columns}

    return {
        'trigger_rate': float(trigger_rate),
        'n_triggers':   n_triggers,
        # 末根(已收盘)bar 是否触发 —— 这才是"现在该不该动手"的答案。
        # trigger_rate 是历史累计频率,量错了东西:B01 19.7% 意味着每 5 根 bar
        # 触发一次,阈值对那个币太松,规则没有区分度。
        'last_hit':     bool(triggers_combined.iloc[-1]),
        'alpha_avg':    alpha_avgs,
        'direction':    direction,
    }


# ── Main screener ─────────────────────────────────────────────────────────
def run_screener(coins, rule_ids, days=DEFAULT_DAYS, direction='long',
                 top_n=20, threshold_mode='default', output_csv=None, workers=20,
                 rps_per_key=DEFAULT_RPS_PER_KEY, key_cooldown=DEFAULT_KEY_COOLDOWN,
                 batch_size=DEFAULT_BATCH_SIZE, batch_sleep=DEFAULT_BATCH_SLEEP):
    """
    主入口
    """
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # --direction both 的语义是"不覆盖方向,用每条规则自己的文档方向"。
    # 直接把 'both' 传下去会被当成方向值:输出里 'both' != 'long' 就走"做空"分支,
    # B01(文档 long)会被标成做空 —— 方向标错就是下错单,这里显式归一。
    if direction == 'both':
        direction = None

    # 解析规则
    rules = []
    for rid in rule_ids:
        r = get_rule_by_id(rid)
        if r is None:
            print(f"  [warn] Rule {rid} not found, skipping")
            continue
        if r.get('skip', False):
            print(f"  [skip] Rule {rid} ({r.get('skip_reason', 'skip')}), skipping")
            continue
        rules.append(r)
    if not rules:
        print("  No valid rules. Abort.")
        return None

    print(f"=== Coin Screener ===")
    print(f"  Coins:     {len(coins)}")
    print(f"  Rules:     {[r['id'] for r in rules]}")
    print(f"  Window:    {start} ~ {end} ({days} days)")
    print(f"  Direction: {direction if direction else 'both(不覆盖,用文档方向)'}")
    print(f"  Top:       {top_n}")
    print(f"  Threshold: {threshold_mode}")
    print(f"  Workers:   {workers}, rate: {rps_per_key} req/s/key, batch: {batch_size}/{batch_sleep}s")
    print()

    # 1. Fetch 数据
    print(f"[1/3] Fetching data for {len(coins)} coins...")
    coin_data = fetch_all_coins_parallel(
        coins, start, end, workers=workers,
        rps_per_key=rps_per_key, key_cooldown=key_cooldown,
        batch_size=batch_size, batch_sleep=batch_sleep,
    )

    # 2. 评分
    print(f"\n[2/3] Scoring {len(coin_data)} coins × {len(rules)} rules...")
    rows = []
    skip_count = {}   # rule_id -> 返回 None 的币数
    t0 = time.time()
    for i, (coin, df) in enumerate(coin_data.items(), 1):
        for rule in rules:
            score = score_coin_rule(df, rule, direction_override=direction, threshold_mode=threshold_mode)
            if score is None:
                skip_count[rule['id']] = skip_count.get(rule['id'], 0) + 1
                continue
            row = {
                'coin':         coin,
                'rule':         rule['id'],
                'rule_name':    rule['name_cn'],
                'direction':    score['direction'],
                'last_hit':     score['last_hit'],
                'trigger_rate': round(score['trigger_rate'] * 100, 2),   # %
                'n_triggers':   score['n_triggers'],
                'n_bars':       len(df) if df is not None else 0,
                'alpha_HC_avg': round(score['alpha_avg'].get('HC', 0), 3) if 'HC' in score['alpha_avg'] else None,
                'alpha_TI_avg': round(score['alpha_avg'].get('TI', 0), 3) if 'TI' in score['alpha_avg'] else None,
                'alpha_MS_avg': round(score['alpha_avg'].get('MS', 0), 3) if 'MS' in score['alpha_avg'] else None,
                'alpha_WH_avg': round(score['alpha_avg'].get('WH', 0), 3) if 'WH' in score['alpha_avg'] else None,
            }
            rows.append(row)
        if i % 100 == 0:
            print(f"    [{i}/{len(coin_data)}] coins scored ({time.time()-t0:.1f}s)", flush=True)
    print(f"  Scoring done: {len(rows)} rows in {time.time()-t0:.1f}s")
    # 全部币都无结果的规则要显式报出。静默跳过是最坏的失败方式:default 模式曾
    # 让 31/45 条规则返回 None 而输出看起来完全正常,只有触发率分布异常才露馅。
    dead_rules = {r: n for r, n in skip_count.items() if n >= len(coin_data)}
    if dead_rules:
        print(f"  ⚠️  {len(dead_rules)}/{len(rules)} 条规则在所有币上都无结果,未进入排名:")
        for rid in sorted(dead_rules):
            print(f"       {rid} — 检查 cond_builder 依赖的列是否存在于 fetch_coin_data")

    df_res = pd.DataFrame(rows)
    if len(df_res) == 0:
        print("  No results. Abort.")
        return None

    # 3. 排名 + 输出
    print(f"\n[3/3] Ranking & output...")

    # 末根(已收盘)bar 触发 = 此刻可执行的信号,放在前面。
    # 下面的 trigger_rate 排名量的是历史累计频率,不是当前状态。
    hits = df_res[df_res['last_hit']]
    print(f"\n=== 末根(已收盘)bar 触发 — {len(hits)} 个(币,规则)对 ===")
    if len(hits) == 0:
        print("  (无 — 此刻没有规则触发。这不是失败:低频规则集在任意时刻本来就多数无信号)")
    else:
        for r in hits.itertuples():
            print(f"  {r.coin:<14} {r.rule:<4} {r.rule_name:<16} "
                  f"{'做多' if r.direction == 'long' else '做空'}   N={r.n_triggers:>3}")

    # 综合评分:对每条规则单独取 top,然后也输出"跨规则综合"top
    print(f"\n=== Top {top_n} per rule (历史触发率,不是当前状态) ===")
    summary_per_rule = {}
    for rule in rules:
        sub = df_res[df_res['rule'] == rule['id']].sort_values('trigger_rate', ascending=False)
        top_rule = sub.head(top_n)
        summary_per_rule[rule['id']] = {
            'rule_name':  rule['name_cn'],
            'n_valid':    len(sub),
            'top':        top_rule.to_dict(orient='records'),
        }
        print(f"\n--- {rule['id']} ({rule['name_cn']}, {rule['direction_doc']}) — Top {min(top_n, len(sub))} ---")
        for j, r in enumerate(top_rule.itertuples(), 1):
            print(f"  {j:>2}. {r.coin:<12} trigger={r.trigger_rate:>5.1f}%  N={r.n_triggers:>3}  "
                  f"HC={r.alpha_HC_avg if r.alpha_HC_avg is not None else 'N/A'}  "
                  f"MS={r.alpha_MS_avg if r.alpha_MS_avg is not None else 'N/A'}")

    # 跨规则综合:每币在所有规则上的 trigger_rate 平均
    if len(rules) > 1:
        print(f"\n=== Top {top_n} cross-rule (avg trigger_rate) ===")
        cross = df_res.groupby('coin').agg(
            avg_trigger=('trigger_rate', 'mean'),
            n_rules_hit=('trigger_rate', lambda x: (x > 0).sum()),
            total_triggers=('n_triggers', 'sum'),
        ).reset_index().sort_values(['avg_trigger', 'n_rules_hit'], ascending=False)
        for j, r in cross.head(top_n).iterrows():
            print(f"  {r['coin']:<12} avg_trigger={r['avg_trigger']:>5.1f}%  "
                  f"n_rules_hit={int(r['n_rules_hit'])}/{len(rules)}  "
                  f"total_N={int(r['total_triggers'])}")

    # 落 CSV
    if output_csv is None:
        rules_str = '_'.join(rule_ids)
        output_csv = f"{DEFAULT_OUT_DIR}/screener_{rules_str}_{end}.csv"
    os.makedirs(os.path.dirname(output_csv) or '.', exist_ok=True)
    df_res.to_csv(output_csv, index=False, encoding='utf-8-sig')
    print(f"\nSaved: {output_csv}")
    return df_res


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='Coin Screener — find coins with strongest rule signals')
    parser.add_argument('--rules', required=True, help='逗号分隔的规则 ID, e.g. D01 或 D01,F03,A06')
    parser.add_argument('--coins', default=DEFAULT_COINS_FILE, help=f'币池文件 (default {DEFAULT_COINS_FILE})')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS, help=f'回看天数 (default {DEFAULT_DAYS})')
    parser.add_argument('--direction', default='long', choices=['long', 'short', 'both'],
                        help='方向过滤 (default long)')
    parser.add_argument('--top', type=int, default=20, help='每规则 top N (default 20)')
    parser.add_argument('--threshold', default='default', choices=['default', 'adaptive'],
                        help='阈值模式: default=规则固定, adaptive=每币 p90')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help=f'并行 worker 数 (default {DEFAULT_WORKERS})')
    parser.add_argument('--out', default=None, help='输出 CSV 路径')
    # 限流参数
    parser.add_argument('--rps-per-key', type=float, default=DEFAULT_RPS_PER_KEY,
                        help=f'每 key 每秒请求数 (default {DEFAULT_RPS_PER_KEY}; 6 keys → 12 req/s)')
    parser.add_argument('--key-cooldown', type=float, default=DEFAULT_KEY_COOLDOWN,
                        help=f'同 key 连续请求最小间隔秒 (default {DEFAULT_KEY_COOLDOWN})')
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE,
                        help=f'分批跑,每批多少币 (default {DEFAULT_BATCH_SIZE}; 0=不分批)')
    parser.add_argument('--batch-sleep', type=float, default=DEFAULT_BATCH_SLEEP,
                        help=f'批间 sleep 秒 (default {DEFAULT_BATCH_SLEEP}; 0=不 sleep)')
    parser.add_argument('--no-batch', action='store_true', help='不分批(单批跑完所有币)')
    args = parser.parse_args()

    # 读币池
    coins_path = Path(args.coins)
    if not coins_path.exists():
        print(f"  [ERR] Coins file not found: {args.coins}")
        return 1

    if coins_path.suffix == '.xlsx':
        df_coins = pd.read_excel(args.coins)
    else:
        df_coins = pd.read_csv(args.coins, header=None)
    coins = df_coins.iloc[:, 0].dropna().astype(str).tolist()
    coins = [c for c in coins if c.endswith('USDT')]
    print(f"Loaded {len(coins)} coins from {args.coins}\n")

    rule_ids = [r.strip() for r in args.rules.split(',') if r.strip()]
    run_screener(
        coins=coins,
        rule_ids=rule_ids,
        days=args.days,
        direction=args.direction,
        top_n=args.top,
        threshold_mode=args.threshold,
        output_csv=args.out,
        workers=args.workers,
        rps_per_key=args.rps_per_key,
        key_cooldown=args.key_cooldown,
        batch_size=len(coins) if args.no_batch else args.batch_size,
        batch_sleep=args.batch_sleep,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
