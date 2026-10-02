"""lib.data — single data-access surface for the whole project.

原 lib/data.py(單檔 3250 行)拆成 package,按領域分檔:
  http      傳輸層(session pool / Blave key 輪詢 / retry / rate limit)
  cache     快取佈局(monthly partition + single-file + migration + TTL)
  batch     多 id 批量骨架(_fetch_batch_cached / _fetch_twstock_cached_batch)
  _shared   純讀期共享助手(_sanity_check_ohlc / _is_sub_5min)
  kline     加密 kline(Blave /kline + BingX)
  alpha     Blave alpha series + /studio/market/db 期貨 + 總經日曆
  twstock   台股(個股層級)
  twmarket  台股大盤(全市場層級)
  twfutures 台指期 / 個股期貨 + txf_settlement_mask

  feeds    非 price feed 的發布時間對齊(align_feed / FEED_TIMING)

這個 __init__ 把所有子模組的公開 API 重新匯出到 lib.data 命名空間,
讓 `from lib.data import fetch_kline` 等所有既有呼叫端零改動。
"""
# time / requests are re-exported as attributes on purpose: the fleet's checks patch
# lib.data.time / lib.data.requests, and they are shared module objects, so the patch
# reaches the submodules' own `import time` / `import requests` too.
import time                   # noqa: F401  (patch target, see the comment above)
import requests               # noqa: F401
# datetime is the same kind of patch target as time/requests: a check pins it to a frozen
# instant to exercise the cross-midnight Taipei bound, and the cache layer reads it per call.
from datetime import datetime, timedelta, timezone, date  # noqa: F401

from .http import (
    BASE, _CACHE_DIR,
    _thread_local, _session, _RateLimiter,
    _HEADERS_LOCK, _KEY_PAIRS, _KEY_INDEX, _load_key_pairs,
    _default_headers, _current_headers, _rotate_headers,
    _drop_bad_key, _retry_get, _desktop_denied, _get,
)

from .cache import (
    _monthly_cache_dir, _next_month, _iter_months, _contiguous_spans,
    _normalise_index, _month_end_utc, _written_before_month_end,
    _HEAD_VERIFIED_META, _HEAD_TOLERANCE, _head_short_unverified,
    _stale_incomplete_month, _atomic_to_parquet, _tmp_path,
    _extend_cache_monthly, _save_monthly,
    _SINGLE_FILE_PREFIXES, _META_TS_FMT, _META_KEY,
    _single_path, _write_single, _read_single_meta,
    _read_monthly_dir_all, _migrate_monthly_to_single,
    _read_single, _has_single_cache, _tail_needs_completion,
    _merge_frames, _extend_cache_single, _merge_meta, _merge_single,
    _save_single,
    _fundamental_cache_path, _load_fundamental_cache, _save_fundamental_cache,
)

from ._shared import _sanity_check_ohlc, _is_sub_5min

from .batch import _mark_empty_months, _fetch_batch_cached, _fetch_twstock_cached_batch

from .kline import (
    _fetch_kline_raw, normalize_symbol, drop_unsettled_bar, _BAR_SECONDS,
    closed_bars_only, _drop_forming_bar,
    fetch_kline, fetch_kline_batch,
    _BINGX_BASE, _BINGX_INTERVALS, _BINGX_PAGE, _EPOCH,
    _fetch_bingx_kline_raw, fetch_bingx_kline,
    _kline_source, _BINANCE_KLINES, _BINANCE_PAGE, _BINANCE_INTERVALS,
    _BINANCE_LIMITER, _BINANCE_INFLIGHT, _binance_get,
    _binance_klines_to_df, _fetch_binance_kline_raw, _binance_batch,
)

from .alpha import (
    _fetch_alpha_raw, _fetch_alpha,
    fetch_holder_concentration, fetch_funding_rate, fetch_taker_intensity,
    fetch_whale_hunter, fetch_unusual_movement, fetch_squeeze_momentum,
    fetch_liquidation, fetch_market_direction, fetch_capital_shortage,
    fetch_market_sentiment, fetch_top_trader_exposure,
    _DB_CHUNK_DAYS, _fetch_db_raw, settlement_signals_from_db, fetch_db_kline,
    fetch_economic_calendar,
    _FNG_URL, _FNG_START, _FNG_SOURCE, _fetch_fear_greed_raw, fetch_fear_greed,
    fetch_liquidation_coin,
)

from .twstock import *  # noqa: F401,F403
from .twmarket import *  # noqa: F401,F403
from .twfutures import *  # noqa: F401,F403

from .feeds import (
    FEED_TIMING, FeedNotPublished, live_feeds,
    feed_available_at, align_feed, TW_FLOWS, join_tw_flow,
)

from .alpha import UnknownFetcher, _ALPHA_FETCHERS


def __getattr__(name):
    if name.startswith('__') or not (name.startswith(('fetch_', 'get_'))
                                     or 'alpha' in name or 'indicator' in name):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import inspect
    sigs = '; '.join(f'{n}{inspect.signature(globals()[n])}' for n in _ALPHA_FETCHERS)
    raise UnknownFetcher(
        f"lib.data has no {name!r}. There is no generic alpha fetcher — each Blave alpha has "
        f"its own function (all return a DataFrame with an 'alpha' column; 'headers' is the "
        f"api-key/secret-key dict, see references/lib.md > 'Alpha fetchers - quick reference'): "
        f"{sigs}", name=__name__)

from ._shared import (
    DataAccessError, _NO_ACCESS_MSG, _check_data_access,
    _check_desktop_key, _daemon_on_desktop,
)

from .public_sources import (
    _TW_PUBLIC_SOURCE_ZH, _TW_PUBLIC_SOURCE_EN,
    _TW_PUBLIC_HEADERS, _TW_PUBLIC_LIMITER, _TWSE_LIMITER, _TW_PUBLIC_SESSION,
    TwPublicUnavailable,
    _tw_public_session, _tw_public_get, _tw_public_post,
    _roc_date, _tw_num, _roc_ymd, _tw_public_months,
    _TWSE_INDEX_HIST, _TWSE_FMTQIK, _TWSE_BFI82U, _TWSE_MI_MARGN,
    _TAIFEX_FUT_INST,
    _TWSE_SOURCE_ZH, _TWSE_SOURCE_EN,
    _TAIFEX_SOURCE_ZH, _TAIFEX_SOURCE_EN,
    _TWSE_OPENDATA_SOURCE_ZH, _TWSE_OPENDATA_SOURCE_EN,
    PUBLIC_SOURCE_EN,
    _TWSE_NO_DATA, _BFI82U_BUCKET,
    _TAIFEX_INST_COMMODITY, _TAIFEX_INVESTOR,
    tw_market_public_allowed, _tw_market_public_gate,
    _twse_json, _in_window, _twse_monthly_raw, _twse_daily_raw,
    _bfi82u_row, _mi_margn_row, _public_series, _taifex_inst_raw,
    fetch_twmarket_index_public, fetch_twmarket_turnover_public,
    fetch_twmarket_institutional_public, fetch_twmarket_margin_public,
    fetch_twfutures_institutional_public,
    _TWSE_OPENAPI, _twse_openapi,
    fetch_tw_announcements_public, fetch_twse_day_all_public,
    _HOLIDAY_MEMO, _HOLIDAY_MEMO_TTL, _TPE,
    _taipei_date, fetch_twstock_holidays, is_tw_trading_day,
    _twstock_daily_source, _fetch_twstock_daily_free, _twstock_daily,
    _TWSE_STOCK_DAY, _TWSE_STOCK_DAY_ALL, _TWSE_EXRIGHT,
    _TPEX_TRADING_STOCK, _TPEX_MAINBOARD, _TPEX_EXRIGHT, _FINMIND_DATA,
    _TWSE_STOCK_DAY_FROM, _TW_DAILY_COLS, _TW_EXRIGHT_COLS,
    _tw_daily_frame, _twse_stock_day, _tpex_trading_stock,
    _fetch_twstock_daily_public_raw, _tw_market_file,
    _twse_all_codes, _tpex_all_codes, _tw_public_market, _write_market_file,
    _tw_public_probe_market, _twse_exright_rows, _tpex_exright_rows,
    _tw_exright_events, _tw_exright_for, _tw_forward_adjust,
    _fetch_twstock_daily_public, _fetch_twstock_daily_finmind_raw,
)

from .snapshots import (
    _raw_snapshot,
    fetch_long_short_ratio_table, fetch_long_short_ratio_coin,
    fetch_open_interest_table, fetch_open_interest_coin,
    fetch_liquidation_map, fetch_liquidation_exchanges,
    fetch_cvd_table, fetch_cvd_coin,
    _BINANCE_TICKER_24H, fetch_binance_ticker_24h,
    fetch_news,
)

__all__ = [
    'BASE', '_CACHE_DIR', '_thread_local', '_session', '_RateLimiter',
    '_HEADERS_LOCK', '_KEY_PAIRS', '_KEY_INDEX', '_load_key_pairs',
    '_default_headers', '_current_headers', '_rotate_headers',
    '_drop_bad_key', '_retry_get', '_desktop_denied', '_get',
    '_monthly_cache_dir', '_next_month', '_iter_months', '_contiguous_spans',
    '_normalise_index', '_month_end_utc', '_written_before_month_end',
    '_HEAD_VERIFIED_META', '_HEAD_TOLERANCE', '_head_short_unverified',
    '_stale_incomplete_month', '_atomic_to_parquet', '_tmp_path',
    '_extend_cache_monthly', '_save_monthly',
    '_SINGLE_FILE_PREFIXES', '_META_TS_FMT', '_META_KEY',
    '_single_path', '_write_single', '_read_single_meta',
    '_read_monthly_dir_all', '_migrate_monthly_to_single',
    '_read_single', '_has_single_cache', '_tail_needs_completion',
    '_merge_frames', '_extend_cache_single', '_merge_meta', '_merge_single',
    '_save_single',
    '_fundamental_cache_path', '_load_fundamental_cache', '_save_fundamental_cache',
    '_sanity_check_ohlc', '_is_sub_5min',
    '_mark_empty_months', '_fetch_batch_cached', '_fetch_twstock_cached_batch',
    '_fetch_kline_raw', 'normalize_symbol', 'drop_unsettled_bar', '_BAR_SECONDS',
    'closed_bars_only', '_drop_forming_bar',
    'fetch_kline', 'fetch_kline_batch',
    '_BINGX_BASE', '_BINGX_INTERVALS', '_BINGX_PAGE', '_EPOCH',
    '_fetch_bingx_kline_raw', 'fetch_bingx_kline',
    '_kline_source', '_BINANCE_KLINES', '_BINANCE_PAGE', '_BINANCE_INTERVALS',
    '_BINANCE_LIMITER', '_BINANCE_INFLIGHT', '_binance_get',
    '_binance_klines_to_df', '_fetch_binance_kline_raw', '_binance_batch',
    '_fetch_alpha_raw', '_fetch_alpha',
    'fetch_holder_concentration', 'fetch_funding_rate', 'fetch_taker_intensity',
    'fetch_whale_hunter', 'fetch_unusual_movement', 'fetch_squeeze_momentum',
    'fetch_liquidation', 'fetch_market_direction', 'fetch_capital_shortage',
    'fetch_market_sentiment', 'fetch_top_trader_exposure',
    '_DB_CHUNK_DAYS', '_fetch_db_raw', 'settlement_signals_from_db', 'fetch_db_kline',
    'fetch_economic_calendar',
    '_FNG_URL', '_FNG_START', '_FNG_SOURCE', '_fetch_fear_greed_raw', 'fetch_fear_greed',
    'fetch_liquidation_coin',
    '_ALPHA_FETCHERS', 'UnknownFetcher',
    'FEED_TIMING', 'FeedNotPublished', 'live_feeds',
    'feed_available_at', 'align_feed', 'TW_FLOWS', 'join_tw_flow',
    '_fetch_twstock_price_raw', 'fetch_twstock_price_adj',
    '_fetch_twstock_price_nonadj_raw', 'fetch_twstock_price',
    'fetch_twstock_quote', 'fetch_twstock_quote_batch',
    '_TWSTOCK_MINUTE_CHUNK_DAYS', '_TWSTOCK_MINUTE_MAX_DAYS',
    '_fetch_twstock_minute_raw', 'fetch_twstock_ohlcv', 'fetch_twstock_ohlcv_symbols',
    '_fetch_twstock_inst_raw', 'fetch_twstock_institutional',
    '_fetch_twstock_shareholding_raw', 'fetch_twstock_shareholding',
    '_fetch_twstock_per_raw', 'fetch_twstock_per',
    '_DIVIDEND_COLUMNS', '_dividend_slice', 'fetch_twstock_dividend', 'fetch_twstock_dividend_batch',
    '_broker_day_cache_path', '_make_date_chunks',
    '_populate_broker_day_cache', '_populate_trader_day_cache',
    'fetch_twstock_broker_net', 'fetch_twstock_all_broker_net', 'fetch_twstock_branch_daily_net',
    '_fetch_twstock_fundamental_raw', '_fetch_fundamental',
    'fetch_twstock_financials', 'fetch_twstock_balance_sheet', 'fetch_twstock_monthly_revenue',
    '_twstock_list_cache_path', 'fetch_twstock_list', 'fetch_twstock_info', 'fetch_twstock_market_value_all',
    '_fetch_fundamental_batch',
    'fetch_twstock_financials_batch', 'fetch_twstock_balance_sheet_batch', 'fetch_twstock_monthly_revenue_batch',
    'fetch_twstock_shareholding_batch', 'fetch_twstock_price_adj_batch',
    'fetch_twstock_price_batch', 'fetch_twstock_per_batch', 'fetch_twstock_institutional_batch',
    '_fetch_twstock_foreign_shareholding_raw', 'fetch_twstock_foreign_shareholding_batch',
    '_trader_day_cache_path', 'fetch_twstock_trader_flows',
    '_fetch_twmarket_index_raw', 'fetch_twmarket_index',
    '_fetch_twmarket_raw', '_TWMARKET_TURNOVER_COLUMNS', '_TWMARKET_INST_COLUMNS',
    '_TWMARKET_MARGIN_COLUMNS',
    'fetch_twmarket_turnover', 'fetch_twmarket_institutional',
    'fetch_twmarket_margin', 'fetch_twmarket_dividend_points',
    '_TW_FUTURES_CHUNK_DAYS', '_fetch_twfutures_raw',
    '_ExportUnavailable', '_TW_FUTURES_RESAMPLE_RULES',
    '_fetch_twfutures_via_export', '_fetch_twfutures_raw_smart',
    'fetch_twfutures_ohlcv', 'fetch_twfutures_ohlcv_batch',
    '_fetch_twfutures_bid_ask_vol_raw', 'fetch_twfutures_pcr',
    '_TWFUT_INST_INVESTORS', '_TWFUT_INST_COLUMNS', '_TWFUT_INST_ALIASES',
    '_fetch_twfutures_institutional_raw', 'fetch_twfutures_institutional',
    'fetch_twfutures_bid_ask_vol',
    'fetch_stock_futures_batch_daily', 'fetch_stock_futures_ohlcv_symbols',
    'txf_settlement_mask',
    'DataAccessError', '_NO_ACCESS_MSG', '_check_data_access',
    '_check_desktop_key', '_daemon_on_desktop',
    '_TW_PUBLIC_SOURCE_ZH', '_TW_PUBLIC_SOURCE_EN',
    '_TW_PUBLIC_HEADERS', '_TW_PUBLIC_LIMITER', '_TWSE_LIMITER', '_TW_PUBLIC_SESSION',
    'TwPublicUnavailable',
    '_tw_public_session', '_tw_public_get', '_tw_public_post',
    '_roc_date', '_tw_num', '_roc_ymd', '_tw_public_months',
    '_TWSE_INDEX_HIST', '_TWSE_FMTQIK', '_TWSE_BFI82U', '_TWSE_MI_MARGN',
    '_TAIFEX_FUT_INST',
    '_TWSE_SOURCE_ZH', '_TWSE_SOURCE_EN',
    '_TAIFEX_SOURCE_ZH', '_TAIFEX_SOURCE_EN',
    '_TWSE_OPENDATA_SOURCE_ZH', '_TWSE_OPENDATA_SOURCE_EN',
    'PUBLIC_SOURCE_EN',
    '_TWSE_NO_DATA', '_BFI82U_BUCKET',
    '_TAIFEX_INST_COMMODITY', '_TAIFEX_INVESTOR',
    'tw_market_public_allowed', '_tw_market_public_gate',
    '_twse_json', '_in_window', '_twse_monthly_raw', '_twse_daily_raw',
    '_bfi82u_row', '_mi_margn_row', '_public_series', '_taifex_inst_raw',
    'fetch_twmarket_index_public', 'fetch_twmarket_turnover_public',
    'fetch_twmarket_institutional_public', 'fetch_twmarket_margin_public',
    'fetch_twfutures_institutional_public',
    '_TWSE_OPENAPI', '_twse_openapi',
    'fetch_tw_announcements_public', 'fetch_twse_day_all_public',
    '_HOLIDAY_MEMO', '_HOLIDAY_MEMO_TTL', '_TPE',
    '_taipei_date', 'fetch_twstock_holidays', 'is_tw_trading_day',
    '_twstock_daily_source', '_fetch_twstock_daily_free', '_twstock_daily',
    '_TWSE_STOCK_DAY', '_TWSE_STOCK_DAY_ALL', '_TWSE_EXRIGHT',
    '_TPEX_TRADING_STOCK', '_TPEX_MAINBOARD', '_TPEX_EXRIGHT', '_FINMIND_DATA',
    '_TWSE_STOCK_DAY_FROM', '_TW_DAILY_COLS', '_TW_EXRIGHT_COLS',
    '_tw_daily_frame', '_twse_stock_day', '_tpex_trading_stock',
    '_fetch_twstock_daily_public_raw', '_tw_market_file',
    '_twse_all_codes', '_tpex_all_codes', '_tw_public_market', '_write_market_file',
    '_tw_public_probe_market', '_twse_exright_rows', '_tpex_exright_rows',
    '_tw_exright_events', '_tw_exright_for', '_tw_forward_adjust',
    '_fetch_twstock_daily_public', '_fetch_twstock_daily_finmind_raw',
    '_raw_snapshot',
    'fetch_long_short_ratio_table', 'fetch_long_short_ratio_coin',
    'fetch_open_interest_table', 'fetch_open_interest_coin',
    'fetch_liquidation_map', 'fetch_liquidation_exchanges',
    'fetch_cvd_table', 'fetch_cvd_coin',
    '_BINANCE_TICKER_24H', 'fetch_binance_ticker_24h',
    'fetch_news',
]
