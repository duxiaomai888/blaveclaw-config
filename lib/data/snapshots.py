"""Crypto / market snapshot fetchers — point-in-time cross-sections, no local cache.

拆自官方 lib/data.py 的快照段(長短比 / 未平倉量 / CVD / 爆倉地圖 / 爆倉矩陣),
外加 Binance 24h ticker 與鉅亨新聞候選。這些端點回的不是時間序列而是「當下」的
橫截面,所以不做本地快取——每一次呼叫都代表 "now"。

_access gate 的位置與官方 monolith 一致,在 _retry_get 內(只檢查 {BASE} 開頭
的 URL、請求之前):沒權限時 raise DataAccessError,讓報表把它分成「缺資料」
而不是「端點壞了」。Binance 端點(fetch_binance_ticker_24h)是公開的、
官方 _binance_get 刻意不做 gate,這裡同樣不做。
"""
import pandas as pd
import requests

import lib.data as _pkg

from .http import BASE
from ._shared import _call_through
_retry_get = lambda *a, **k: _call_through('_retry_get', *a, **k)
from .kline import normalize_symbol, _binance_get, _BINANCE_LIMITER, _BINANCE_INFLIGHT

# _raw_snapshot is read off the package per call, not off this module's globals: a check that
# patches lib.data._raw_snapshot to fake the wire must reach every snapshot fetcher.
_resolve = lambda name: getattr(_pkg, name)


__all__ = [
    '_raw_snapshot',
    'fetch_long_short_ratio_table', 'fetch_long_short_ratio_coin',
    'fetch_open_interest_table', 'fetch_open_interest_coin',
    'fetch_liquidation_map', 'fetch_liquidation_exchanges',
    'fetch_cvd_table', 'fetch_cvd_coin',
    '_BINANCE_TICKER_24H', '_BINANCE_LIMITER', '_BINANCE_INFLIGHT',
    '_binance_get', 'fetch_binance_ticker_24h',
    'fetch_news',
]


def _raw_snapshot(endpoint, headers, params=None, allow_404=False):
    """Shared GET for the raw cross-exchange snapshot endpoints (long/short ratio, open
    interest, CVD, liquidation matrix). No local cache — the server holds the snapshot;
    every call means "now". A 503 (the scheduled job has no fresh result / a feed is
    silent) propagates as requests.HTTPError after _retry_get's backoff: an empty table
    would read as "nothing is happening", which is a different claim from "unknown".
    allow_404 → None for a coin no source collects (an answer, not an error)."""
    try:
        r = _retry_get(f'{BASE}/{endpoint}', headers=headers, params=params, timeout=30)
    except requests.exceptions.HTTPError as e:
        if allow_404 and e.response is not None and e.response.status_code == 404:
            return None
        raise
    return r.json().get('data', {})


def fetch_long_short_ratio_table(headers):
    """多空比總表 Long/short ratio (GET /long_short_ratio/get_table), every coin × every source, latest cross-section.
    Returns a dict — a point-in-time snapshot, not a time series:
      coins[]: token, token_id, and one ratio per source key (null when that exchange has
        no such feed for the coin). Ordered by Binance open-interest notional
      sources[]: the 10 feeds — exchange, key, type ('account' / 'top_account' /
        'top_position'), last_at, stale. Binance / OKX / Gate publish all three types,
        Bybit only 'account'. **Read `sources[]`, never a hard-coded key list.**
      summary{binance_long_majority, binance_tokens}, tokens_shown / tokens_total, full,
        updated_at
    The ratio is longs ÷ shorts; long share = r / (1 + r), computed by the caller.
    **"Top trader" means something different at each exchange** — Binance = the top 20 %
    of users by margin balance, OKX = the top 5 % of traders by open-position value, Gate
    has never published its rule. So `binance_top_account` and `okx_top_account` are not
    comparable as levels; compare each source against its own history instead.
    Only coins that resolve to a CoinMarketCap crypto are listed. An API key sees every
    row (`full: true`); anonymous callers get 30."""
    return _resolve('_raw_snapshot')('long_short_ratio/get_table', headers)


def fetch_long_short_ratio_coin(symbol, headers):
    """一檔幣的多空比 — one coin's long/short ratio per source (GET /long_short_ratio/get_coin). Returns a dict:
      latest{<source key>: {ts, value}}: newest 5-minute sample of each source
      series: bucket_seconds=3600, days=7, timestamp[] (epoch seconds, 168 slots,
        old → new), one array per source key (last sample of each hour, null for an hour
        with no sample), price[] (Binance perp close on the same frame, null when the coin
        has no Binance perp) with price_symbol / price_multiplier, and provisional_from
        (first slot that can still change)
      sources[]: the full roster — exchange, key, type, listed (False = that exchange has
        no such feed for this coin, its array is all null), last_at, stale
      symbol, token_id, updated_at (the newest source; it says nothing about the others —
        judge a single source by its own `last_at` / `stale`)
    `symbol` accepts BTC / BTCUSDT / btc. Returns None for a coin no source collects (the
    API's 404); 503 (a listed source unreadable) propagates as requests.HTTPError."""
    return _resolve('_raw_snapshot')('long_short_ratio/get_coin', headers,
                         {'symbol': symbol}, allow_404=True)


def fetch_open_interest_table(headers):
    """未平倉量總表 Open interest (GET /oi_imbalance/get_table — the path still carries the
    old "imbalance" name; this is the RAW table, not that indicator, see below), every coin
    × 5 exchanges, in USD notional.
    Basis: USDT-margined perpetuals only, USD notional, one-sided — an exchange that
    reports both sides is halved (`exchanges[].side_factor`, Gate = 0.5). Returns a dict:
      coins[]: token, token_id, oi_total (USD, summed across exchanges), chg_1h / chg_4h /
        chg_24h (decimal fractions, null when no exchange has a baseline at that window's
        start), market_cap, oi_mcap (= oi_total ÷ market cap), by_exchange{name: {oi,
        chg_1h, chg_4h, chg_24h}}
      exchanges[]: binance / okx / bingx / bybit / gate — whole-market oi, side_factor,
        since + full_7d (False = that feed started less than 7 days ago), last_at, stale
      total{oi, chg_*, n_exchanges, n_full_7d}, summary{oi_mcap_leader,
        oi_mcap_leader_value}, tokens_shown / tokens_total, full, updated_at
    Each exchange's value is already USD notional, so 1000PEPE-style multiplied contracts
    add up across exchanges without rescaling.
    **`oi_total` / `oi_mcap` here are NOT the "OI 失衡" indicator** (`/oi_imbalance/
    get_overview_data`, Binance + OKX + BingX only) — two different numbers; a threshold
    tuned on one does not carry over to the other.
    Built by a scheduled job, so this is not a per-second feed; a stale result is a 503,
    never a table of zeros. API key sees every row; anonymous callers get 30."""
    return _resolve('_raw_snapshot')('oi_imbalance/get_table', headers)


def fetch_open_interest_coin(symbol, headers):
    """一檔幣的未平倉量 — one coin's open interest per exchange (GET /oi_imbalance/get_coin;
    same basis as fetch_open_interest_table). Returns a dict:
      exchanges[]: the full roster — key, oi (USD), share, chg_1h / chg_4h / chg_24h /
        chg_7d, side_factor, listed (False = not listed there, values null), since /
        full_7d, last_at, stale
      oi_total, market_cap, oi_mcap, oi_mcap_rank / tokens_total (from the table job's last
        round; null when it has no fresh result)
      windows{'1h','4h','24h','7d'}: chg, chg_usd, and `exchanges` = which exchanges were
        counted in that window (only those with a baseline at its start — numerator and
        denominator over the same set, so 24h and 7d can count fewer exchanges than 1h)
      series: bucket_seconds=3600, days=7, timestamp[] (epoch seconds, 168 slots),
        total[] (only the `total_exchanges` — the feeds with a full 7 days — are in this
        line), total_exchanges[], price[] + price_symbol / price_multiplier,
        provisional_from
      symbol, token_id, updated_at
    `symbol` accepts BTC / BTCUSDT / btc. 404 → None; 503 propagates."""
    return _resolve('_raw_snapshot')('oi_imbalance/get_coin', headers,
                         {'symbol': symbol}, allow_404=True)


def fetch_liquidation_map(symbol, headers):
    """爆倉地圖 Liquidation map (GET /liquidation/get_map) — one coin, two layers over the same
    200 price buckets (labels, USDT), around the current `price`:
      actual     liquidation['24h']: buy_liq[] / sell_liq[] — Binance force-order liquidations
                 that HAPPENED in the last 24 h, bucketed by fill price, USD; each value is
                 divided by a fixed 0.3 Binance-share assumption to estimate the whole market.
                 buy_liq = short liquidations (above price), sell_liq = long liquidations.
      estimated  oi_value[] / cumsum[] — a MODEL ESTIMATE of where Binance open interest would
                 be liquidated (leaderboard positions + OI + volume), USD. Not real orders and
                 not actual events; any block built from it must say so.
    `symbol` accepts BTC / BTCUSDT / btc. Needs data access (API plan or data fee); no cache —
    the server snapshot is the state. 400 with "symbol is required" never happens from here."""
    sym = normalize_symbol(symbol if str(symbol).upper().endswith('USDT') else str(symbol).upper() + 'USDT')
    return _resolve('_raw_snapshot')('liquidation/get_map', headers, {'symbol': sym})


def fetch_cvd_table(headers):
    """主動買賣淨額總表 CVD (cumulative volume delta; GET /taker_intensity/get_cvd_table), every coin × 3 exchanges, USD.
    Basis: each exchange's own reported taker turnover, never volume × a borrowed price —
    Binance = the 5-minute kline's taker-buy quote volume (sell = the bar's total minus
    it), OKX = its USD taker volume, Gate = taker contracts × the same row's multiplier
    and mark price. Perpetuals only: **no spot, and not trade-by-trade**. Returns a dict:
      coins[]: token, token_id, buy_24h / sell_24h, net_1h / net_4h / net_24h (USD;
        net = buy − sell), by_exchange{name: {buy_24h, sell_24h, net_1h, net_4h, net_24h}}
      exchanges[]: binance / okx / gate — whole-market buy_24h / sell_24h / net_24h,
        last_at, stale. A feed more than an hour behind answers null windows and
        `stale: true` rather than a sum that quietly covers less than the window; the
        totals then add only the fresh exchanges
      total{buy_24h, sell_24h, net_24h}, tokens_shown / tokens_total, full, updated_at
    Windows are rolling, ending at the last closed bar. Built by a scheduled job; a stale
    result is a 503, never zeros. API key sees every row; anonymous callers get 30."""
    return _resolve('_raw_snapshot')('taker_intensity/get_cvd_table', headers)


def fetch_cvd_coin(symbol, headers):
    """一檔幣的主動買賣淨額 — one coin's CVD per exchange (GET /taker_intensity/get_cvd_coin;
    same basis as fetch_cvd_table). Returns a dict:
      windows{'1h','4h','24h','7d'}: buy / sell / net in USD, summed over the fresh
        exchanges
      exchanges[]: the full roster — key, listed, windows{...} per exchange, since /
        full_7d (False = fewer than 7 days of history here; its 7d window is null and it
        is left out of the 7d total and of the series), last_at, stale
      series: bucket_seconds=3600, days=7, timestamp[] (epoch seconds, 168 slots), net[]
        (clock-hour sums), cvd[] (running total, cvd[0] = 0), exchanges[] (who is in the
        line), price[] + price_symbol / price_multiplier, provisional_from
      symbol, token_id, updated_at
    `symbol` accepts BTC / BTCUSDT / btc. 404 → None; 503 propagates."""
    return _resolve('_raw_snapshot')('taker_intensity/get_cvd_coin', headers,
                         {'symbol': symbol}, allow_404=True)


def fetch_liquidation_exchanges(headers, hours=24, top_n=10):
    """爆倉矩陣 (GET /liquidation/get_exchanges) — forced liquidations aggregated across exchanges for the whole market,
    as the coin × exchange matrix behind the per-coin endpoint. USD notional is converted
    at collection time, so exchanges can be added up. Returns a dict:
      exchanges[]: binance / bybit / gate / okx / htx / bitfinex — total / long /
        short_liq_usd, long_pct / short_pct, events, last_event_at, and the two basis
        columns (price_basis, coverage, time_basis) that say how comparable a row is
      coins[]: the top `top_n` by cross-exchange total — token, token_id, total / long /
        short_liq_usd, by_exchange{name: {total/long/short_liq_usd}}
      others{total/long/short_liq_usd, by_exchange, coin_count}: everything the top_n cut
        off, so coins[] + others adds back up to each exchange's total
      total{total/long/short_liq_usd, long_pct, short_pct}, covered_hours, buckets,
        window_hours / window_start / window_end, updated_at
    `long_liq_usd` = long positions liquidated (price fell), `short_liq_usd` = shorts.
    The window is rolling, aligned to 5-minute buckets — the same frame as the per-coin
    endpoint's `windows`, so a coin's 24 h total matches on both.
    `hours` 1–168 and `top_n` 1–50; outside that the API answers 400 (it does not clamp).
    No local cache — the server caches 5 minutes."""
    return _resolve('_raw_snapshot')('liquidation/get_exchanges', headers,
                         {'hours': hours, 'top_n': top_n})


# ── Binance public endpoint (no key, any machine) ────────────────────────────
_BINANCE_TICKER_24H = 'https://fapi.binance.com/fapi/v1/ticker/24hr'



def fetch_binance_ticker_24h():
    """Binance USDT-M perpetuals, rolling 24 h (public, no key, any machine). DataFrame indexed
    by symbol (BTCUSDT): last, change_pct (percent, +3.2 = +3.2 %), quote_volume (USDT), volume
    (base asset, rolling 24 h — the same unit as a daily kline's Volume).
    Only symbols ending in USDT; one request."""
    # A report brick, not a backtest: two tries, then the caller drops the table (a blocked region must
    # not stall every brief for minutes of backoff).
    rows = _binance_get(_BINANCE_TICKER_24H, {}, max_retries=2, timeout=15, max_wait=5).json()
    out = [{'symbol': r['symbol'], 'last': float(r['lastPrice']), 'change_pct': float(r['priceChangePercent']),
            'quote_volume': float(r['quoteVolume']), 'volume': float(r.get('volume') or 'nan')}
           for r in rows if isinstance(r, dict) and str(r.get('symbol', '')).endswith('USDT')]
    return pd.DataFrame(out, columns=['symbol', 'last', 'change_pct', 'quote_volume', 'volume']).set_index('symbol')


def fetch_news(headers, q=None, since=None, limit=None):
    """鉅亨 B2B news candidates via Blave (licensed; needs Blave data access like the paid
    series — DataAccessError without it). DataFrame newest first: id, title, published_at
    (unix s), source, tags (list), stocks (list). Titles and times only, no article text and
    no link: the licence covers the headline. `q` matches title / tags (whole word for
    Latin: SOL does not match Solidigm), `since` unix seconds. A down upstream is a 503
    (requests.HTTPError after retries), never an empty frame."""
    # 官方 monolith 的 gate 在 _retry_get 內;BBAC-D 的 _retry_get 同樣如此。
    params = {}
    if q:
        params['q'] = q
    if since is not None:
        params['since'] = int(since)
    if limit is not None:
        params['limit'] = int(limit)
    # A report brick: give up in seconds (two tries, ~6 s of backoff) and let the brief go out
    # without the candidates, rather than hold every brief for two minutes while the source is down.
    r = _retry_get(f'{BASE}/studio/market/anue/news', headers=headers, params=params, timeout=10,
                   max_retries=2)
    data = r.json().get('data') or []
    return pd.DataFrame(data, columns=['id', 'title', 'published_at', 'source', 'tags', 'stocks'])
