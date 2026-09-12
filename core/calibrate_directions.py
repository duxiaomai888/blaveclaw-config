"""
Calibrate Directions — 全局更新 50 条规则的实测方向
=====================================================
读 cache/csv/batch_50_summary.csv,对每条规则按多数币的
direction_best 投票,算出实测最优方向(direction_best),
然后回写到 rules_catalog/catalog.py 每条规则里。

对没有回测数据的规则(skip 或触发不足),direction_best = direction_doc。
"""
import sys, os, re, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _ROOT)

import pandas as pd
from rules_catalog.catalog import ALL_RULES

CSV_PATH = os.path.join(_ROOT, 'cache', 'csv', 'batch_50_summary.csv')
CATALOG_PATH = os.path.join(_ROOT, 'rules_catalog', 'catalog.py')


def compute_calibration():
    """从批量回测结果算每条规则的实测方向。"""
    df = pd.read_csv(CSV_PATH)
    cal = df.groupby('rule').agg(
        doc_dir=('direction_doc', lambda x: x.iloc[0]),
        n_long=('direction_best', lambda x: (x == 'long').sum()),
        n_short=('direction_best', lambda x: (x == 'short').sum()),
        n_coins=('symbol', 'count'),
        avg_sharpe=('sharpe', 'mean'),
    ).reset_index()
    cal['direction_best'] = cal.apply(
        lambda r: 'long' if r['n_long'] >= r['n_short'] else 'short', axis=1)
    cal['flipped'] = cal['doc_dir'] != cal['direction_best']
    return {r['rule']: r for _, r in cal.iterrows()}


def build_direction_map():
    """全 50 条规则的 direction_best 映射(有数据用校准值,无数据用文档方向)。"""
    cal = compute_calibration()
    result = {}
    for r in ALL_RULES:
        rid = r['id']
        if rid in cal:
            result[rid] = cal[rid]['direction_best']
        else:
            # 无回测数据:用文档方向
            result[rid] = r['direction_doc']
    return result, cal


def patch_catalog(direction_map):
    """把 direction_best 字段写进 catalog.py 每条规则字典。"""
    with open(CATALOG_PATH, encoding='utf-8') as f:
        src = f.read()

    changes = 0
    for rid, best_dir in direction_map.items():
        # 匹配形如: 'id': 'A01', ... 'direction_doc': 'long',
        # 在 direction_doc 行后面加 direction_best 行
        # 但要精确匹配该规则块,避免跨规则误改

        # 策略:找 'id': 'A01' 后面第一个 'direction_doc' 行,在其后插入
        # 如果已有 direction_best 行,先删旧的再插新的(幂等)

        # 先找该规则块的起止
        id_pattern = f"'id': '{rid}'"
        id_pos = src.find(id_pattern)
        if id_pos == -1:
            print(f"  [skip] {rid}: id not found in catalog.py")
            continue

        # 找该块内的 direction_doc 行
        doc_pattern = f"'direction_doc':"
        doc_pos = src.find(doc_pattern, id_pos)
        if doc_pos == -1:
            print(f"  [skip] {rid}: direction_doc not found")
            continue

        # 找 direction_doc 行的结尾(换行符)
        line_end = src.find('\n', doc_pos)
        if line_end == -1:
            continue

        # 检查下一个非空行是否已是 direction_best
        after = src[line_end:]
        # 找下一行内容
        next_line_match = re.match(r'\s*\'direction_best\':', after)
        if next_line_match:
            # 已有 direction_best,替换值
            existing_line_end = src.find('\n', line_end + next_line_match.end() - len(next_line_match.group(0)))
            # 精确替换:找 direction_best 那一行的值
            old_line_start = line_end + next_line_match.start()
            old_line_end = src.find('\n', old_line_start)
            old_line = src[old_line_start:old_line_end]
            new_line = f"        'direction_best': '{best_dir}',"
            if new_line.strip() != old_line.strip():
                src = src[:old_line_start] + new_line + src[old_line_end:]
                changes += 1
        else:
            # 插入新的 direction_best 行
            indent = "        "
            new_line = f"{indent}'direction_best': '{best_dir}',"
            src = src[:line_end + 1] + new_line + src[line_end + 1:]
            changes += 1

    with open(CATALOG_PATH, 'w', encoding='utf-8') as f:
        f.write(src)

    return changes


def main():
    print("=== 规则方向全局校准 ===\n")

    direction_map, cal = build_direction_map()

    # 打印校准结果
    print(f"{'规则':4s} | {'文档方向':6s} | {'实测方向':6s} | {'状态':8s} | {'币数':4s} | {'均Sharpe':8s}")
    print("-" * 60)
    for r in ALL_RULES:
        rid = r['id']
        doc_dir = r['direction_doc']
        best_dir = direction_map[rid]
        is_skip = r.get('skip', False)

        if rid in cal.index:
            c = cal.loc[rid]
            status = '⚠️翻转' if c['flipped'] else '✅一致'
            n_coins = int(c['n_coins'])
            avg_sharpe = f"{c['avg_sharpe']:.2f}"
        else:
            status = '🔒skip' if is_skip else '⏸️无数据'
            n_coins = 0
            avg_sharpe = '—'

        print(f"{rid:4s} | {doc_dir:6s} | {best_dir:6s} | {status:8s} | {n_coins:4d} | {avg_sharpe:8s}")

    n_flipped = sum(1 for rid in direction_map if rid in cal.index and cal.loc[rid]['flipped'])
    n_consistent = sum(1 for rid in direction_map if rid in cal.index and not cal.loc[rid]['flipped'])
    n_nodata = sum(1 for r in ALL_RULES if r['id'] not in cal.index)

    print(f"\n总计: {len(ALL_RULES)} 条")
    print(f"  ✅ 方向一致: {n_consistent}")
    print(f"  ⚠️ 方向翻转: {n_flipped}")
    print(f"  ⏸️ 无数据/skip: {n_nodata}")

    print(f"\n=== 写回 catalog.py ===")
    changes = patch_catalog(direction_map)
    print(f"  修改 {changes} 处")

    # 验证:重新 import 检查字段存在
    print(f"\n=== 验证 ===")
    import importlib
    import rules_catalog.catalog as cat_mod
    importlib.reload(cat_mod)
    for r in cat_mod.ALL_RULES[:3]:
        has_best = 'direction_best' in r
        print(f"  {r['id']}: direction_doc={r['direction_doc']}, direction_best={r.get('direction_best', '缺失')}")
    print(f"  ... (共 {len(cat_mod.ALL_RULES)} 条)")

    print(f"\n✓ 全局校准完成")


if __name__ == '__main__':
    main()
