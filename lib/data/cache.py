"""Cache layouts — monthly-partitioned + single-file + migration + TTL helpers.

拆分自原 lib/data.py。这里只管「数据怎么落盘/读取/合并/迁移」,不管任何
具体端点。所有 fetcher 模块(kline/alpha/twstock/twmarket/twfutures)的
缓存语义都收敛在这里。

_fundamental_cache_path / _load_fundamental_cache / _save_fundamental_cache
是从台股段搬上来的通用 TTL 单文件缓存助手(twstock 和 twmarket 都用)。
"""
import os
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .http import _CACHE_DIR
from ._shared import _is_sub_5min

__all__ = [
    '_monthly_cache_dir', '_next_month', '_iter_months', '_contiguous_spans',
    '_normalise_index', '_month_end_utc', '_written_before_month_end',
    '_HEAD_VERIFIED_META', '_HEAD_TOLERANCE', '_head_short_unverified',
    '_stale_incomplete_month', '_atomic_to_parquet',
    '_extend_cache_monthly', '_save_monthly',
    '_SINGLE_FILE_PREFIXES', '_META_TS_FMT', '_META_KEY',
    '_single_path', '_write_single', '_read_single_meta',
    '_read_monthly_dir_all', '_migrate_monthly_to_single',
    '_read_single', '_has_single_cache', '_tail_needs_completion',
    '_merge_frames', '_extend_cache_single', '_merge_meta', '_merge_single',
    '_save_single',
    '_fundamental_cache_path', '_load_fundamental_cache', '_save_fundamental_cache',
]


def _monthly_cache_dir(prefix, params):
    """cache/{prefix}_{param_str}/  — parent dir for monthly parquet files."""
    param_str = '_'.join(str(v) for _, v in sorted(params.items()))
    return _CACHE_DIR / f'{prefix}_{param_str}'


def _next_month(ym):
    """'2022-01' → '2022-02', '2022-12' → '2023-01'"""
    y, m = int(ym[:4]), int(ym[5:7])
    return f'{y+1}-01' if m == 12 else f'{y}-{m+1:02d}'


def _iter_months(start_str, end_str):
    """Yield 'YYYY-MM' strings from start month to end month (inclusive)."""
    ym, end_ym = start_str[:7], end_str[:7]
    while ym <= end_ym:
        yield ym
        ym = _next_month(ym)


def _contiguous_spans(months):
    """Group a sorted list of 'YYYY-MM' into runs of consecutive months.
    ['2022-01','2022-02','2022-05'] → [['2022-01','2022-02'], ['2022-05']]."""
    spans, cur = [], []
    for ym in months:
        if cur and _next_month(cur[-1]) == ym:
            cur.append(ym)
        else:
            if cur:
                spans.append(cur)
            cur = [ym]
    if cur:
        spans.append(cur)
    return spans


def _normalise_index(df):
    """Convert tz-aware index to tz-naive UTC in-place-safe copy."""
    if df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert('UTC').tz_localize(None)
    return df


def _month_end_utc(ym):
    """Naive-UTC datetime of the first instant AFTER month `ym` ('YYYY-MM')."""
    nxt = _next_month(ym)
    return datetime(int(nxt[:4]), int(nxt[5:7]), 1)


def _written_before_month_end(path, ym):
    """True if `path` was last written before month `ym` was over (UTC).

    A past-month file written while that month was still the current month may
    be missing its tail (the delta-update path stops running once the month
    rolls over), so it needs one completing re-fetch. After that re-fetch the
    file's mtime is later than the month end and this never triggers again.
    An unstatable file counts as incomplete → re-fetch.
    """
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime) < _month_end_utc(ym)
    except Exception:
        return True


_HEAD_VERIFIED_META = {b'blave_head_verified': b'1'}
_HEAD_TOLERANCE = timedelta(hours=1)


def _head_short_unverified(path, ym):
    """True if past month `path` starts more than _HEAD_TOLERANCE after the month
    begins and has never been re-fetched to confirm that — one completing
    re-fetch, then the footer flag _HEAD_VERIFIED_META stops it recurring.

    Why: an earlier lib clamped sub-5min fetch ranges to a 45-day floor, so a
    month straddling that floor was cached from the clamp date onward (real
    incident: 2026-07 stored as 07-10 → 07-31, nine days missing) — and the
    mtime rule above then treats it as complete forever, so a later two-year
    backtest inherits the hole. A listing month is legitimately head-short;
    it re-fetches once, gets the flag, and is never re-fetched again.
    Footer-only schema read first (cheap), index read only when unflagged.
    """
    try:
        schema = pq.read_schema(path)
        if not schema.names or (schema.metadata or {}).get(b'blave_head_verified'):
            return False                  # empty marker, or already verified
        idx = pd.to_datetime(pd.read_parquet(path, columns=[]).index)
        if getattr(idx, 'tz', None) is not None:
            idx = idx.tz_convert('UTC').tz_localize(None)
        month_start = datetime(int(ym[:4]), int(ym[5:7]), 1)
        return idx.min() > pd.Timestamp(month_start) + _HEAD_TOLERANCE
    except Exception:
        return True


def _stale_incomplete_month(path, ttl_hours, ym, edge_days=15):
    """True if `path` is an empty or implausibly-partial past month older than
    `ttl_hours` — used by sources whose history is backfilled progressively
    server-side, where a month fetched mid-backfill caches a permanent hole.

    Only called when a TTL is set. mtime is checked first (cheap stat) so files
    younger than the TTL are never opened. Past the age gate only the index is
    read; the month counts as stale when it has 0 rows, or its first bar starts
    more than `edge_days` after the month begins, or its last bar ends more than
    `edge_days` before the month ends (15 days clears the longest TAIFEX Lunar
    New Year closure, ~11 calendar days). A month that passes gets its mtime
    refreshed so it is re-examined at most once per TTL window. An unreadable
    file counts as stale → re-fetch.

    Known blind spots (heuristic limits, accepted): a hole of <= `edge_days` at
    either month edge, and any hole strictly inside the month, pass the check and
    are never re-fetched. Also note the flip side: a month that can never be
    complete (source history starts mid-month, delisting, pre-history empty
    months inside the requested range) re-fetches once per TTL window forever —
    clamp the requested start to the source's known history start where possible.
    """
    try:
        if time.time() - path.stat().st_mtime <= ttl_hours * 3600:
            return False
        idx = pd.read_parquet(path, columns=[]).index   # index only, no data pages
        if len(idx) == 0:
            return True
        month_start = datetime(int(ym[:4]), int(ym[5:7]), 1)
        month_end   = _month_end_utc(ym)
        idx = pd.to_datetime(idx)
        if getattr(idx, 'tz', None) is not None:
            idx = idx.tz_convert('UTC').tz_localize(None)
        margin = timedelta(days=edge_days)
        if idx.min() > pd.Timestamp(month_start) + margin:
            return True
        if idx.max() < pd.Timestamp(month_end) - margin:
            return True
        try:
            os.utime(path)   # passed — skip the index read until the TTL expires again
        except OSError:
            pass             # can't refresh mtime — worst case we re-check next call
        return False
    except Exception:
        return True


def _atomic_to_parquet(df, path, footer_meta=None):
    """Write `df` to `path` via same-directory tmp file + os.replace.
    `footer_meta` (bytes→bytes) is merged into the parquet schema metadata.

    Readers (and concurrent writers racing on the same month) never see a
    half-written parquet: a crash/kill mid-write leaves only a *.tmp file,
    and os.replace on the same filesystem is atomic.
    """
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        if footer_meta:
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata({**(table.schema.metadata or {}), **footer_meta})
            pq.write_table(table, tmp)
        else:
            df.to_parquet(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _extend_cache_monthly(prefix, params, fetch_raw_fn, start, end,
                          empty_marker_ttl_hours=None):
    """Monthly-partitioned cache.

    Past months (before current month) are stored immutably — fetched once, never re-fetched,
    with one exception: a past-month file last written BEFORE that month ended
    (i.e. cached while it was still the current month, so its tail may be
    missing) gets one completing re-fetch, merged with what is already cached.
    Current month is delta-updated: load cached, fetch from last bar to now, merge.

    empty_marker_ttl_hours (opt-in): by default (None) an empty past month is
    marked once and never re-fetched — correct for sources whose history is
    truly immutable. Sources that backfill history progressively server-side
    (e.g. twstock minute lines, Taiwan futures) pass a TTL: a past month that
    is empty or implausibly partial (first/last bar far from the month edges —
    the backfill-frontier month) and whose mtime is older than the TTL is
    treated as a cache miss and re-fetched, merged with the existing rows. If
    the re-fetch adds nothing the file is rewritten (mtime refreshed), so the
    source is hit at most once per TTL window per incomplete span.

    Directory: cache/{prefix}_{params}/
    Files:     YYYY-MM.parquet  (one per month)

    Daily-frequency prefixes listed in _SINGLE_FILE_PREFIXES are routed to the
    one-file-per-id layout (_extend_cache_single) — same contract, same return.
    """
    if prefix in _SINGLE_FILE_PREFIXES:
        if empty_marker_ttl_hours is not None:
            raise ValueError(f'{prefix}: empty_marker_ttl_hours is monthly-layout only')
        return _extend_cache_single(prefix, params, fetch_raw_fn, start, end)
    cache_dir = _monthly_cache_dir(prefix, params)
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow()
    end_str   = end or now.strftime('%Y-%m-%d')
    current_ym = now.strftime('%Y-%m')
    tomorrow   = (now + timedelta(days=1)).strftime('%Y-%m-%d')

    all_months    = list(_iter_months(start, end_str))
    past_months   = [ym for ym in all_months if ym < current_ym]
    present_months = [ym for ym in all_months if ym >= current_ym]   # current (+ any future)

    # ── Backfill missing/incomplete PAST months in contiguous spans ───────────
    # Past months are immutable once complete. Fetch each contiguous run of missing
    # months with a SINGLE ranged call — raw_fn chunks it concurrently internally —
    # instead of one slow sequential call per month, then split the result into
    # per-month parquets. A month re-fetched here is MERGED with any existing rows,
    # never blindly overwritten, so a thin/partial re-fetch cannot shrink the cache.
    # Empty months still get a marker file so they are never re-fetched — unless
    # empty_marker_ttl_hours is set, in which case an expired empty or implausibly-
    # partial month is treated as missing and re-fetched (see docstring).
    # Sub-5min klines get a one-shot month-head check (see _head_short_unverified);
    # every past month written below carries the verified flag so it never recurs.
    try:
        head_check = prefix == 'kline2' and _is_sub_5min(params.get('period', '1d'))
    except Exception:
        head_check = False
    footer_meta = _HEAD_VERIFIED_META if head_check else None

    def _needs_fetch(ym):
        path = cache_dir / f'{ym}.parquet'
        if not path.exists():
            return True
        if _written_before_month_end(path, ym):
            return True   # cached mid-month, tail may be missing — complete it once
        if head_check and _head_short_unverified(path, ym):
            return True   # cached from a clamped start, head may be missing — complete it once
        return (empty_marker_ttl_hours is not None
                and _stale_incomplete_month(path, empty_marker_ttl_hours, ym))
    missing = [ym for ym in past_months if _needs_fetch(ym)]
    for span in _contiguous_spans(missing):
        span_start = f'{span[0]}-01'
        span_end   = f'{_next_month(span[-1])}-01'   # exclusive upper bound
        df = fetch_raw_fn(span_start, span_end)
        if not df.empty:
            df = _normalise_index(df)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            by_month = {ym: grp for ym, grp in df.groupby(df.index.strftime('%Y-%m'))}
        else:
            by_month = {}
        for ym in span:
            grp  = by_month.get(ym)
            path = cache_dir / f'{ym}.parquet'
            if path.exists():
                try:
                    existing = _normalise_index(pd.read_parquet(path))
                except Exception:
                    existing = pd.DataFrame()
                if not existing.empty:
                    grp = existing if grp is None else pd.concat([existing, grp])
                    grp = grp[~grp.index.duplicated(keep='last')].sort_index()
            _atomic_to_parquet(grp if grp is not None else pd.DataFrame(), path, footer_meta)

    # One batched read for all past months instead of one read_parquet call per month
    # file — with hundreds of ids x ~140 month files each, the per-file open/parse
    # overhead is what makes warm multi-stock backtests slow (2026-08: 151s of a
    # tw_low_pe run was this loop). Caveat: a multi-file read takes the FIRST file's
    # schema — an empty-month marker (no columns) first in the list would silently
    # blank the whole id, and a month with a divergent schema would silently lose
    # columns. So filter markers out with a footer-only schema read (~50x cheaper
    # than a full read) and only batch when every schema matches; otherwise fall
    # back to the per-file loop, whose concat unions columns.
    frames = []
    non_empty, names0, uniform = [], None, True
    for ym in past_months:
        path = cache_dir / f'{ym}.parquet'
        names = pq.read_schema(path).names
        if not names:
            continue                      # empty-month marker
        if names0 is None:
            names0 = names
        elif names != names0:
            uniform = False
        non_empty.append(path)
    if non_empty and uniform:
        try:
            cached = pd.read_parquet(non_empty)
            if not cached.empty:
                frames.append(cached)
        except Exception:                 # e.g. same names, clashing dtypes
            uniform = False
    if non_empty and not uniform:
        for path in non_empty:
            cached = pd.read_parquet(path)
            if not cached.empty:
                frames.append(cached)

    # ── Current month (and any future month in range) — delta update ──────────
    for ym in present_months:
        path     = cache_dir / f'{ym}.parquet'
        ym_start = f'{ym}-01'
        if path.exists():
            cached = _normalise_index(pd.read_parquet(path))
            last_ts = cached.index[-1].strftime('%Y-%m-%d')
            delta = fetch_raw_fn(last_ts, tomorrow)
            if not delta.empty:
                delta  = _normalise_index(delta)
                merged = pd.concat([cached, delta])
                merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                _atomic_to_parquet(merged, path)
                frames.append(merged)
            else:
                frames.append(cached)
        else:
            df = fetch_raw_fn(ym_start, tomorrow)
            if df.empty:
                continue
            df = _normalise_index(df)
            df = df[~df.index.duplicated(keep='last')].sort_index()
            _atomic_to_parquet(df, path)
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames)
    result = _normalise_index(result)
    result = result[~result.index.duplicated(keep='last')].sort_index()

    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end_str) + pd.Timedelta(days=1)
    return result[(result.index >= start_ts) & (result.index < end_ts)]


def _save_monthly(prefix, params, df):
    """Split df by month and save each month's slice to its own parquet file.
    Used by batch fetchers to populate the monthly cache after a bulk API call.
    """
    cache_dir = _monthly_cache_dir(prefix, params)
    cache_dir.mkdir(parents=True, exist_ok=True)
    df = _normalise_index(df)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    for ym, grp in df.groupby(df.index.strftime('%Y-%m')):
        path = cache_dir / f'{ym}.parquet'
        if path.exists():
            existing = _normalise_index(pd.read_parquet(path))
            grp = pd.concat([existing, grp])
            grp = grp[~grp.index.duplicated(keep='last')].sort_index()
        grp.to_parquet(path, compression='snappy')


# ── Single-file cache layout (daily datasets) ─────────────────────────────────
#
# Monthly partitioning is right for minute bars (hundreds of thousands of rows,
# only the current month ever changes) but wrong for daily series: a 台股 daily
# dataset is ~20 rows / 7KB per month, so a 300-stock universe is 41,400 tiny
# parquets and the per-file open/parse/write overhead dominates everything.
# Measured on a real Windows customer box (2026-08-22, 300 stocks, 723k rows):
# cold write 41,400 monthly files ≈ 13 min vs 300 single files ≈ 2.3 s; warm
# read 32.0 s vs 1.1 s; the same 300-stock Type C backtest went 14.5 min cold /
# 57 s warm → 45 s cold / 15 s warm. So daily datasets keep ONE parquet per
# (prefix, id) whose parquet footer metadata carries what the monthly layout
# encoded in file names and mtimes: which months are covered (one contiguous
# range — fetch spans always fill the whole requested range, empty months
# included, so no empty-month markers are needed) and when the tail was last
# fetched (a month is complete iff it was fetched after it ended; the current
# month is delta-updated on every call, exactly as before). Meta lives INSIDE
# the parquet (schema metadata key b'blave_cache_meta'), so frame and coverage
# are always written together in one atomic replace — no sidecar that could
# describe somebody else's frame under concurrent writers.
#
# File: cache/{prefix}_{params}.parquet   all rows, tz-naive UTC index, sorted;
#       footer meta {"from": "YYYY-MM", "to": "YYYY-MM",
#                    "tail_fetched_at": "YYYY-MM-DDTHH:MM:SS"}
# An existing monthly directory for the same (prefix, params) is consolidated
# into the single file on first touch and removed — machines already in the
# field migrate lazily, no manual step.
_SINGLE_FILE_PREFIXES = frozenset({
    'twstock_price', 'twstock_price_nonadj', 'twstock_inst', 'twstock_shareholding',
    'twstock_per', 'twstock_foreign_sh',
    'twmarket_index', 'twmarket_turnover', 'twmarket_institutional', 'twmarket_margin',
    'twfutures_institutional',
})
_META_TS_FMT = '%Y-%m-%dT%H:%M:%S'
_META_KEY = b'blave_cache_meta'


def _single_path(prefix, params):
    return Path(f'{_monthly_cache_dir(prefix, params)}.parquet')   # same stem as the old directory


def _write_single(prefix, params, df, meta):
    """One atomic write: frame + coverage meta in the parquet footer."""
    path = _single_path(prefix, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not df.empty:
        df = _normalise_index(df)
        df = df[~df.index.duplicated(keep='last')].sort_index()
    table = pa.Table.from_pandas(df, preserve_index=True)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}),
                                           _META_KEY: json.dumps(meta).encode()})
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_single_meta(prefix, params):
    """Footer-only read of the coverage meta (no data pages). None if absent/unreadable."""
    path = _single_path(prefix, params)
    try:
        raw = (pq.read_schema(path).metadata or {}).get(_META_KEY)
        meta = json.loads(raw) if raw else None
        if meta and all(k in meta for k in ('from', 'to', 'tail_fetched_at')):
            return meta
    except Exception:
        pass
    return None


def _read_monthly_dir_all(cache_dir):
    """Read every month file in a monthly cache dir. Returns (frame, months_ok,
    months_bad): markers count as ok (fetched, empty); unreadable files are
    reported in months_bad so the caller can keep them OUT of the claimed
    coverage. Same schema-uniform batch read / per-file fallback as
    _extend_cache_monthly."""
    paths, names0, uniform, ok, bad = [], None, True, [], []
    for path in sorted(cache_dir.glob('*.parquet')):
        ym = path.stem
        if not (len(ym) == 7 and ym[4] == '-'):
            continue                      # not a month file (e.g. a stray tmp)
        try:
            names = pq.read_schema(path).names
        except Exception:
            bad.append(ym)
            continue
        ok.append(ym)
        if not names:
            continue                      # empty-month marker
        if names0 is None:
            names0 = names
        elif names != names0:
            uniform = False
        paths.append(path)
    if not paths:
        return pd.DataFrame(), ok, bad
    df = None
    if uniform:
        try:
            df = pd.read_parquet(paths)
        except Exception:
            df = None
    if df is None:
        frames = []
        for p in paths:
            try:
                f = pd.read_parquet(p)
            except Exception:
                bad.append(p.stem); ok.remove(p.stem)
                continue
            if not f.empty:
                frames.append(f)
        df = pd.concat(frames) if frames else pd.DataFrame()
    return df, ok, bad


def _migrate_monthly_to_single(prefix, params):
    """Consolidate an old monthly directory into the single-file layout and
    remove it. Claimed coverage = the LAST contiguous run of readable month
    files (a directory can legitimately hold disjoint ranges — a 2021 backtest
    then a 2025 live run — and claiming the gap would freeze a hole forever;
    rows outside the claimed range are kept and simply get overlaid when an
    earlier start is requested). tail_fetched_at = mtime of the LAST month's
    file, which is exactly what the monthly layout used to judge that month's
    completeness (only the last month can ever be incomplete). The directory
    is renamed first so a concurrent caller never globs a half-deleted dir:
    the loser of the rename sees no cache and takes the cold path, which merges
    on write (_merge_single). Returns (frame, meta) or (None, None)."""
    cache_dir = _monthly_cache_dir(prefix, params)
    work = cache_dir.with_name(f'{cache_dir.name}.migrating.{os.getpid()}')
    try:
        os.rename(cache_dir, work)
    except OSError:
        return None, None                 # someone else is migrating it, or it's gone
    try:
        df, ok, _bad = _read_monthly_dir_all(work)
        spans = _contiguous_spans(sorted(set(ok)))
        if not spans:
            shutil.rmtree(work, ignore_errors=True)   # nothing readable in it
            return None, None
        last = spans[-1]
        try:
            newest = (work / f'{last[-1]}.parquet').stat().st_mtime
        except Exception:
            newest = 0                    # unstatable → treated as never complete → re-fetched
        meta = {'from': last[0], 'to': last[-1],
                'tail_fetched_at': datetime.utcfromtimestamp(newest).strftime(_META_TS_FMT)}
        _write_single(prefix, params, df, meta)
    except Exception:
        # Keep the history: put the directory back so the next call retries the
        # migration instead of paying a full cold fetch for 41k files' worth of data.
        try:
            os.rename(work, cache_dir)
        except OSError:
            pass
        raise
    shutil.rmtree(work, ignore_errors=True)       # only after the single file landed
    return (_normalise_index(df) if not df.empty else df), meta


def _read_single(prefix, params):
    """→ (frame, meta) or (None, None) when nothing is cached. Migrates a
    monthly directory on first touch."""
    path = _single_path(prefix, params)
    if path.exists():
        meta = _read_single_meta(prefix, params)
        if meta is not None:
            try:
                df = pd.read_parquet(path)
            except Exception:
                df = None                 # unreadable: fall through, treat as uncached
            if df is not None:
                # A monthly dir (rename lost a race / an earlier cold write beat the
                # migration) or an orphan .migrating.* would otherwise sit forever.
                stale = _monthly_cache_dir(prefix, params)
                for d in [stale] + list(stale.parent.glob(f'{stale.name}.migrating.*')):
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
                return (_normalise_index(df) if not df.empty else df), meta
    cache_dir = _monthly_cache_dir(prefix, params)
    if cache_dir.is_dir():
        return _migrate_monthly_to_single(prefix, params)
    return None, None


def _has_single_cache(prefix, params):
    if _single_path(prefix, params).exists():
        return True
    cache_dir = _monthly_cache_dir(prefix, params)
    return cache_dir.is_dir() and any(cache_dir.glob('*.parquet'))


def _tail_needs_completion(meta, ym):
    """True if month `ym` (<= meta['to']) may be missing its tail: it was last
    fetched before it ended — the single-file twin of _written_before_month_end."""
    try:
        fetched = datetime.strptime(meta['tail_fetched_at'], _META_TS_FMT)
    except Exception:
        return True
    return fetched < _month_end_utc(ym)


def _merge_frames(a, b):
    parts = [x for x in (a, b) if x is not None and not x.empty]
    if not parts:
        return pd.DataFrame()
    out = pd.concat([_normalise_index(x) for x in parts])
    return out[~out.index.duplicated(keep='last')].sort_index()


def _extend_cache_single(prefix, params, fetch_raw_fn, start, end):
    """One-file-per-id twin of _extend_cache_monthly. Fetch windows are month-
    aligned like the monthly layout's spans, so the batch prefetch in
    _fetch_batch_cached serves the same spans:
      - no cache:        fetch [from-01, upper)
      - earlier start:   fetch [req_from-01, from-01) and prepend
      - tail:            fetch [max(last cached bar, to-01), upper) when the
                         last covered month is the current one or was fetched
                         before it ended; or [next_month(to)-01, upper) when
                         the request reaches past a complete `to`
    upper = tomorrow when the request touches the current month, else the first
    day after the requested end month. Returns rows in [start, end]."""
    now        = datetime.utcnow()
    end_str    = end or now.strftime('%Y-%m-%d')
    current_ym = now.strftime('%Y-%m')
    tomorrow   = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    req_from, req_to = start[:7], end_str[:7]
    cap_to = min(req_to, current_ym)       # never claim coverage of a future month
    upper  = tomorrow if req_to >= current_ym else f'{_next_month(req_to)}-01'
    stamp  = now.strftime(_META_TS_FMT)

    df, meta = _read_single(prefix, params)
    changed = False
    if df is None:
        fetched = fetch_raw_fn(f'{req_from}-01', upper)
        df = _merge_frames(fetched, None)
        meta = {'from': req_from, 'to': cap_to, 'tail_fetched_at': stamp}
        # Merge rather than write: a concurrent migration may have landed a
        # wider file between our read and now (we'd be the rename-race loser).
        _merge_single(prefix, params, df, meta)
        df, meta2 = _read_single(prefix, params)
        if df is None:                    # vanishingly unlikely; keep the fetched frame
            df = _merge_frames(fetched, None)
        else:
            meta = meta2
        changed = False
    else:
        if req_from < meta['from']:
            early = fetch_raw_fn(f'{req_from}-01', f"{meta['from']}-01")
            df = _merge_frames(early, df)
            meta['from'] = req_from
            changed = True
        delta_start = None
        if meta['to'] >= current_ym or _tail_needs_completion(meta, meta['to']):
            floor = f"{meta['to']}-01"
            if not df.empty:
                floor = max(floor, df.index[-1].strftime('%Y-%m-%d'))
            delta_start = floor
        elif req_to > meta['to']:
            delta_start = f"{_next_month(meta['to'])}-01"
        if delta_start is not None and delta_start < upper:
            delta = fetch_raw_fn(delta_start, upper)
            df = _merge_frames(df, delta)
            meta['to'] = max(meta['to'], cap_to)
            meta['tail_fetched_at'] = stamp
            changed = True
    if changed:
        _write_single(prefix, params, df, meta)
    if df.empty:
        return pd.DataFrame()
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end_str) + pd.Timedelta(days=1)
    return df[(df.index >= start_ts) & (df.index < end_ts)]


def _merge_meta(old, new):
    """Coverage union ONLY when the two ranges overlap or touch — a gap between
    them was never fetched and must not be claimed (it would freeze a hole).
    Disjoint: keep the segment with the larger `to` (same rule as migration;
    the other segment's rows stay in the file as harmless extras and get
    overlaid by the next early fetch). tail_fetched_at follows the segment
    that owns `to`; when both end in the same month, the fresher stamp wins."""
    a, b = (old, new) if old['to'] <= new['to'] else (new, old)   # a ends first
    if _next_month(a['to']) >= b['from'] and _next_month(b['to']) >= a['from']:
        if a['to'] == b['to']:
            tail = max(a['tail_fetched_at'], b['tail_fetched_at'])
        else:
            tail = b['tail_fetched_at']
        return {'from': min(a['from'], b['from']), 'to': b['to'], 'tail_fetched_at': tail}
    return dict(b)


def _merge_single(prefix, params, df, meta):
    """Write `df`+`meta` merged with whatever is already cached — never narrows
    or blanks an existing file. Used by the phase-2 batch save and by the cold
    path of _extend_cache_single (a concurrent migration may have landed a wider
    file in between)."""
    existing, old_meta = _read_single(prefix, params)
    if existing is None:
        _write_single(prefix, params, df if df is not None else pd.DataFrame(), meta)
        return
    if df is None or df.empty:
        return                            # never blank a cache that has something
    _write_single(prefix, params, _merge_frames(existing, df), _merge_meta(old_meta, meta))


def _save_single(prefix, params, df, start, end):
    """Phase-2 twin of _save_monthly + _mark_empty_months: a full-range batch
    fetch covered every month in [start, end] (empty ones included). Merges with
    whatever is already cached (an id can reach phase 2 after a phase-1 demotion
    in a rate-limit storm, and a wider older cache must survive that); an empty
    result only creates a file when none exists (same rule as _mark_empty_months).
    tail_fetched_at = min(now, end+1d): "freshness = how far the fetch reached",
    so a past mid-month `end` leaves that month marked incomplete and the next
    call completes it instead of freezing a hole."""
    now = datetime.utcnow()
    end_str = end or now.strftime('%Y-%m-%d')
    reached = min(now, datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=1))
    meta = {'from': start[:7], 'to': min(end_str[:7], now.strftime('%Y-%m')),
            'tail_fetched_at': reached.strftime(_META_TS_FMT)}
    _merge_single(prefix, params, df, meta)


# ── TTL single-file cache helpers (fundamentals / snapshots) ──────────────────
# 不是按月布局:整份数据一个 parquet,mtime 超龄即整份重抓。台股基本面
# (twstock_fin/bs/rev)、股票清单、市值排名、大盤除息點數共用这三个助手。

def _fundamental_cache_path(prefix, stock_id):
    return _CACHE_DIR / f'{prefix}_{stock_id}.parquet'


def _load_fundamental_cache(path, max_age_days=30):
    if not path.exists():
        return None
    if (time.time() - path.stat().st_mtime) / 86400 > max_age_days:
        return None
    return pd.read_parquet(path)


def _save_fundamental_cache(path, df):
    path.parent.mkdir(exist_ok=True)
    df.to_parquet(path, compression='snappy')
