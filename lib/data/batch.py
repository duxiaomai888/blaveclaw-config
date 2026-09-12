"""Batch fetcher family — _fetch_batch_cached and friends.

拆分自原 lib/data.py。多 id 批量抓取的公共骨架:热 id 走增量(预取当前月
delta 走 batch 端点,不是一 id 一请求),冷 id 走全区段批量,失败 id 绝不
被误标为空月。kline.py(fetch_kline_batch)和 twstock.py(各 *_batch)都
从这里走。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd

from .http import BASE, _retry_get
from .cache import (
    _SINGLE_FILE_PREFIXES,
    _extend_cache_monthly,
    _has_single_cache,
    _iter_months,
    _monthly_cache_dir,
    _normalise_index,
    _read_single_meta,
    _save_monthly,
    _save_single,
    _tail_needs_completion,
    _written_before_month_end,
)

__all__ = ['_mark_empty_months', '_fetch_batch_cached', '_fetch_twstock_cached_batch']


def _mark_empty_months(prefix, sid, start, end):
    """Write empty-marker parquets for every PAST month in [start, end] that has
    no cached file.

    The /batch endpoint only returns months that have data, so the empty early
    months (e.g. institutional before the dataset existed) never get a file from
    _save_monthly — and the next run's extend path would re-fetch every one of
    them. Marking them here keeps a cold batch fetch a true cache hit on re-run.
    """
    cache_dir = _monthly_cache_dir(prefix, {'id': sid})
    cache_dir.mkdir(parents=True, exist_ok=True)
    current_ym = datetime.utcnow().strftime('%Y-%m')
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    for ym in _iter_months(start, end_str):
        if ym >= current_ym:        # never freeze the current (still-growing) month
            continue
        path = cache_dir / f'{ym}.parquet'
        if not path.exists():
            pd.DataFrame().to_parquet(path)


def _fetch_batch_cached(prefix, batch_url, id_param_name, raw_fn, parse_fn, ids, start, end, headers,
                         chunk_size=50, mark_empty_months=True, start_param='start', end_param='end',
                         date_chunk_days=None):
    """Shared batch fetcher for monthly-cached datasets (Taiwan stocks and stock futures).

    Phase 1: ids that already have a local cache are extended (current-month delta).
             The delta itself is fetched through batch_url in chunk_size-id chunks — NOT
             one request per id — then handed to _extend_cache_monthly for the merge/write.
             (Previously this phase called raw_fn per id individually: on a warm run with
             ids in the hundreds that meant that many single-id HTTP calls, each paying
             api_plan_required's full auth cost — bcrypt check + 2 uncached MySQL lookups
             + 2 Redis rate-limit hits — independently, which is what actually made warm
             runs slow, not the local parquet reads. See twstock_momentum backtest timeout,
             2026-07.)
    Phase 2: ids with no local cache yet are fetched the same way, chunk_size ids per
             request, full requested range.

    raw_fn(id, start, end, headers) -> DataFrame   single-id fallback (missing past months)
    parse_fn(records) -> DataFrame                 one id's batch records -> cached frame
    """
    results, uncached = {}, []

    single = prefix in _SINGLE_FILE_PREFIXES
    to_extend = []
    for _id in ids:
        if single:
            cached = _has_single_cache(prefix, {'id': _id})
        else:
            cache_dir = _monthly_cache_dir(prefix, {'id': _id})
            cached = cache_dir.exists() and bool(list(cache_dir.glob('*.parquet')))
        (to_extend if cached else uncached).append(_id)

    def _date_spans(range_start, range_end):
        """Split [range_start, range_end] into <= date_chunk_days pieces. Some batch
        endpoints (crypto /kline*) silently clamp an over-long single request to their
        own max window instead of erroring — chunking client-side is the only way to
        actually get the full requested range back."""
        if not date_chunk_days:
            return [(range_start, range_end)]
        s = datetime.strptime(range_start, '%Y-%m-%d')
        e = datetime.utcnow() if not range_end else datetime.strptime(range_end, '%Y-%m-%d')
        spans, cursor = [], s
        while cursor <= e:
            span_end = min(cursor + timedelta(days=date_chunk_days), e)
            spans.append((cursor.strftime('%Y-%m-%d'), span_end.strftime('%Y-%m-%d')))
            cursor = span_end + timedelta(days=1)
        return spans

    def _fetch_batch_range(id_list, range_start, range_end):
        """chunk_size ids per request x date_chunk_days-sized date spans, all issued
        concurrently. Returns ({id: DataFrame}, failed_ids): frames merged across spans,
        missing/empty ids simply absent (caller treats absence as 'no data') — EXCEPT
        ids in failed_ids, whose chunk errored or was server-side rate-limited; for
        those, absence is unknown, not 'empty', and must never be cached as empty."""
        out, failed_ids = {}, set()
        id_chunks = [id_list[i:i + chunk_size] for i in range(0, len(id_list), chunk_size)]
        date_spans = _date_spans(range_start, range_end)
        jobs = [(idx, chunk, span) for idx, chunk in enumerate(id_chunks) for span in date_spans]

        def _fetch_chunk(idx, chunk, span):
            span_start, span_end = span
            partial = {}
            try:
                params = {id_param_name: ','.join(chunk), start_param: span_start, end_param: span_end}
                r = _retry_get(batch_url, headers=headers, params=params, timeout=120)
                body = r.json()
                failed = body.get('failed', [])
                if failed:
                    print(f'  [batch] {batch_url} server-side fetch failed (rate limit or upstream error), dropped: {failed}')
                    failed_ids.update(failed)
                for _id, records in body.get('data', {}).items():
                    if records:
                        partial[_id] = _normalise_index(parse_fn(records))
            except Exception as e:
                print(f'  [batch] {batch_url} chunk {idx + 1} {span}: error: {e}')
                failed_ids.update(chunk)
            return partial

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_fetch_chunk, idx, chunk, span) for idx, chunk, span in jobs]
            for future in as_completed(futures):
                for _id, df in future.result().items():
                    out[_id] = pd.concat([out[_id], df]) if _id in out else df

        for _id, df in out.items():
            out[_id] = df[~df.index.duplicated(keep='last')].sort_index()
        return out, failed_ids

    # Pre-fetch the delta for every to_extend id in one batched pass instead of
    # per-id inside _extend_one below. _extend_cache_monthly asks for
    # (last_cached_ts, tomorrow) which falls inside the current month, so a
    # current-month-start..tomorrow batch fetch covers the common case. After a
    # month rollover it ALSO asks each id to complete the previous month (its file
    # was last written mid-month — see _written_before_month_end); when any id
    # needs that, widen the batch window to the previous month's start so those
    # spans are served from the same batched pass — otherwise every warm id would
    # fall back to one single-id HTTP call each (the twstock_momentum-timeout
    # storm, once per month across the whole warm cache). Extra days before each
    # id's own last_ts are harmless, _extend_cache_monthly dedupes by index.
    prefetch_batch, prefetch_failed, prefetch_start = {}, set(), None
    if to_extend:
        now = datetime.utcnow()
        current_ym = now.strftime('%Y-%m')
        prev_ym = (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')

        def _prev_month_needs_completion(_id):
            if single:
                meta = _read_single_meta(prefix, {'id': _id})   # footer only, no data pages
                # unknown / older coverage → treat as needing the wider window
                return meta is None or meta['to'] < prev_ym or _tail_needs_completion(meta, prev_ym)
            path = _monthly_cache_dir(prefix, {'id': _id}) / f'{prev_ym}.parquet'
            return not path.exists() or _written_before_month_end(path, prev_ym)

        widen = (prev_ym >= start[:7]
                 and any(_prev_month_needs_completion(_id) for _id in to_extend))
        prefetch_start = f'{prev_ym}-01' if widen else f'{current_ym}-01'
        prefetch_batch, prefetch_failed = _fetch_batch_range(to_extend, prefetch_start, tomorrow)

    def _make_fetch_fn(_id):
        def _fetch(s, e):
            # Any span inside the pre-fetched window (current-month delta, and the
            # previous-month completion after a rollover): serve from the batch.
            if prefetch_start is not None and s >= prefetch_start:
                if _id in prefetch_failed:
                    # Absence is unknown, not 'no data' — serving an empty frame here
                    # would let the previous-month completion path rewrite the month
                    # file (mtime past month end = complete) and freeze the hole.
                    # Raising demotes this id to the phase-2 full-range batch refetch.
                    raise RuntimeError(f'batch prefetch failed for {_id}')
                df = prefetch_batch.get(_id)
                if df is None:
                    return pd.DataFrame()
                return df[(df.index >= s) & (df.index < e)]
            # Deeper past-month holes (rare — a partially-cached id) fall back to
            # the single-id fetch; not worth batching for an edge case.
            return raw_fn(_id, s, e, headers)
        return _fetch

    def _extend_one(_id):
        return _extend_cache_monthly(
            prefix, {'id': _id},
            _make_fetch_fn(_id),
            start, end,
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_extend_one, _id): _id for _id in to_extend}
        for future in as_completed(futures):
            _id = futures[future]
            try:
                results[_id] = future.result()
            except Exception:
                uncached.append(_id)

    # Month-aligned start for the single-file layout, like _extend_cache_monthly's
    # spans: the cache claims the whole start month, so it must hold the whole month
    # (a later request from the 1st would otherwise find a permanent hole).
    # (Single layout only: the monthly batch path keeps its exact `start` — sub-5min
    # kline batches are validated against the server's earliest date and a month-
    # aligned start before it is a hard 400, not a clamp.)
    fetched, failed_ids = _fetch_batch_range(uncached, f'{start[:7]}-01' if (single and start) else start, end)
    end_str = end or datetime.utcnow().strftime('%Y-%m-%d')
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end_str) + pd.Timedelta(days=1)
    for _id, df in fetched.items():
        if single:
            _save_single(prefix, {'id': _id}, df, start, end)
        else:
            _save_monthly(prefix, {'id': _id}, df)
        # match _extend_cache_monthly's own [start, end] clamp so both phases return
        # the same range regardless of how much extra the raw fetch pulled back
        results[_id] = df[(df.index >= start_ts) & (df.index < end_ts)]
    # Mark every in-range past month with no data as an empty parquet, so the next
    # run is a cache hit instead of re-fetching the empty months. Ids with any
    # failed chunk are skipped for the whole range: a transient fetch failure is
    # not evidence of an empty month, and a wrongly-frozen empty parquet would
    # hide that id's history on every future warm run. (Single-file layout: an
    # id that came back with no rows at all gets an empty frame whose footer meta
    # covers the range — same "fetched, empty" meaning; never overwrites a file
    # that already has rows.)
    if start and mark_empty_months:
        for _id in uncached:
            if _id in failed_ids:
                continue
            if single:
                if _id not in fetched:
                    _save_single(prefix, {'id': _id}, pd.DataFrame(), start, end)
            else:
                _mark_empty_months(prefix, _id, start, end)

    return results


def _fetch_twstock_cached_batch(prefix, endpoint, raw_fn, parse_fn, stock_ids, start, end, headers=None):
    """Shared batch fetcher for monthly-cached 台股 datasets. Thin wrapper over
    _fetch_batch_cached — kept for existing callers (stock_ids param name, 50/chunk)."""
    return _fetch_batch_cached(
        prefix, f'{BASE}/studio/market/twstock/batch/{endpoint}', 'stock_ids',
        raw_fn, parse_fn, stock_ids, start, end, headers, chunk_size=50)
