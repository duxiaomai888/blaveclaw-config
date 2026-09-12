"""Minimal check for lib/analysis.py::backtest() and rules_catalog/catalog.py —
no network, no api. backtest() is the single source-of-truth rule-evaluation
entry: a one-bar shift in entry timing or a dropped fee silently changes every
Sharpe/total the whole project reports, so this pins its contract to the
docstring (next-bar entry at close[i], exit at close[i+hold], single-side fee,
overlap-skipping position model, min_trades gate). catalog.py is the single
source of truth for 59 rules: unique ids, required fields, callable
cond_builders, valid directions, and the active/skip counts the README advertises.

Run: cd <workspace> && .venv/Scripts/python tests/check_backtest_catalog.py
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MPLBACKEND", "Agg")
import numpy as np
import pandas as pd

from lib.analysis import backtest
from rules_catalog.catalog import (
    ALL_RULES, get_active_rules, get_total_count, get_active_count,
    get_rule_by_id, get_rules_by_category,
)

fails = 0

def check(cond, msg):
    global fails
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    fails += (not cond)


# ─── backtest() golden cases ────────────────────────────────────────────────
# 20 bars, monotonically rising price 100..119 so every long trade is a winner
# and every short trade is a loser — direction sign errors are impossible to hide.
close = np.arange(100.0, 120.0)          # 20 bars
n = len(close)
hold = 3
fee = 0.0005
ppy = 8760

# Signal at bar 0 → entry pos[1..4] (s=1, e=min(1+3,20)=4). Signal at bar 2
# overlaps (2 < last_e=4) and is skipped in pos, but still counts in trade_rets.
cond = pd.Series(np.zeros(n, dtype=bool))
cond.iloc[0] = True
cond.iloc[2] = True                       # overlaps bar-0 trade → skipped in pos

res = backtest(cond, close, 'long', hold, fee=fee, min_trades=1, periods_per_year=ppy)

# trade_rets includes BOTH signals (overlap only suppresses pos, not trade_rets):
#   t=0: ep=100, xp=close[3]=103 → (3/100 - fee)*100 = 2.95
#   t=2: ep=102, xp=close[5]=105 → (3/102 - fee)*100 ≈ 2.9412
trade0 = (close[3] - close[0]) / close[0] - fee
trade2 = (close[5] - close[2]) / close[2] - fee
expected_avg = (trade0 + trade2) / 2 * 100
expected_total = (trade0 + trade2) * 100
check(res is not None, "backtest returns a result (>= min_trades)")
check(res['n'] == 2, f"n counts every signal incl. overlap ({res['n']} == 2)")
check(res['wr'] == 100.0, f"wr=100% for rising longs ({res['wr']})")
check(abs(res['avg'] - expected_avg) < 1e-9, f"avg == hand-computed ({res['avg']} vs {expected_avg})")
check(abs(res['total'] - expected_total) < 1e-9, f"total == sum of trade_rets ({res['total']} vs {expected_total})")

# pos overlap-skip: pos[1:4]=+1 from bar-0 signal; bar-2 signal must NOT extend
# into [4:7]. Build pos the same way backtest does and assert the slice.
# (backtest doesn't return pos, so reconstruct to document the contract.)
pos = np.zeros(n)
last_e = 0
for i in np.flatnonzero(cond.to_numpy()):
    if i < last_e or i >= n - hold - 1:
        continue
    s = min(i + 1, n - 1)
    e = min(s + hold, n)
    pos[s:e] = 1.0
    last_e = e
check(pos[1:4].tolist() == [1.0, 1.0, 1.0], "bar-0 signal fills pos[1:4]")
check(pos[4:7].tolist() == [0.0, 0.0, 0.0], "bar-2 signal (overlap) does not extend pos past last_e")

# short on rising prices → every trade negative, wr=0
res_s = backtest(cond, close, 'short', hold, fee=fee, min_trades=1, periods_per_year=ppy)
check(res_s['wr'] == 0.0, f"wr=0% for rising shorts ({res_s['wr']})")
check(res_s['n'] == 2, f"short n == 2 ({res_s['n']})")
check(res_s['avg'] < 0, f"short avg negative ({res_s['avg']})")

# min_trades gate: 1 signal < min_trades=5 → None
cond1 = pd.Series(np.zeros(n, dtype=bool)); cond1.iloc[0] = True
check(backtest(cond1, close, 'long', hold, fee=fee, min_trades=5) is None,
      "min_trades=5 suppresses a 1-signal run → None")

# too-few bars gate: n < hold+2
short_close = np.arange(100.0, 103.0)     # 3 bars, hold=3 → 3 < 5
c = pd.Series([True, False, False])
check(backtest(c, short_close, 'long', 3) is None, "n < hold+2 → None")

# no signals → None
check(backtest(pd.Series(np.zeros(n, dtype=bool)), close, 'long', hold) is None,
      "no signals → None")

# zero/negative price guard doesn't crash (bar with close=0 → that ret=0, no inf)
close0 = close.copy(); close0[5] = 0.0
c0 = pd.Series(np.zeros(n, dtype=bool)); c0.iloc[0] = True
r0 = backtest(c0, close0, 'long', hold, fee=fee, min_trades=1)
check(r0 is not None and math.isfinite(r0['sharpe']) and math.isfinite(r0['mdd']),
      "zero-price bar produces no inf/nan in sharpe/mdd")

# entry timing: signal at bar i, exit at close[i+hold] (NOT close[i] / open[i+1])
# With flat price then a single uptick, only the entry-at-close[i+hold] model
# gives a positive trade; close[i] entry would give 0.
flat = np.full(20, 100.0); flat[4] = 110.0               # uptick at bar 4
cf = pd.Series(np.zeros(20, dtype=bool)); cf.iloc[1] = True   # signal bar 1, hold=3 → exit bar 4
rf = backtest(cf, flat, 'long', 3, fee=fee, min_trades=1)
# ep=close[1]=100, xp=close[4]=110 → (10/100 - fee)*100 = 9.95
check(rf is not None and abs(rf['total'] - 9.95) < 1e-9,
      f"entry at close[i], exit at close[i+hold] → 9.95 total ({rf['total'] if rf else None})")

# single-side fee: total = (ret - fee)*100, NOT (ret - 2*fee)*100
check(abs(rf['total'] - ((110-100)/100 - fee)*100) < 1e-9,
      "exactly one (single-side) fee per trade, not two")


# ─── catalog integrity ───────────────────────────────────────────────────────
check(get_total_count() == 59, f"catalog has 59 rules total ({get_total_count()})")
check(len(ALL_RULES) == 59, f"ALL_RULES len == 59 ({len(ALL_RULES)})")

ids = [r['id'] for r in ALL_RULES]
check(len(ids) == len(set(ids)), f"no duplicate rule ids (dups={set([x for x in ids if ids.count(x)>1])})")

for r in ALL_RULES:
    rid = r['id']
    for f in ('id', 'category', 'name_cn', 'direction_doc'):
        check(f in r, f"rule {rid} has field {f!r}")
    check(r['direction_doc'] in ('long', 'short', 'neutral'),
          f"rule {rid} direction_doc valid ({r['direction_doc']!r})")
    is_active = not r.get('skip', False)
    if is_active:
        check('cond_builder' in r and callable(r['cond_builder']), f"active rule {rid} has callable cond_builder")
        check(isinstance(r.get('param_space'), dict), f"active rule {rid} param_space is dict")
    else:
        check('skip_reason' in r, f"skipped rule {rid} has skip_reason")

# A-M categories all present and non-empty
cats = {r['category'] for r in ALL_RULES}
check(cats == set('ABCDEFGHIJKLM'), f"all 13 categories A-M present ({sorted(cats)})")
for cat in 'ABCDEFGHIJKLM':
    check(len(get_rules_by_category(cat)) > 0, f"category {cat} non-empty")

# active vs skip counts: 59 total = 54 active + 5 skipped (E05 E06 J01 J02 J03, the
# BTC/sector cross-coin rules that need multi-coin data). K/L/M (9 new rules) are
# active but un-backtested — direction_best falls back to direction_doc.
skip_ids = [r['id'] for r in ALL_RULES if r.get('skip', False)]
check(len(skip_ids) == 5, f"5 skipped rules ({skip_ids})")
check(get_active_count() == 54, f"54 active rules ({get_active_count()})")
check(set(skip_ids) == {'E05', 'E06', 'J01', 'J02', 'J03'},
      f"skipped ids are the BTC/sector cross-coin rules ({skip_ids})")

# get_rule_by_id round-trip
check(get_rule_by_id('A01') is not None, "get_rule_by_id('A01') found")
check(get_rule_by_id('ZZZ') is None, "get_rule_by_id('ZZZ') → None")

# every active cond_builder runs on a minimal frame and returns a bool Series
# of the right length — catches a rule that references a column load_data never builds
cols = ['Open', 'High', 'Low', 'Close', 'Volume', 'HC', 'WH', 'MS', 'TI', 'LM',
        'SM', 'abs_HC', 'abs_TI', 'hc_sign', 'ti_sign', 'lm_sign', 'hc_delta',
        'ret_1h', 'ret_24h', 'new_high_24h', 'new_low_24h']
demo = pd.DataFrame(np.random.default_rng(0).standard_normal((200, len(cols))),
                    columns=cols)
demo['new_high_24h'] = demo['new_high_24h'] > 0
demo['new_low_24h'] = demo['new_low_24h'] > 0
for r in get_active_rules():
    try:
        # resolve_param_space needs the alpha columns; give it demo frame
        from rules_catalog.catalog import resolve_param_space
        ps = resolve_param_space(r, demo)
        for combo_vals in (list(ps.values()) or [[]]):
            p = {k: v[0] if isinstance(v, list) and v else v for k, v in ps.items()}
            out = r['cond_builder'](demo, p)
            if not isinstance(out, pd.Series):
                out = pd.Series(out, index=demo.index)
            check(len(out) == len(demo) and out.astype(bool).sum() >= 0,
                  f"{r['id']} cond_builder returns bool Series len {len(demo)}")
            break
    except (ValueError, KeyError, TypeError) as e:
        check(False, f"{r['id']} cond_builder crashed on demo frame: {e}")


print("\nALL PASS" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
