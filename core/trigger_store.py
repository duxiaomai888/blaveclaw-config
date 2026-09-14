"""
Trigger Store — 全市场规则触发时间序列存储
============================================

把 coin_screener 扔掉的"触发布尔时间序列"存下来,作为意图推断的地基。

与 coin_screener.py 的关系:
  - 复用 fetch_coin_data / fetch_all_coins_parallel(import,不改它一行)
  - 复用 catalog.get_active_rules / resolve_param_space(只读)
  - 复刻 score_coin_rule 里 cond_builder 的参数解析口径(取中位值,
    与扫描器一致),保证存下来的触发序列 = 扫描器 last_hit 的完整时间线

输出: cache/triggers/{COIN}.parquet
  - index:                  OpenTime(小时频率)
  - 每条 active rule 一列:   bool,该小时是否触发
  - Close:                   收盘价(算前视收益用)
  - fwd_ret_1h/4h/12h/24h:   T 时刻起未来 1/4/12/24h 的收益(前视,末尾 NaN)

用法:
  python core/trigger_store.py                       # 全市场 539 币,默认 30 天
  python core/trigger_store.py --days 90             # 90 天
  python core/trigger_store.py --coins midcap_symbols.csv   # 指定币池
  python core/trigger_store.py --rules D01,B04,F02   # 只存几条规则
  python core/trigger_store.py --workers 20          # 并发

不修改任何现有文件。删掉 cache/triggers/ 即可完全回退。
"""
import sys, io
# stdout 包装加守卫:避免 import coin_screener 时它再包一次,
# 第二次 TextIOWrapper 会 close 掉前一个的底层 buffer,导致后续
# argparse --help 报 "I/O operation on closed file"。
# (直接运行本脚本时包装一次即可;被 import 时不重复包装)
if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta
from itertools import product

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401  — sys.path setup,使 from lib.* / rules_catalog.* 可用
from dotenv import load_dotenv
load_dotenv()

# 复用现有扫描器的数据层(只读 import,不改它)
from core.coin_screener import (
    fetch_coin_data,
    fetch_all_coins_parallel,
    DEFAULT_COINS_FILE,
    DEFAULT_INTERVAL,
    DEFAULT_WORKERS,
    DEFAULT_RPS_PER_KEY,
    DEFAULT_KEY_COOLDOWN,
    DEFAULT_BATCH_SIZE,
    DEFAULT_BATCH_SLEEP,
)
from rules_catalog.catalog import (
    get_active_rules,
    get_rule_by_id,
    resolve_param_space,
)

# ── 存储 ───────────────────────────────────────────────────────────────────
TRIGGER_DIR = Path('cache/triggers')
FWD_HORIZONS = [1, 4, 12, 24]   # 前视收益的小时数

# Blave 限流:100 req / 5min / per-key = 0.333 req/s per-key。
# 多 key 轮询存在正是因为限额是 per-key;8 key × 0.30 = 2.4 req/s,
# 539 币 × 7 请求 ≈ 3773 req / 2.4 ≈ 26 分钟(理论上限,实测留余量)。
TRIGGER_RPS_PER_KEY = 0.30
TRIGGER_WORKERS = 8


# ── 单币触发序列(复刻 score_coin_rule 的参数口径) ──────────────────────
def compute_trigger_series(coin_df, rules, threshold_mode='adaptive'):
    """
    对一个币,跑每条 active rule 的 cond_builder,返回宽表:
      index = coin_df.index (小时)
      每条 rule 一列 bool

    参数解析口径与 coin_screener.score_coin_rule 完全一致:
      resolve_param_space → 取每维中位值 → 单组参数 → cond_builder → bool Series
    (多档参数扫描是回测侧的职责,扫描/存储侧只取单档中位,保持一致)
    """
    if coin_df is None or len(coin_df) < 50:
        return None

    trigger_cols = {}
    skipped = []
    for rule in rules:
        if rule.get('skip', False):
            continue
        # 参数解析 —— 复刻 score_coin_rule 的逻辑
        try:
            ps = resolve_param_space(
                rule, coin_df,
                mode='adaptive' if threshold_mode == 'adaptive' else 'default')
            # 取每维中位值(与 score_coin_rule 同口径)
            ps = {
                k: [v[len(v) // 2]] if isinstance(v, list) and v else v
                for k, v in ps.items()
            }
            # alpha 列全 NaN 时 resolve 返回原始字符串,跳过(与扫描器同口径)
            if any(isinstance(v, str) for v in ps.values()):
                skipped.append(rule['id'])
                continue
        except (ValueError, KeyError, TypeError):
            skipped.append(rule['id'])
            continue

        keys = list(ps.keys())
        values_list = [ps[k] for k in keys]
        triggers_combined = None
        for combo in product(*values_list):
            p = dict(zip(keys, combo))
            try:
                cond = rule['cond_builder'](coin_df, p)
                if not isinstance(cond, pd.Series):
                    cond = pd.Series(cond, index=coin_df.index)
                cond = cond.fillna(False).astype(bool)
            except (ValueError, KeyError, TypeError):
                continue
            if triggers_combined is None:
                triggers_combined = cond
            else:
                triggers_combined = triggers_combined | cond

        if triggers_combined is None:
            skipped.append(rule['id'])
            continue
        trigger_cols[rule['id']] = triggers_combined

    if not trigger_cols:
        return None

    out = pd.DataFrame(trigger_cols, index=coin_df.index)
    out.attrs['_skipped_rules'] = skipped   # 顺手记下哪些规则没跑出来
    return out


def add_forward_returns(coin_df, trigger_df):
    """在 trigger_df 上加 Close + 前视收益列(供 pattern_match 查"之后涨没涨")"""
    trigger_df = trigger_df.copy()
    close = coin_df['Close']
    trigger_df['Close'] = close
    for h in FWD_HORIZONS:
        # T 时刻的未来 h 小时收益 = Close(T+h)/Close(T) - 1
        trigger_df[f'fwd_ret_{h}h'] = close.shift(-h) / close - 1
    return trigger_df


# ── 主流程 ────────────────────────────────────────────────────────────────
def _save_coin(coin, df, rules, threshold_mode):
    """算一个币的触发序列 + 前视收益,存 parquet,返回元数据(或 None)。"""
    triggers = compute_trigger_series(df, rules, threshold_mode=threshold_mode)
    if triggers is None or len(triggers) == 0:
        return None
    triggers = add_forward_returns(df, triggers)
    out_path = TRIGGER_DIR / f"{coin}.parquet"
    triggers.to_parquet(out_path)
    rule_cols = [c for c in triggers.columns if c not in ('Close',) and not c.startswith('fwd_ret_')]
    return {
        'coin': coin,
        'n_bars': len(triggers),
        'n_rules_triggered': int((triggers[rule_cols].sum() > 0).sum()),
        'total_trigger_events': int(triggers[rule_cols].sum().sum()),
        'skipped_rules': len(triggers.attrs.get('_skipped_rules', [])),
    }


def run_trigger_store(coins, rule_ids=None, days=30, threshold_mode='adaptive',
                      workers=TRIGGER_WORKERS, rps_per_key=TRIGGER_RPS_PER_KEY,
                      key_cooldown=DEFAULT_KEY_COOLDOWN, batch_size=DEFAULT_BATCH_SIZE,
                      batch_sleep=DEFAULT_BATCH_SLEEP, skip_existing=False):
    """
    扫全市场,把每币的规则触发时间序列存成 parquet。

    边拉边存(每批 fetch 完立即存盘)——中断不丢数据,下次 --skip-existing 可续跑。
    rule_ids=None 表示用全部 active 规则。
    """
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # 解析规则
    if rule_ids:
        rules = []
        for rid in rule_ids:
            r = get_rule_by_id(rid)
            if r is None:
                print(f"  [warn] 规则 {rid} 不存在,跳过")
                continue
            if r.get('skip', False):
                print(f"  [skip] 规则 {rid} ({r.get('skip_reason', 'skip')})")
                continue
            rules.append(r)
    else:
        rules = get_active_rules()

    if not rules:
        print("  无可用规则,终止。")
        return None

    TRIGGER_DIR.mkdir(parents=True, exist_ok=True)

    # --skip-existing: 跳过已存盘的币(断点续跑)
    if skip_existing:
        before = len(coins)
        coins = [c for c in coins if not (TRIGGER_DIR / f"{c}.parquet").exists()]
        print(f"  [skip-existing] 跳过已存盘 {before - len(coins)} 币,剩 {len(coins)} 币待跑")

    print(f"=== Trigger Store ===")
    print(f"  币数:     {len(coins)}")
    print(f"  规则:     {len(rules)} 条 ({', '.join(r['id'] for r in rules[:8])}{'...' if len(rules) > 8 else ''})")
    print(f"  窗口:     {start} ~ {end} ({days} 天)")
    print(f"  阈值:     {threshold_mode}")
    print(f"  并发:     {workers} workers, {rps_per_key} req/s/key "
          f"(= {rps_per_key*8:.1f} req/s 总), batch {batch_size}/{batch_sleep}s")
    print(f"  输出:     {TRIGGER_DIR}/ (边拉边存,中断不丢)")
    print()

    # 边拉边存:每批 fetch 完立即算+存,进程被杀也保住已完成批
    total_batches = (len(coins) + batch_size - 1) // batch_size
    saved = 0
    failed = 0
    skipped_total = {}
    rows_meta = []
    t0 = time.time()
    for b in range(total_batches):
        batch = coins[b*batch_size : (b+1)*batch_size]
        print(f"  Batch {b+1}/{total_batches} ({len(batch)} 币)...")
        coin_data = fetch_all_coins_parallel(
            batch, start, end, workers=workers,
            rps_per_key=rps_per_key, key_cooldown=key_cooldown,
            # 内部不再分批(我们自己分了),省一层 sleep
            batch_size=len(batch), batch_sleep=0,
        )
        # 立即存盘(每批 fetch 完就存,不等全部跑完)
        for coin, df in coin_data.items():
            if df is None:
                failed += 1
                continue
            meta = _save_coin(coin, df, rules, threshold_mode)
            if meta is None:
                failed += 1
                continue
            saved += 1
            rows_meta.append(meta)
            for rid in meta.get('skipped_rules_list', []):
                skipped_total[rid] = skipped_total.get(rid, 0) + 1
        done = (b+1) * batch_size
        elapsed = time.time() - t0
        print(f"    [{min(done,len(coins))}/{len(coins)}] 累计存盘 {saved}, 失败 {failed} ({elapsed:.0f}s)", flush=True)
        # 批间冷却(给限流窗口喘息)
        if b+1 < total_batches and batch_sleep > 0:
            time.sleep(batch_sleep)

    elapsed = time.time() - t0
    print(f"\n完成: 存盘 {saved}, 失败 {failed}, 耗时 {elapsed:.0f}s ({elapsed/60:.1f} 分钟)")

    if saved == 0:
        print("  无币存盘,检查数据源/网络/key。")
        return None

    # 增量写 manifest(合并已存的 + 本次新存的)
    meta_df_new = pd.DataFrame(rows_meta)
    meta_path = TRIGGER_DIR / '_manifest.csv'
    if skip_existing and meta_path.exists():
        old = pd.read_csv(meta_path, encoding='utf-8-sig')
        # 用本次覆盖同名币,其余保留
        old = old[~old['coin'].isin(meta_df_new['coin'])]
        meta_df = pd.concat([old, meta_df_new], ignore_index=True)
    else:
        meta_df = meta_df_new
    meta_df.to_csv(meta_path, index=False, encoding='utf-8-sig')

    print(f"\n=== 已存盘 {len(meta_df)} 币 ===")
    print(f"  平均每币触发规则数: {meta_df['n_rules_triggered'].mean():.1f}/{len(rules)}")
    print(f"  平均每币触发事件数: {meta_df['total_trigger_events'].mean():.0f}")
    print(f"  触发规则最多的币 (top 5):")
    top = meta_df.nlargest(5, 'n_rules_triggered')
    for _, r in top.iterrows():
        print(f"    {r['coin']:<12} 规则 {r['n_rules_triggered']}/{len(rules)}, "
              f"事件 {r['total_trigger_events']} 次, {r['n_bars']} bars")

    # 全币都失败的规则
    dead = {rid: n for rid, n in skipped_total.items() if n >= saved}
    if dead:
        print(f"\n  ⚠️  {len(dead)} 条规则在所有币上都无结果:")
        for rid in sorted(dead):
            print(f"       {rid}")

    print(f"\n清单: {meta_path}")
    print(f"触发序列: {TRIGGER_DIR}/*.parquet (每币一个文件)")
    print(f"\n下一步: python core/pattern_match.py --scan-current   # 扫所有币此刻指纹的历史 edge")
    return meta_df


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Trigger Store — 全市场规则触发时间序列存储(意图推断地基)')
    parser.add_argument('--coins', default=DEFAULT_COINS_FILE,
                        help=f'币池文件 (default {DEFAULT_COINS_FILE})')
    parser.add_argument('--days', type=int, default=30, help=f'回看天数 (default 30)')
    parser.add_argument('--rules', default=None,
                        help='逗号分隔的规则 ID;留空 = 全部 active 规则')
    parser.add_argument('--threshold', default='adaptive', choices=['default', 'adaptive'],
                        help='阈值模式 (default adaptive, per-coin 分位)')
    parser.add_argument('--workers', type=int, default=TRIGGER_WORKERS,
                        help=f'并行 worker 数 (default {TRIGGER_WORKERS},对齐 8 key)')
    parser.add_argument('--rps-per-key', type=float, default=TRIGGER_RPS_PER_KEY,
                        help=f'每 key 每秒请求数 (default {TRIGGER_RPS_PER_KEY}, '
                             f'= 100req/5min 的安全值,8 key 合计 {TRIGGER_RPS_PER_KEY*8:.1f} req/s)')
    parser.add_argument('--key-cooldown', type=float, default=DEFAULT_KEY_COOLDOWN)
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--batch-sleep', type=float, default=DEFAULT_BATCH_SLEEP)
    parser.add_argument('--no-batch', action='store_true', help='不分批')
    parser.add_argument('--skip-existing', action='store_true',
                        help='跳过已存盘的币(断点续跑;中断后接着跑不用从头来)')
    args = parser.parse_args()

    # 读币池(与 coin_screener 同口径)
    coins_path = Path(args.coins)
    if not coins_path.exists():
        print(f"  [ERR] 币池文件不存在: {args.coins}")
        return 1
    if coins_path.suffix == '.xlsx':
        df_coins = pd.read_excel(args.coins)
    else:
        df_coins = pd.read_csv(args.coins, header=None)
    coins = df_coins.iloc[:, 0].dropna().astype(str).tolist()
    coins = [c for c in coins if c.endswith('USDT')]
    print(f"从 {args.coins} 载入 {len(coins)} 币\n")

    rule_ids = None
    if args.rules:
        rule_ids = [r.strip() for r in args.rules.split(',') if r.strip()]

    run_trigger_store(
        coins=coins,
        rule_ids=rule_ids,
        days=args.days,
        threshold_mode=args.threshold,
        workers=args.workers,
        rps_per_key=args.rps_per_key,
        key_cooldown=args.key_cooldown,
        batch_size=len(coins) if args.no_batch else args.batch_size,
        batch_sleep=args.batch_sleep,
        skip_existing=args.skip_existing,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
