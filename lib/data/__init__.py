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

這個 __init__ 把所有子模組的公開 API 重新匯出到 lib.data 命名空間,
讓 `from lib.data import fetch_kline` 等所有既有呼叫端零改動。
"""
from .http import (
    BASE, _CACHE_DIR,
    _thread_local, _session, _RateLimiter,
    _HEADERS_LOCK, _KEY_PAIRS, _KEY_INDEX, _load_key_pairs,
    _GLOBAL_LIMITER, set_global_limiter, get_global_limiter,
    _default_headers, get_headers, get_all_headers,
    _KeyAwareRateLimiter, _current_headers, _rotate_headers,
    _drop_bad_key, _retry_get,
)
# Back-compat alias: callers re-export this as _KeyRateLimiter.
_KeyRateLimiter = _KeyAwareRateLimiter

from .cache import (
    _monthly_cache_dir, _next_month, _iter_months, _contiguous_spans,
    _normalise_index, _month_end_utc, _written_before_month_end,
    _HEAD_VERIFIED_META, _HEAD_TOLERANCE, _head_short_unverified,
    _stale_incomplete_month, _atomic_to_parquet,
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
    fetch_kline, fetch_kline_batch,
    _BINGX_BASE, _BINGX_INTERVALS, _BINGX_PAGE, _EPOCH,
    _fetch_bingx_kline_raw, fetch_bingx_kline,
)

from .alpha import (
    _fetch_alpha_raw, _fetch_alpha,
    fetch_holder_concentration, fetch_funding_rate, fetch_taker_intensity,
    fetch_whale_hunter, fetch_unusual_movement, fetch_squeeze_momentum,
    fetch_liquidation, fetch_market_direction, fetch_capital_shortage,
    fetch_market_sentiment, fetch_top_trader_exposure,
    _DB_CHUNK_DAYS, _fetch_db_raw, settlement_signals_from_db, fetch_db_kline,
    fetch_economic_calendar,
)

from .twstock import *  # noqa: F401,F403
from .twmarket import *  # noqa: F401,F403
from .twfutures import *  # noqa: F401,F403

__all__ = [
    'BASE', '_CACHE_DIR', '_thread_local', '_session', '_RateLimiter',
    '_HEADERS_LOCK', '_KEY_PAIRS', '_KEY_INDEX', '_load_key_pairs',
    '_GLOBAL_LIMITER', 'set_global_limiter', 'get_global_limiter',
    '_default_headers', 'get_headers', 'get_all_headers',
    '_KeyAwareRateLimiter', '_KeyRateLimiter', '_current_headers', '_rotate_headers',
    '_drop_bad_key', '_retry_get',
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
    '_sanity_check_ohlc', '_is_sub_5min',
    '_mark_empty_months', '_fetch_batch_cached', '_fetch_twstock_cached_batch',
    '_fetch_kline_raw', 'normalize_symbol', 'drop_unsettled_bar', '_BAR_SECONDS',
    'fetch_kline', 'fetch_kline_batch',
    '_BINGX_BASE', '_BINGX_INTERVALS', '_BINGX_PAGE', '_EPOCH',
    '_fetch_bingx_kline_raw', 'fetch_bingx_kline',
    '_fetch_alpha_raw', '_fetch_alpha',
    'fetch_holder_concentration', 'fetch_funding_rate', 'fetch_taker_intensity',
    'fetch_whale_hunter', 'fetch_unusual_movement', 'fetch_squeeze_momentum',
    'fetch_liquidation', 'fetch_market_direction', 'fetch_capital_shortage',
    'fetch_market_sentiment', 'fetch_top_trader_exposure',
    '_DB_CHUNK_DAYS', '_fetch_db_raw', 'settlement_signals_from_db', 'fetch_db_kline',
    'fetch_economic_calendar',
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
]
