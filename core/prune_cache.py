"""
Cache housekeeping — 清理过期缓存 parquet + 治理 cache/csv 目录布局。
=====================================================================
两类清理:

1. prune: 删除 N 天未访问的 *过期* parquet 缓存。
   安全闸:只删「过去的月份」文件(文件名 YYYY-MM 早于当前月),当前月文件
   永不动(还在 delta-updating);不可读大小的、mtime 在未来的一律跳过。
   cache/ 下 5247 个 parquet、2132 个目录,大量是已改名币的下架历史,
   删掉长期不访问的可回收可观空间而不影响任何 warm 回测。

2. csv-organize(默认关,需显式 --organize-csv):把 cache/csv/ 里平铺的 CSV 按
   single/batch/partial 归类到子目录(原文件移走,可逆)。
   ⚠️ 但这会打破「cache/csv 平铺」这个硬编码契约,必须先改完这些读取方:
       core/analyze_results.py    读 batch_50_summary.csv / cross_period_rule_summary.csv
                                  / btc_corr_results.csv / j03_sector_results.csv
       core/cross_period_analysis.py  读 {sym}_180d.csv,写 batch_180d_partial.csv
       core/run_batch.py            写 cache/csv/{sym}_{period}d.csv
       core/coin_screener.py        DEFAULT_OUT_DIR = 'cache/csv'
   否则 analyze_results / cross_period_analysis 会直接 FileNotFoundError。
   所以默认只报告不动手;真要分类,先迁移上面四个文件再跑。

用法:
    # 默认就是干跑:三项全扫一遍,只报告不删不动
    python core/prune_cache.py

    # 真执行(删 parquet + 移 CSV + 清 tmp)
    python core/prune_cache.py --apply

    # 只看要删什么,阈值改 180 天
    python core/prune_cache.py --days 180

    # 只做 CSV 归类(默认关;会打破硬编码契约,见文件头警告)
    python core/prune_cache.py --organize-csv        # 只报告要移哪些
    python core/prune_cache.py --organize-csv --apply  # 迁移读取方之后再真移
"""
import argparse
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import _bootstrap  # noqa: F401  — sys.path setup

_CACHE = Path(__file__).resolve().parent.parent / 'cache'
_CSV = _CACHE / 'csv'
_TMP = Path(__file__).resolve().parent.parent / 'tmp'


def _is_past_month(stem):
    """文件名是 YYYY-MM 且早于当前月 → 安全可删的过去月份。"""
    if len(stem) != 7 or stem[4] != '-':
        return False
    try:
        y, m = int(stem[:4]), int(stem[5:7])
    except ValueError:
        return False
    if not (1 <= m <= 12):
        return False
    now = datetime.now(timezone.utc)
    return (y, m) < (now.year, now.month)


def prune_parquet(days, dry_run=False):
    """删 cache/ 下 N 天未访问的过去月份 parquet。当前月不动。"""
    cutoff = time.time() - days * 86400
    removed = 0
    kept = 0
    for path in _CACHE.rglob('*.parquet'):
        try:
            st = path.stat()
        except OSError:
            continue
        # 未来 mtime(时钟漂移)或未过 cutoff 的 → 保留
        if st.st_mtime > cutoff or st.st_mtime > time.time() + 86400:
            kept += 1
            continue
        # 只删过去月份的文件;当前月 / 非 YYYY-MM 命名的(如 single-file .parquet)
        # 一律保留 —— single-file 是活跃缓存主体,删它会让 warm 回测变冷。
        if not _is_past_month(path.stem):
            kept += 1
            continue
        if dry_run:
            print(f"  [dry] rm {path.relative_to(_CACHE.parent)} "
                  f"(atime {time.strftime('%Y-%m-%d', time.localtime(st.st_atime))})")
        else:
            path.unlink(missing_ok=True)
        removed += 1
    # 回收空目录(holder_concentration_1h_<symbol>/ 里文件删光后)。
    # 注意干跑时不会回收「删完 parquet 才会变空」的目录 —— 文件没真删,
    # 这些目录此刻仍有内容,所以这里只报真正已空的目录。
    emptied = 0
    _csv_subs = {_CSV / s for s in ('single', 'batch', 'partial')}
    for d in sorted(_CACHE.rglob('*'), reverse=True):
        # csv 分类目录是固定布局,空着也有意义,不回收。
        if d.resolve() in _csv_subs:
            continue
        if d.is_dir() and not any(d.iterdir()):
            try:
                if dry_run:
                    print(f"  [dry] rmdir {d.relative_to(_CACHE.parent)}")
                else:
                    d.rmdir()
                emptied += 1
            except OSError:
                pass
    print(f"parquet prune: removed={removed} kept={kept} emptied_dirs={emptied} "
          f"({'dry-run' if dry_run else 'applied'}, cutoff {days}d)")
    return removed


def organize_csv(dry_run=False):
    """把 cache/csv/ 平铺 CSV 移到 single/batch/partial 子目录。"""
    if not _CSV.exists():
        print(f"  [skip] {_CSV} not found")
        return 0
    moved = 0
    for sub in ('single', 'batch', 'partial'):
        # 干跑时不建目录 —— --dry-run 必须真的只读,否则「看一遍」就留副作用。
        if not dry_run:
            (_CSV / sub).mkdir(exist_ok=True)
    for path in _CSV.glob('*.csv'):
        name = path.name.lower()
        if 'partial' in name:
            dest = _CSV / 'partial' / path.name
        elif re.fullmatch(r'[a-z0-9]+_\d+d\.csv', name):
            # <symbol>_<period>d.csv 是单币种回测结果
            dest = _CSV / 'single' / path.name
        else:
            # 其余都是聚合产物(batch_*_summary / screener_* / cross_period_* /
            # btc_corr_results),归 batch/ —— 别用「非 batch 即 single」的兜底,
            # 那会把跨周期汇总冒充成单币结果。
            dest = _CSV / 'batch' / path.name
        if dest.exists():
            continue
        if dry_run:
            print(f"  [dry] mv {path.name} → {dest.relative_to(_CSV)}")
        else:
            shutil.move(str(path), str(dest))
        moved += 1
    print(f"csv organize: moved={moved} ({'dry-run' if dry_run else 'applied'})")
    return moved


def clean_tmp(days, dry_run=False):
    """删 tmp/ 下 N 天未访问的探索性 .py/.txt/.json。"""
    cutoff = time.time() - days * 86400
    removed = 0
    if not _TMP.exists():
        return 0
    for path in _TMP.rglob('*'):
        if path.is_dir():
            continue
        if path.suffix not in ('.py', '.txt', '.json', '.csv', '.png', '.log'):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime > cutoff:
            continue
        if dry_run:
            print(f"  [dry] rm {path.relative_to(_TMP.parent)}")
        else:
            path.unlink(missing_ok=True)
        removed += 1
    print(f"tmp clean: removed={removed} ({'dry-run' if dry_run else 'applied'}, cutoff {days}d)")
    return removed


def main():
    p = argparse.ArgumentParser(
        description='Cache housekeeping — 默认只报告,加 --apply 才真删/真移')
    p.add_argument('--days', type=int, default=90, help='未访问多少天才删(默认 90)')
    p.add_argument('--dry-run', action='store_true',
                   help='显式干跑(默认行为,保留只为可读性)')
    p.add_argument('--apply', action='store_true', help='真正执行删除/移动')
    p.add_argument('--no-parquet', action='store_true', help='跳过 parquet 扫描')
    p.add_argument('--no-tmp', action='store_true', help='跳过 tmp/ 清理')
    p.add_argument('--organize-csv', action='store_true',
                   help='归类 cache/csv 平铺 CSV(默认关 —— 会打破硬编码契约,见文件头)')
    args = p.parse_args()

    # 默认干跑是刻意的:此脚本会真删缓存,「裸跑即删除」是不可恢复的误操作。
    dry = not args.apply
    if args.dry_run:
        dry = True
    if not args.no_parquet:
        prune_parquet(args.days, dry)
    if args.organize_csv:
        # 默认关:这步会打破 cache/csv 平铺契约,得先迁移读取方(见文件头)。
        if not dry:
            print('  !! 正在打破 cache/csv 平铺契约 —— '
                  '确认 analyze_results.py / cross_period_analysis.py / '
                  'run_batch.py / coin_screener.py 已改完路径')
        organize_csv(dry)
    if not args.no_tmp:
        clean_tmp(args.days, dry)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
