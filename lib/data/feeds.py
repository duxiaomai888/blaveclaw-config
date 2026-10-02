"""Publication-time alignment for non-price feeds (align_feed / FEED_TIMING).

拆分自原 lib/data.py。A feed row is stamped with the period it DESCRIBES (三大法人 for
trading day D is stamped D 00:00; a Blave alpha row is stamped with its bucket's open).
What a bar may use is what had been PUBLISHED by that bar's close. align_feed() re-stamps
each row with its availability time from FEED_TIMING and attaches to every bar the latest
row available by the bar's close (label + interval). Live (inside live_feeds()), a bar
whose due row has not landed raises FeedNotPublished instead of quietly using the previous
value; in a backtest those trailing bars are trimmed.

Each time is the LATER of the official publication and the moment Blave serves it: the
api caches most TW daily endpoints for 5 minutes (REDIS_TTL = 300), and the FinMind
fundamental endpoints (_get_fundamental: 月營收, 財報, 外資持股) per UTC day — a copy
fetched before the evening publish is served until the api's UTC date rolls over, 08:00
Taipei the next day. The one remaining 待確認 per entry is named in its basis; those keep
a conservative (late) time — never tighten one without a source.
"""
import logging

import numpy as np
import pandas as pd

__all__ = [
    'FEED_TIMING', 'FeedNotPublished', 'live_feeds', 'feed_available_at', 'align_feed',
    'TW_FLOWS', 'join_tw_flow',
]

_FINMIND_CHIP = 'https://finmind.github.io/tutor/TaiwanMarket/Chip/'
_FINMIND_FUND = 'https://finmind.github.io/tutor/TaiwanMarket/Fundamental/'
_FINMIND_TECH = 'https://finmind.github.io/tutor/TaiwanMarket/Technical/'
_FINMIND_DERIV = 'https://finmind.github.io/tutor/TaiwanMarket/Derivative/'
_TWSE_ESHOP = 'https://eshop.twse.com.tw/zh/product/detail/'
_FSC_FIN_RULES = 'https://law.fsc.gov.tw/LawContent.aspx?id=GL000593'
_API_CACHE = pd.Timedelta(minutes=5)          # api REDIS_TTL = 300 on the TW daily endpoints


def _same_day_at(hour, minute=0):
    """Daily row stamped with its trading date → that date at HH:MM (feed tz)."""
    return lambda stamps: stamps.normalize() + pd.Timedelta(hours=hour, minutes=minute)


def _next_day_at(hour, minute=0):
    return lambda stamps: stamps.normalize() + pd.Timedelta(days=1, hours=hour, minutes=minute)


def _next_day_start(stamps):
    return stamps.normalize() + pd.Timedelta(days=1)


def _after_own_period(stamps, period):
    return stamps + period


def _weekday_on_or_after(days):
    """Saturday / Sunday → the following Monday (FinMind's fundamental refresh is weekdays)."""
    return days + pd.to_timedelta(np.where(days.dayofweek == 5, 2, np.where(days.dayofweek == 6, 1, 0)),
                                  unit='D')


def _revenue_available(stamps, due_day=10):
    """FinMind TaiwanStockMonthRevenue stamps March revenue 2019-04-01 (the month it is filed
    in). Filed by the `due_day` (證券交易法 §36: the 10th; 保險業 the 15th from FY2026, see
    _revenue_available_insurance) → FinMind refreshes weekdays 18:00 → the api's UTC-day
    cache serves it from 08:00 Taipei the next day."""
    due = stamps.normalize() - pd.to_timedelta(stamps.day - due_day, unit='D')
    return _weekday_on_or_after(due) + pd.Timedelta(days=1, hours=8)


def _revenue_available_insurance(stamps):
    """公開發行公司財務報告及營運情形公告申報特殊適用範圍辦法 §3(5): 保險業 may file monthly
    revenue by the 15th from FY 115 (2026) on — January 2026 revenue is stamped 2026-02-01."""
    from_2026 = stamps.tz_localize(None) >= pd.Timestamp('2026-02-01') if stamps.tz is not None \
        else stamps >= pd.Timestamp('2026-02-01')
    return _revenue_available(stamps, 15).where(from_2026, _revenue_available(stamps, 10))


def _quarterly_report_available(stamps, q2_deadline=(8, 14)):
    """Filing deadlines (證券交易法 §36): Q1/Q3 45 days after quarter end (5/15, 11/14), Q2
    8/14 (金融控股·銀行·證券·期貨·保險 listed issuers: 8/31, see
    _quarterly_report_available_finance), annual 3/31. FinMind then serves it; the api's
    UTC-day cache → 08:00 Taipei the day after the deadline. Keyed on the stamp's quarter,
    so it holds whether the row is stamped at quarter start or end."""
    q, y = stamps.quarter, stamps.year
    month = np.select([q == 1, q == 2, q == 3], [5, q2_deadline[0], 11], 3)
    day = np.select([q == 1, q == 2, q == 3], [15, q2_deadline[1], 14], 31)
    year = np.where(q == 4, y + 1, y)
    deadline = pd.DatetimeIndex(pd.to_datetime({'year': year, 'month': month, 'day': day}))
    return deadline.tz_localize(stamps.tz) + pd.Timedelta(days=1, hours=8)


def _quarterly_report_available_finance(stamps):
    return _quarterly_report_available(stamps, q2_deadline=(8, 31))


def _weekly_shareholding_available(stamps):
    return stamps.normalize() + pd.Timedelta(days=3, hours=8)


def _econ_available(stamps, frame):
    """Event rows: `real` is known at the release time plus the api's 5-minute cache of the
    upstream calendar. An event with no published time (time None, stamped 00:00) is taken
    as known only from the next day."""
    no_time = frame['time'].isna().to_numpy() if 'time' in frame.columns else np.zeros(len(stamps), bool)
    return (stamps + _API_CACHE).where(~no_time, _next_day_start(stamps))


def _alpha():
    return {'tz': 'UTC', 'period': 'infer', 'available': 'after_period', 'calendar': 'bars',
            'fresh': 'raise',
            'basis': "api enterprise/crypto/routes.py passes only_finalized_data=True and "
                     "crypto/basic.py resamples label-left: a bucket's row exists only once its "
                     "last base bar is collected, so it is final at the bucket's close. Arrival lag "
                     "after that is unconfirmed (local cache files: present 5–57 min after close, "
                     "upper bounds only); the live gate waits for the row."}


def _tw_daily(available, basis, fresh='raise'):
    return {'tz': 'Asia/Taipei', 'period': pd.Timedelta(days=1), 'available': available,
            'calendar': 'tw_trading_days', 'fresh': fresh, 'basis': basis}


FEED_TIMING = {
    **{name: _alpha() for name in (
        'holder_concentration', 'funding_rate', 'taker_intensity', 'whale_hunter',
        'unusual_movement', 'squeeze_momentum', 'liquidation', 'market_direction',
        'capital_shortage', 'market_sentiment', 'top_trader_exposure')},
    'twstock_price': _tw_daily(
        _same_day_at(17, 35), f"TWSE 每日收盤行情 is produced 14:00 / 15:30 / 17:30 ({_TWSE_ESHOP}"
        "cfec9a1470e448ec91bfde006db361e8); the STOCK_DAY page, TPEx tradingStock and FinMind all "
        "showed the day's bar at 15:33 (2026-09-24), but whether the 14:00 version is already "
        "final is unconfirmed (盤後定價 trades 14:00–14:30), so the third version + 5 min is kept. "
        "The openapi.twse.com.tw / TPEx OpenAPI mirrors lag the sites (still the previous day at "
        "15:51) and are not a time basis"),
    'twstock_institutional': _tw_daily(
        _same_day_at(20, 5), f"TWSE 三大法人買賣超 final (incl. 鉅額) 20:00 ({_TWSE_ESHOP}"
        f"c4c87ac184e44896a05fcab5a9d544ec); FinMind 20:00 ({_FINMIND_CHIP}); + api cache 5 min"),
    'twstock_per': _tw_daily(
        _same_day_at(18, 5), f"TWSE 個股日本益比 18:00 ({_TWSE_ESHOP}8a82e9e697fc5f620198abeec9830097); "
        f"FinMind TaiwanStockPER 18:00 ({_FINMIND_TECH}); + api cache 5 min. TPEx publishes later — "
        "an OTC stock's row can land after this, which the live gate waits for"),
    'twstock_foreign_shareholding': _tw_daily(
        _next_day_at(8), f"TWSE 外資投資持股統計 final 21:30 ({_TWSE_ESHOP}fc2ca33908244644b066e0f12cb8efe5), "
        f"FinMind 21:00 ({_FINMIND_CHIP}), but the api serves it from the UTC-day cache "
        "(tw/twstock/services.py _get_fundamental) → next day 08:00"),
    'twstock_broker': _tw_daily(
        _next_day_start, f"TWSE 買賣日報表 16:00 ({_TWSE_ESHOP}c862b8472d7d46ccafbecca13c0336b0), FinMind "
        f"21:00 ({_FINMIND_CHIP}); Blave's store is written only by apijob@tw.twstock.broker_daily_update "
        "(21:30, retry 23:30 Taipei; one run observed: 2026-09-23 wrote the day at 21:31) → next "
        "day 00:00 covers the retry"),
    'twstock_broker_sparse': _tw_daily(_next_day_start, "as twstock_broker; one broker has no row "
                                       "on a day it did not trade, so freshness cannot be checked",
                                       fresh=None),
    'twmarket_institutional': _tw_daily(
        _same_day_at(19, 45), f"TWSE 三大法人買賣金額統計表 14:50 without, 約19:40 with 綜合帳戶/鉅額 "
        f"({_TWSE_ESHOP}d31c1b9570ae47058ec83a0bb1ffa419); FinMind 15:00 ({_FINMIND_CHIP}); + api "
        "cache 5 min. Assumes the stored history is the 19:40 version (unconfirmed) — if it is the "
        "14:50 one this is late, never early"),
    'twmarket_margin': _tw_daily(
        _same_day_at(21, 5), f"TWSE 融資融券餘額 約21:00 ({_TWSE_ESHOP}388dd3a09824427d8c01a9d2b21e820b); "
        f"FinMind 21:00 ({_FINMIND_CHIP}); + api cache 5 min"),
    'twmarket_turnover': _tw_daily(
        _next_day_start, "TWSE FMTQIK, fetched on request (+ api cache 5 min). TWSE publishes no time "
        f"for FMTQIK itself (its 每日收盤行情 product runs 14:00 / 15:30 / 17:30, {_TWSE_ESHOP}"
        "cfec9a1470e448ec91bfde006db361e8) — unconfirmed, next day 00:00 kept"),
    'twfutures_institutional': _tw_daily(
        _same_day_at(18, 5), f"FinMind TaiwanFuturesInstitutionalInvestors 18:00 ({_FINMIND_DERIV}); "
        "TAIFEX itself ~15:00 (api tw/twfutures/services.py _DAILY_PUBLISH_HOUR_TWN); + api cache 5 min"),
    'twfutures_pcr': _tw_daily(
        _next_day_start, "TAIFEX pcRatio page, fetched on request (+ api cache 5 min); TAIFEX publishes "
        "no time for it — unconfirmed, next day 00:00 kept"),
    'twstock_shareholding': {'tz': 'Asia/Taipei', 'period': pd.Timedelta(days=7),
                             'available': _weekly_shareholding_available, 'calendar': None,
                             'fresh': 'warn', 'basis': "TDCC weekly 集保戶股權分散表 (data = the week's "
                             "last business day, https://www.tdcc.com.tw/portal/zh/smWeb/qryStock) via "
                             "FinMind TaiwanStockHoldingSharesPer; neither publishes a time — "
                             "unconfirmed, data date + 3 days 08:00 kept"},
    'twstock_monthly_revenue': {'tz': 'Asia/Taipei', 'period': 'month',
                                'available': _revenue_available, 'calendar': None, 'fresh': 'warn',
                                'basis': f"證券交易法 §36 deadline the 10th; FinMind weekdays 18:00 "
                                         f"({_FINMIND_FUND}; stamp 2019-04-01 = March revenue); api "
                                         "UTC-day cache → next day 08:00. FSC may extend a holiday "
                                         f"month ({_FSC_FIN_RULES} §4-1)"},
    'twstock_monthly_revenue_insurance': {
        'tz': 'Asia/Taipei', 'period': 'month', 'available': _revenue_available_insurance,
        'calendar': None, 'fresh': 'warn',
        'basis': f"保險業: the 15th from FY2026 ({_FSC_FIN_RULES} §3(5)), else as twstock_monthly_revenue"},
    'twstock_financials': {'tz': 'Asia/Taipei', 'period': 'quarter',
                           'available': _quarterly_report_available, 'calendar': None,
                           'fresh': 'warn', 'basis': "證券交易法 §36 deadlines (5/15, 8/14, 11/14, 3/31); "
                           "api UTC-day cache → the next day 08:00. FinMind's own ingest time is "
                           "undocumented (待確認) — the live warning surfaces a late one"},
    'twstock_financials_finance': {
        'tz': 'Asia/Taipei', 'period': 'quarter', 'available': _quarterly_report_available_finance,
        'calendar': None, 'fresh': 'warn',
        'basis': f"金融控股·銀行·證券·期貨·保險 listed issuers: Q2 within two months (8/31, "
                 f"{_FSC_FIN_RULES} §3(3)); Q1/Q3/annual as twstock_financials"},
    'twfutures_bid_ask_vol': {'tz': 'UTC', 'period': 'infer', 'available': 'after_period',
                              'delay': pd.Timedelta(seconds=30), 'calendar': 'bars', 'fresh': 'raise',
                              'basis': "api snapshot/run_sinopac_backfill.py _aggregate_ticks floors "
                                       "ticks to the minute (row = minute open); today's minutes are "
                                       "fetched on request and cached 30 s (tw/sinopac/services.py), "
                                       "so a row is final 30 s after its minute closes"},
    'fear_greed': {'tz': 'UTC', 'period': pd.Timedelta(days=1), 'available': _same_day_at(1),
                   'calendar': 'days', 'fresh': 'raise',
                   'basis': "alternative.me publishes one row a day at 00:00 UTC: on 2026-09-24 "
                            "08:16 UTC the API's time_until_update was 56,644 s = exactly 00:00 "
                            "UTC, and the row stamped that day was the one it had just published. "
                            "How long the API takes to actually serve the new row after 00:00 is "
                            "unconfirmed — +1 h kept; the live gate waits for it"},
    'economic_calendar': {'tz': 'Asia/Taipei', 'period': None, 'available': 'econ',
                          'calendar': 'self', 'fresh': 'raise', 'columns': ['real'],
                          'basis': "`real` at the release time + api cache 5 min (market/anue/"
                                   "services.py _CACHE_TTL); how long the upstream (鉅亨) takes to "
                                   "fill `real` is unconfirmed — the live gate waits for it"},
}
# fetcher-name aliases → the timing entry they share
for _alias, _key in (('twstock_price_adj', 'twstock_price'),
                     ('twstock_price_batch', 'twstock_price'),
                     ('twstock_price_adj_batch', 'twstock_price'),
                     ('twstock_institutional_batch', 'twstock_institutional'),
                     ('twstock_per_batch', 'twstock_per'),
                     ('twstock_foreign_shareholding_batch', 'twstock_foreign_shareholding'),
                     ('twstock_all_broker_net', 'twstock_broker'),
                     ('twstock_branch_daily_net', 'twstock_broker'),
                     ('twstock_trader_flows', 'twstock_broker'),
                     ('twstock_broker_net', 'twstock_broker_sparse'),
                     ('twstock_shareholding_batch', 'twstock_shareholding'),
                     ('twstock_monthly_revenue_batch', 'twstock_monthly_revenue'),
                     ('twstock_balance_sheet', 'twstock_financials'),
                     ('twstock_financials_batch', 'twstock_financials'),
                     ('twstock_balance_sheet_batch', 'twstock_financials')):
    FEED_TIMING[_alias] = FEED_TIMING[_key]


class FeedNotPublished(RuntimeError):
    """Live: the feed row this bar needs was due by `due_at` and is not in the data."""

    def __init__(self, source, need, due_at, last_bar):
        self.source, self.need, self.due_at, self.last_bar = source, need, due_at, last_bar
        super().__init__(
            f"❌ {source}: the row for {need} was due by {due_at} and is not in the data yet — "
            f"refusing to compute the signal for bar {last_bar} on the previous value. "
            f"wait_for_bar keeps retrying; if it never lands, the source is late.")


_live_feeds = 0   # >0 while a LIVE tick's fetch_data runs (runner / wait_for_bar)


class live_feeds:
    """Scope marking a live tick: align_feed raises FeedNotPublished instead of trimming."""
    def __enter__(self):
        global _live_feeds
        _live_feeds += 1
        return self

    def __exit__(self, *exc):
        global _live_feeds
        _live_feeds -= 1
        return False


def _utc_ns(idx):
    return idx.tz_convert('UTC').as_unit('ns').asi8


def _latest_by(avail_ns, stamp_ns, at_ns):
    """For each `at`: position (into the inputs) of the row with the greatest stamp among rows
    with avail <= at, or -1."""
    order = np.argsort(avail_ns, kind='stable')
    a, s = avail_ns[order], stamp_ns[order]
    run_max = np.maximum.accumulate(s) if len(s) else s
    best = np.maximum.accumulate(np.where(s == run_max, np.arange(len(s)), 0)) if len(s) else s
    k = np.searchsorted(a, at_ns, side='right') - 1
    out = np.full(len(at_ns), -1)
    has = k >= 0
    out[has] = order[best[k[has]]]
    return out


def _feed_times(frame, source, default_period=None):
    """→ (stamps, available_at, period) for a FEED_TIMING feed frame, both indexes tz-aware
    (naive stamps are read in the entry's tz). Raises ValueError on a frame without a time
    axis (long formats: pivot / unstack first)."""
    spec = FEED_TIMING[source]
    if len(frame) == 0:
        raise ValueError(f"{source}: the feed has no rows at all — the fetch returned nothing, so "
                         f"there is no way to tell which bars it covers")
    if spec['available'] == 'econ':
        stamps = pd.DatetimeIndex(frame['datetime'])
    else:
        stamps = frame.index
    if not isinstance(stamps, pd.DatetimeIndex):
        raise ValueError(f"{source}: needs a DatetimeIndex (long formats: pivot / unstack first)")
    if stamps.tz is None:
        stamps = stamps.tz_localize(spec['tz'])
    period = spec['period']
    if period == 'infer':
        d = pd.Series(stamps.sort_values()).diff().dropna()
        d = d[d > pd.Timedelta(0)]
        period = d.mode().iloc[0] if len(d) else default_period
        if period is None:
            raise ValueError(f"{source}: cannot infer the row period from a single row")
    if spec['available'] == 'after_period':
        avail = _after_own_period(stamps, period) + spec.get('delay', pd.Timedelta(0))
    elif spec['available'] == 'econ':
        avail = _econ_available(stamps, frame)
    else:
        avail = spec['available'](stamps)
    return stamps, avail, period


def feed_available_at(frame, source):
    """Per-row availability time (tz-aware) of a FEED_TIMING feed frame — what align_feed
    attaches by, and what the runner's look-ahead replay truncates a recorded feed by."""
    return _feed_times(frame.to_frame() if isinstance(frame, pd.Series) else frame, source)[1]


def align_feed(bars, feed, source, interval, bar_tz=None, columns=None):
    """Attach `feed` to `bars` by publication time. → DataFrame on the bars' index (trailing
    not-yet-published bars trimmed in a backtest) with the feed's value columns.

    bars:     DataFrame / Series / DatetimeIndex of the strategy's bars (label = bar open).
    feed:     the fetcher's frame, wide (one row per stamp; pivot long formats first). The
              economic calendar goes in as fetch_economic_calendar returned it, filtered to
              ONE indicator (`real` is the value).
    source:   FEED_TIMING key = the fetcher name without `fetch_` ('twstock_institutional').
    interval: the bars' interval ('1h', '60m', '1d', …); a bar may use a row only if the row
              was available by label + interval.
    bar_tz:   required when the bars' index is naive: 'UTC' for fetch_kline / intraday
              fetch_twfutures_ohlcv, 'Asia/Taipei' for fetch_twstock_price* daily bars.

    A bar whose due row is missing while an older one exists (a late source, a hole) gets
    NaN, not the older value. Live (live_feeds()) the LAST bar being in that state raises
    FeedNotPublished. Monthly / quarterly / weekly filings only warn: a late or delinquent
    filer must not halt a strategy, and the previous filing is what was actually known.
    """
    if source not in FEED_TIMING:
        raise ValueError(f"align_feed: unknown source {source!r} — one of {sorted(FEED_TIMING)}")
    spec = FEED_TIMING[source]
    index = bars if isinstance(bars, pd.DatetimeIndex) else bars.index
    if index.tz is None:
        if not bar_tz:
            raise ValueError("align_feed: the bars' index is naive — pass bar_tz ('UTC' for "
                             "fetch_kline and intraday fetch_twfutures_ohlcv, 'Asia/Taipei' for "
                             "fetch_twstock_price* daily bars)")
        index = index.tz_localize(bar_tz)
    bar_close = index + pd.Timedelta(interval)

    frame = feed.to_frame() if isinstance(feed, pd.Series) else feed
    stamps, avail, period = _feed_times(frame, source, default_period=pd.Timedelta(interval))
    if stamps.has_duplicates:
        raise ValueError(f"align_feed: {source} feed has repeated stamps — pivot it to one row per stamp first")
    cols = columns or spec.get('columns') or list(frame.columns)
    values = frame[cols].reset_index(drop=True)

    present = values.notna().any(axis=1).to_numpy()
    close_ns = _utc_ns(bar_close)
    s_ns, a_ns = _utc_ns(stamps), _utc_ns(avail)
    live_rows = np.flatnonzero(present)
    row = _latest_by(a_ns[present], s_ns[present], close_ns)
    row = np.where(row >= 0, live_rows[np.maximum(row, 0)] if len(live_rows) else -1, -1)
    out_index = bars if isinstance(bars, pd.DatetimeIndex) else bars.index
    if len(values):
        out = values.iloc[np.maximum(row, 0)].set_axis(out_index)
    else:
        out = pd.DataFrame(np.nan, index=out_index, columns=cols)
    out.loc[row < 0] = np.nan

    stale = np.zeros(len(index), dtype=bool)
    need_ns = np.full(len(index), -1, dtype=np.int64)
    if spec['fresh'] == 'raise' and spec['calendar']:
        local = index.tz_convert(stamps.tz)
        if spec['calendar'] == 'self':
            cand = stamps
        elif spec['calendar'] == 'tw_trading_days':
            # the dates a TW daily feed can have a row for: weekdays with a DAY-session bar.
            # TXF's night session labels Friday-night bars Saturday 00:00–05:00 (and the night
            # before a holiday, the holiday) — those dates never get a row.
            if pd.Timedelta(interval) < pd.Timedelta(days=1):
                local = local[(local.hour >= 8) & (local.hour < 14)]
            days = pd.DatetimeIndex(local.floor('D').unique())
            cand = days[days.dayofweek < 5]
        else:
            cand = pd.DatetimeIndex(local.floor(period).unique())
        if spec['available'] == 'after_period':
            c_avail = _after_own_period(cand, period) + spec.get('delay', pd.Timedelta(0))
        elif spec['available'] == 'econ':
            c_avail = avail
        else:
            c_avail = spec['available'](cand)
        c_s = _utc_ns(cand)
        req = _latest_by(_utc_ns(c_avail), c_s, close_ns)
        need_ns = np.where(req >= 0, c_s[np.maximum(req, 0)], -1)
        used_ns = np.where(row >= 0, s_ns[np.maximum(row, 0)], -1)
        stale = (need_ns >= 0) & (used_ns < need_ns)
        out.loc[stale] = np.nan
        if len(stale) and stale[-1]:
            k = int(np.flatnonzero(c_s == need_ns[-1])[0])
            need, due = cand[k], c_avail[k]
            if _live_feeds:
                raise FeedNotPublished(source, need, due, index[-1])
            tail = len(stale) - (np.flatnonzero(~stale)[-1] + 1 if (~stale).any() else 0)
            print(f"  ⚠️  {source}: the last {tail} bar(s) are cut — the row for {need} was due by "
                  f"{due} and is not in the data yet (live refuses these bars until it lands)")
            out = out.iloc[:len(out) - tail]
    elif spec['fresh'] == 'warn' and len(stamps) and present.any():
        last = stamps[present].max()
        nxt = (last + pd.DateOffset(months=1) if period == 'month' else
               last + pd.DateOffset(months=3) if period == 'quarter' else last + period)
        nxt_idx = pd.DatetimeIndex([nxt])
        due = spec['available'](nxt_idx)[0]
        if bar_close[-1] >= due:
            msg = (f"{source}: the next filing after {last.date()} was due by {due} and is not in "
                   f"the data — using the {last.date()} one (late filer, or a cached copy)")
            logging.warning(msg)
            print(f"  ⚠️  {msg}")
    return out


# kind → (fetcher name, FEED_TIMING source, needs an id, default column prefix)
TW_FLOWS = {
    'futures_institutional': ('fetch_twfutures_institutional', 'twfutures_institutional', True, 'fut_'),
    'stock_institutional':   ('fetch_twstock_institutional',   'twstock_institutional',   True, 'inst_'),
    'market_institutional':  ('fetch_twmarket_institutional',  'twmarket_institutional',  False, 'mkt_'),
    'margin':                ('fetch_twmarket_margin',         'twmarket_margin',         False, ''),
    'pcr':                   ('fetch_twfutures_pcr',           'twfutures_pcr',           False, ''),
    'per':                   ('fetch_twstock_per',             'twstock_per',             True, ''),
    'broker_total':          ('fetch_twstock_all_broker_net',  'twstock_all_broker_net',  True, 'broker_'),
    'broker_branch':         ('fetch_twstock_branch_daily_net', 'twstock_branch_daily_net', True, 'br_'),
}


def join_tw_flow(df, kind, interval, start, end, headers, id=None, prefix=None):
    """The common case in one call: fetch a Taiwan daily flow feed and attach it to `df`'s
    bars by publication time (align_feed). → `df` cut to the bars whose flow row is
    published, with the flow columns joined (named `prefix + column`).

    kind   id                    columns (before the prefix)
    futures_institutional  'TX' / 'MTX' / 'TMF' (TXF/MXF accepted)   {foreign|investment_trust|dealer}_{net_oi|long_oi|short_oi|net_deal}
    stock_institutional    stock id        foreign_net + the raw buy/sell columns
    market_institutional   —               foreign, investment_trust, dealer, total (元)
    margin                 —               margin_balance(_prev), margin_balance_value, short_balance(_prev)
    pcr                    —               pcr
    per                    stock id        dividend_yield, PER, PBR
    broker_total           stock id        net (all branches summed)
    broker_branch          stock id        one column per branch id

    Bars from any TW price fetcher work as they come: a naive intraday index is UTC
    (fetch_twfutures_ohlcv / fetch_twstock_ohlcv minute bars), a naive daily index is the
    Taipei date (fetch_twstock_price*), an aware index is used as is. Not for crypto bars:
    fetch_kline '1d' is naive UTC and would be read as Taipei dates — use align_feed. A row missing on a
    day it should exist is NaN on the bars it covers — never the previous day's value — and
    live, the tick refuses (FeedNotPublished) until it lands."""
    if kind not in TW_FLOWS:
        raise ValueError(f"join_tw_flow: kind must be one of {sorted(TW_FLOWS)}")
    fetcher, source, needs_id, default_prefix = TW_FLOWS[kind]
    if needs_id and not id:
        raise ValueError(f"join_tw_flow: kind {kind!r} needs id= (see the docstring table)")
    # looked up per call (on the package, where the fetchers are re-exported) so the
    # backtest recorder sees a fresh attribute read, and a patched fetcher is honored.
    import lib.data as _pkg
    fetch = getattr(_pkg, fetcher)
    args = (id, start, end, headers) if needs_id else (start, end, headers)
    flow = fetch(*args)
    if isinstance(flow, pd.Series):
        flow = flow.to_frame(flow.name or 'net')
    bar_tz = None
    if df.index.tz is None:
        bar_tz = 'UTC' if pd.Timedelta(interval) < pd.Timedelta(days=1) else 'Asia/Taipei'
    aligned = align_feed(df, flow, source, interval, bar_tz=bar_tz)
    pre = default_prefix if prefix is None else prefix
    return df.loc[aligned.index].join(aligned.add_prefix(pre))
