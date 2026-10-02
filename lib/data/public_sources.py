"""Desktop-gated key-free Taiwan market sources (TWSE / TAIFEX open data).

拆自官方 lib/data.py 的公開資料段,只保留報表子系統(lib/report_bricks.py /
lib/report_templates.py)需要的市場層級:大盤指數 / 成交值 / 三大法人 / 融資餘額、
期貨法人、休市表與交易日判斷、上市公司重大訊息、全市場收盤。

個股層級的 public 日線回退(官方 _twstock_daily / _tw_public_market 那一支)不在
這裡:BBA C-D 的 twstock.py 走 Blave 端,報表不需要個股 public 路徑。

只在桌面版上允許(BLAVE_AGENT_LOCAL=1,tw_market_public_allowed);雲端機器
永遠不直連 twse.com.tw / taifex.com.tw。沒有權限時這些函式直接 raise
TwPublicUnavailable —— 那是告訴呼叫端「免費路徑不允許」,不是錯誤。

匯入的共享元件:
  BASE / _retry_get / _RateLimiter   (.http — 官方 _retry_get 的 BLAVE_DATA_ACCESS
      gate 在 BBAC-D 的金鑰輪詢版裡不存在,所以 fetch_twstock_holidays 自己補上)
  _extend_cache_monthly / _iter_months (.cache — 與 Blave 路徑同一份 monthly 布局)
  _sanity_check_ohlc / _check_data_access (._shared)
  _TWMARKET_*_COLUMNS (.twmarket) / _TWFUT_INST_* (.twfutures — 與官方逐字相同,
      故直接共用,不重抄)
"""
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, date as _date

import pandas as pd
import requests

from .http import BASE, _RateLimiter
from ._shared import _sanity_check_ohlc, _check_data_access, _TPE, DataAccessError, _call_through
_retry_get = lambda *a, **k: _call_through('_retry_get', *a, **k)
from .cache import (_extend_cache_monthly, _iter_months, _atomic_to_parquet,
                    _written_before_month_end, _META_TS_FMT, _tmp_path)

# lib.data.<name> is the patch surface the official checks use; _CACHE_DIR / _TW_PUBLIC_*
# are this module's own globals, so a patch on the package would not reach them. Read
# them per call via _pkg() (nothing changes unless a check redirects them).


def _cache_dir():
    return getattr(_pkg(), '_CACHE_DIR')


def _dt():
    """datetime as resolved per call. The checks pin lib.data.datetime to a frozen instant (the
    Taipei year flips at UTC 16:00, the future-window bound is cross-midnight) and this module
    decides what to fetch from it — this module's own import would not see that clock."""
    return getattr(_pkg(), 'datetime', datetime)


def _pub_session():
    return getattr(_pkg(), '_TW_PUBLIC_SESSION')


def _pub_limiter(url):
    return getattr(_pkg(), '_TWSE_LIMITER') if '.twse.com.tw/' in url else getattr(_pkg(), '_TW_PUBLIC_LIMITER')
from .twmarket import (_TWMARKET_TURNOVER_COLUMNS, _TWMARKET_INST_COLUMNS,
                       _TWMARKET_MARGIN_COLUMNS)
from .twfutures import _TWFUT_INST_COLUMNS, _TWFUT_INST_ALIASES

__all__ = [
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
    '_TWSE_STOCK_DAY', '_TWSE_STOCK_DAY_ALL', '_TWSE_EXRIGHT',
    '_TPEX_TRADING_STOCK', '_TPEX_MAINBOARD', '_TPEX_EXRIGHT',
    '_FINMIND_DATA', '_TWSE_STOCK_DAY_FROM', '_TW_DAILY_COLS', '_TW_EXRIGHT_COLS',
    '_twstock_daily_source',
    '_tw_daily_frame', '_twse_stock_day', '_tpex_trading_stock',
    '_fetch_twstock_daily_public_raw', '_tw_market_file',
    '_twse_all_codes', '_tpex_all_codes', '_tw_public_market', '_write_market_file',
    '_tw_public_probe_market', '_twse_exright_rows', '_tpex_exright_rows',
    '_tw_exright_events', '_tw_exright_for', '_tw_forward_adjust',
    '_fetch_twstock_daily_public', '_fetch_twstock_daily_finmind_raw',
    '_fetch_twstock_daily_free', '_twstock_daily',
    '_twse_json', '_in_window', '_twse_monthly_raw', '_twse_daily_raw',
    '_bfi82u_row', '_mi_margn_row', '_public_series', '_taifex_inst_raw',
    'fetch_twmarket_index_public', 'fetch_twmarket_turnover_public',
    'fetch_twmarket_institutional_public', 'fetch_twmarket_margin_public',
    'fetch_twfutures_institutional_public',
    '_TWSE_OPENAPI', '_twse_openapi',
    'fetch_tw_announcements_public', 'fetch_twse_day_all_public',
    '_HOLIDAY_MEMO', '_HOLIDAY_MEMO_TTL', '_TPE',
    '_taipei_date', 'fetch_twstock_holidays', 'is_tw_trading_day',
]


# ── Public TW sources: shared transport ─────────────────────────────────────
_TW_PUBLIC_SOURCE_ZH = '資料來源:臺灣證券交易所、證券櫃檯買賣中心(政府資料開放授權)'
_TW_PUBLIC_SOURCE_EN = 'Source: Taiwan Stock Exchange, Taipei Exchange (Open Government Data License)'
_TW_PUBLIC_HEADERS   = {'User-Agent': 'Mozilla/5.0 (compatible; blave-agent; +https://blave.org)'}
_TW_PUBLIC_LIMITER   = _RateLimiter(1, 1.0)
# twse.com.tw on its own, slower bucket: it blocks an IP at roughly one request a second (no
# published number), and the IP it blocks is the user's home connection.
_TWSE_LIMITER        = _RateLimiter(1, 3.0)
_TW_PUBLIC_SESSION   = None


class TwPublicUnavailable(RuntimeError):
    """The key-free path could not serve this request (site down, layout changed, blocked,
    or the id is on neither exchange) — the caller moves on to the next source."""


def _tw_public_session():
    s = _pub_session()
    if s is None:
        s = requests.Session()
        # cached the same way the official monolith does it (a module-global write)
        setattr(_pkg(), '_TW_PUBLIC_SESSION', s)
    return s


def _tw_public_get(url, params, tries=3):
    """One throttled GET at an exchange site or FinMind. Timeouts, connection errors, 429
    and 5xx are retried twice with a short backoff; anything else raises — there is no
    per-user quota worth waiting on, and the caller has further sources to try."""
    limiter = _pub_limiter(url)
    for attempt in range(tries):
        limiter.acquire()
        try:
            r = _tw_public_session().get(url, params=params, headers=_TW_PUBLIC_HEADERS, timeout=30)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and attempt < tries - 1:
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r


def _roc_date(s):
    """民國 date '113/01/02' or '112年03月16日' → Timestamp 2024-01-02 / 2023-03-16."""
    parts = [p for p in s.replace('年', '/').replace('月', '/').replace('日', '').split('/') if p.strip()]
    y, m, d = (int(p) for p in parts[:3])
    return pd.Timestamp(year=y + 1911, month=m, day=d)


def _tw_num(s):
    """'27,997,826' → 27997826.0, '+3.00' → 3.0; '--' (no trade), blank or 全形空白 → NaN."""
    try:
        return float(str(s).replace(',', '').replace('　', '').strip())
    except ValueError:
        return float('nan')


def _tw_public_months(start, end):
    """The 'YYYY-MM' months whose first day is in [start, end) — `end` exclusive, as
    _extend_cache_monthly passes it — and not past the current Taipei month."""
    datetime = _dt()
    last = datetime.now(_TPE).strftime('%Y-%m')
    return [ym for ym in _iter_months(start, end) if f'{ym}-01' < end and ym <= last]


# ── Taiwan market calendar ────────────────────────────────────────────────────

_HOLIDAY_MEMO = {}
_HOLIDAY_MEMO_TTL = 3600


def _taipei_date(value):
    """'YYYY-MM-DD' (zero padding optional: '2026-9-1' is fine), date, datetime or
    Timestamp → the Taipei calendar date. A tz-aware value is converted to Taipei first;
    a naive one is taken as Taipei already. Anything else raises instead of being guessed."""
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f"date must be 'YYYY-MM-DD' (Taipei), got {value!r}") from None
    if isinstance(value, datetime):   # pd.Timestamp included
        return (value.astimezone(_TPE) if value.tzinfo else value).date()
    if isinstance(value, _date):
        return value
    raise TypeError(f"date must be 'YYYY-MM-DD', a date or a datetime, got {type(value).__name__}")


def fetch_twstock_holidays(headers, year=None):
    """TWSE 年度休市表 (annual market holiday schedule) for `year` (default: this Taipei year).

    DataFrame, one row per listed day: date (Timestamp), name, type, note. type is
    'holiday' or 'settlement_only' (市場無交易,僅辦理結算交割 — nothing trades that day either);
    the table's 「…開始/最後交易日」 marker rows are already removed server-side. Weekend
    dates may appear. **Ad-hoc closures (typhoon days) are never in it.**

    df.attrs: year, source / source_zh (the licence attribution — copy one verbatim into
    any report or reply that cites the table), note, stale (True = TWSE was unreachable
    and this is the last stored copy).

    Returns None — and prints why — when the endpoint is unreachable or TWSE has not
    published that year (published:false). None means "unknown", never "no holidays"."""
    datetime = _dt()
    if year is None:
        year = datetime.now(_TPE).year
    year = int(year)
    hit = _HOLIDAY_MEMO.get(year)
    if hit and time.time() - hit[0] < _HOLIDAY_MEMO_TTL:
        return hit[1]
    # 官方 monolith 的 gate 在 _retry_get 內(URL 以 BASE 開頭才檢查),memo 命中時
    # 不會走到 _retry_get 故不被 gate;BBAC-D 的 _retry_get 是金鑰輪詢版、不做這個
    # 檢查,所以在這裡直接補上同一個 gate —— 位置與官方相同:memo 之後、請求之前。
    _check_data_access(headers or {})
    try:
        # 5xx 預設退避約兩分鐘;休市表只是判斷用,拿不到就回 None,不值得讓報告卡那麼久。
        r = _retry_get(f'{BASE}/studio/market/twmarket/holidays', headers=headers,
                       params={'year': year}, timeout=20, max_retries=2)
        payload = r.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  [holidays] {year}: holiday table unavailable ({type(e).__name__}) — trading-day status unknown")
        return None
    if not isinstance(payload, dict) or not payload.get('published') or not payload.get('data'):
        reason = payload.get('reason') if isinstance(payload, dict) else None
        what = ("the source no longer serves this year's table" if reason == 'not_available_from_source'
                else "TWSE has not published this year's table")
        print(f"  [holidays] {year}: {what} ({reason or 'published:false'}) "
              f"— trading-day status unknown, not 'no holidays'")
        return None
    df = pd.DataFrame(payload['data'])
    for col in ('name', 'type', 'note'):
        if col not in df.columns:
            df[col] = None
    df['date'] = pd.to_datetime(df['date'])
    df = df[['date', 'name', 'type', 'note']].sort_values('date').reset_index(drop=True)
    df.attrs = {k: payload.get(k) for k in ('year', 'source', 'source_zh', 'note', 'stale')}
    if payload.get('stale'):
        print(f"  [holidays] {year}: WARNING TWSE unreachable, this is the last stored copy of the table")
    _HOLIDAY_MEMO[year] = (time.time(), df)
    return df


def _pkg():
    """The lib.data package namespace, looked up at call time.

    In the official monolith is_tw_trading_day calls fetch_twstock_holidays by its bare
    name inside lib/data.py — the public export and the module global are one binding, so
    replacing lib.data.fetch_twstock_holidays (tests do) is seen by internal callers.
    Splitting into a package broke that: a bare name here binds only this submodule.
    Public fetchers called from other public functions therefore resolve through the
    package's public name, so patching the public API reaches the internal callers —
    same property as the monolith. Runtime behaviour is unchanged (both names start out
    bound to the same object; the extra lookup only matters after a rebinding)."""
    return sys.modules[__package__]


def is_tw_trading_day(date, headers):
    """Is `date` a TWSE trading day? 'YYYY-MM-DD', date, datetime or Timestamp; a tz-aware
    value is converted to Taipei first, a naive one is taken as a Taipei date. True / False,
    or None when unknown (holiday table unavailable or that year not published).
    Saturday/Sunday → False without any fetch; a 'holiday' or 'settlement_only' row → False.
    True only means "not in the official table": a typhoon closure is not in it."""
    d = _taipei_date(date)
    if d.weekday() >= 5:
        return False
    table = _pkg().fetch_twstock_holidays(headers, d.year)
    if table is None:
        return None
    closed = table[table['type'].isin(['holiday', 'settlement_only'])]['date'].dt.date
    return d not in set(closed)


# ── Key-free twins of the Blave market series (desktop only) ─────────────────
# Blave series. Runs only on the user's own computer (BLAVE_AGENT_LOCAL=1, the flag
# tw_market_public_allowed reads): a cloud machine never calls twse.com.tw / taifex.com.tw.
_TWSE_INDEX_HIST = 'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST'
_TWSE_FMTQIK     = 'https://www.twse.com.tw/exchangeReport/FMTQIK'
_TWSE_BFI82U     = 'https://www.twse.com.tw/fund/BFI82U'
_TWSE_MI_MARGN   = 'https://www.twse.com.tw/exchangeReport/MI_MARGN'
_TAIFEX_FUT_INST = 'https://www.taifex.com.tw/cht/3/futContractsDateDown'
# Not "開放授權": BFI82U (三大法人) is not in the TWSE open-data set, so the line names the site
# the four series are read from and claims no licence.
_TWSE_SOURCE_ZH   = '資料來源:臺灣證券交易所網站'
_TWSE_SOURCE_EN   = 'Source: Taiwan Stock Exchange website'
_TAIFEX_SOURCE_ZH = '資料來源:臺灣期貨交易所(政府資料開放授權)'
_TAIFEX_SOURCE_EN = 'Source: Taiwan Futures Exchange (Open Government Data License)'
# zh attribution line → its en twin, for a report published with lang="en".
_TWSE_OPENDATA_SOURCE_ZH = '資料來源:臺灣證券交易所(政府資料開放授權)'
_TWSE_OPENDATA_SOURCE_EN = 'Source: Taiwan Stock Exchange (Open Government Data License)'
PUBLIC_SOURCE_EN = {_TW_PUBLIC_SOURCE_ZH: _TW_PUBLIC_SOURCE_EN, _TWSE_SOURCE_ZH: _TWSE_SOURCE_EN,
                    _TWSE_OPENDATA_SOURCE_ZH: _TWSE_OPENDATA_SOURCE_EN, _TAIFEX_SOURCE_ZH: _TAIFEX_SOURCE_EN}
# TWSE answers 200 + stat for everything: these mean "no rows for that date", anything
# else non-OK (throttle, layout change) raises and is never cached as an empty day.
_TWSE_NO_DATA = ('很抱歉', '沒有符合條件', '查詢日期大於', '查詢日期小於')
_BFI82U_BUCKET = {'外資及陸資(不含外資自營商)': 'foreign', '外資自營商': 'dealer', '投信': 'investment_trust',
                  '自營商(自行買賣)': 'dealer', '自營商(避險)': 'dealer', '合計': 'total'}
_TAIFEX_INST_COMMODITY = {'TX': 'TXF', 'MTX': 'MXF', 'TMF': 'TMF'}
_TAIFEX_INVESTOR = {'外資及陸資': 'foreign', '外資': 'foreign', '投信': 'investment_trust', '自營商': 'dealer'}


def tw_market_public_allowed():
    """True only on the desktop build (BLAVE_AGENT_LOCAL=1) — canon: key-free sources are
    fetched on the user's own computer, never from a Blave-hosted machine."""
    return os.environ.get('BLAVE_AGENT_LOCAL') == '1'


def _tw_market_public_gate():
    if not tw_market_public_allowed():
        raise TwPublicUnavailable('key-free market data runs only on the desktop build (BLAVE_AGENT_LOCAL=1)')


def _twse_json(url, params, label):
    """TWSE JSON with stat OK → payload; a no-data stat → None; anything else raises."""
    j = _tw_public_get(url, dict(params, response='json')).json()
    stat = str(j.get('stat', ''))
    if stat == 'OK':
        return j
    if any(m in stat for m in _TWSE_NO_DATA):
        return None
    raise TwPublicUnavailable(f'TWSE {label}: {stat[:60]}')


def _in_window(df, s, e):
    return df[(df.index >= pd.Timestamp(s)) & (df.index < pd.Timestamp(e))] if len(df) else df


def _twse_monthly_raw(url, label, cols, parse, s, e):
    rows = []
    for ym in _tw_public_months(s, e):
        j = _twse_json(url, {'date': f'{ym[:4]}{ym[5:7]}01'}, f'{label} {ym}')
        for x in (j or {}).get('data') or []:
            d = _roc_date(x[0])
            if d.strftime('%Y-%m') == ym:   # TWSE sometimes pads a month with a neighbour's rows
                rows.append((d, *parse(x)))
    df = pd.DataFrame(rows, columns=['date'] + cols).set_index('date').sort_index()
    return _in_window(df.astype(float), s, e)


def _twse_daily_raw(url, label, params, cols, parse, s, e):
    """One request per TWSE trading day in [s, e) — the days come from the public index
    series, so holidays cost nothing. A no-data answer for an older trading day raises (it
    would otherwise be cached as a hole for good); for today it means not published yet, and
    so it does for yesterday within this month (MI_MARGN comes out in the evening and runs
    past midnight on heavy days — the frame then ends a day earlier instead of failing)."""
    datetime = _dt()
    now = datetime.now(_TPE)
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    days = fetch_twmarket_index_public(s, (pd.Timestamp(e) - timedelta(days=1)).strftime('%Y-%m-%d')).index
    rows = []
    for d in days:
        day = d.strftime('%Y-%m-%d')
        j = _twse_json(url, params(day.replace('-', '')), f'{label} {day}')
        if j is None:
            if day < today and not (day >= yesterday and day[:7] == today[:7]):
                raise TwPublicUnavailable(f'TWSE {label} {day}: no data for a trading day')
            continue
        rows.append((d, *parse(j)))
    return pd.DataFrame(rows, columns=['date'] + cols).set_index('date').sort_index().astype(float)


def _bfi82u_row(j):
    net = {}
    for x in j.get('data') or []:
        bucket = _BFI82U_BUCKET.get(str(x[0]).strip())
        if bucket:
            net[bucket] = net.get(bucket, 0.0) + _tw_num(x[3])
    if 'total' not in net:
        raise TwPublicUnavailable('TWSE BFI82U: no 合計 row')
    return tuple(net.get(c, float('nan')) for c in _TWMARKET_INST_COLUMNS)


def _mi_margn_row(j):
    # 信用交易統計: 項目, 買進, 賣出, 現金(券)償還, 前日餘額, 今日餘額 — 交易單位 = 張, 金額 仟元
    tables = [t for t in j.get('tables') or [] if '信用交易統計' in str(t.get('title', ''))]
    rows = {str(x[0]).strip(): x for x in (tables[0].get('data') if tables else [])}
    try:
        m, s, v = rows['融資(交易單位)'], rows['融券(交易單位)'], rows['融資金額(仟元)']
    except KeyError:
        raise TwPublicUnavailable('TWSE MI_MARGN: 信用交易統計 layout changed') from None
    return _tw_num(m[5]), _tw_num(m[4]), _tw_num(v[5]) * 1000, _tw_num(s[5]), _tw_num(s[4])


def _public_series(kind, raw, start, end, source):
    _tw_market_public_gate()
    df = _extend_cache_monthly('twmarket_public', {'kind': kind}, raw, start, end)
    df.attrs['source'] = source
    return df


def fetch_twmarket_index_public(start, end):
    """fetch_twmarket_index('TAIEX') from TWSE MI_5MINS_HIST, one month per request.
    Desktop only (tw_market_public_allowed); attrs['source'] = 'TWSE'."""
    raw = lambda s, e: _twse_monthly_raw(_TWSE_INDEX_HIST, 'MI_5MINS_HIST', ['Open', 'High', 'Low', 'Close'],
                                         lambda x: tuple(_tw_num(v) for v in x[1:5]), s, e)
    return _sanity_check_ohlc(_public_series('index', raw, start, end, 'TWSE'), 'TAIEX twse index')


def fetch_twmarket_turnover_public(start, end):
    """fetch_twmarket_turnover from TWSE FMTQIK (成交股數 / 成交金額 元 / 成交筆數)."""
    raw = lambda s, e: _twse_monthly_raw(_TWSE_FMTQIK, 'FMTQIK', _TWMARKET_TURNOVER_COLUMNS,
                                         lambda x: tuple(_tw_num(v) for v in x[1:4]), s, e)
    return _public_series('turnover', raw, start, end, 'TWSE')


def fetch_twmarket_institutional_public(start, end):
    """fetch_twmarket_institutional from TWSE BFI82U, one trading day per request (net 元;
    外資自營商 counted in dealer, as the Blave series)."""
    raw = lambda s, e: _twse_daily_raw(_TWSE_BFI82U, 'BFI82U', lambda d: {'type': 'day', 'dayDate': d},
                                       _TWMARKET_INST_COLUMNS, _bfi82u_row, s, e)
    return _public_series('institutional', raw, start, end, 'TWSE')


def fetch_twmarket_margin_public(start, end):
    """fetch_twmarket_margin from TWSE MI_MARGN 信用交易統計, one trading day per request
    (balances in 張, margin_balance_value 元 = 融資金額仟元 × 1,000)."""
    raw = lambda s, e: _twse_daily_raw(_TWSE_MI_MARGN, 'MI_MARGN', lambda d: {'date': d, 'selectType': 'MS'},
                                       _TWMARKET_MARGIN_COLUMNS, _mi_margn_row, s, e)
    return _public_series('margin', raw, start, end, 'TWSE')


def _tw_public_post(url, data, tries=3):
    for attempt in range(tries):
        _pub_limiter(url).acquire()
        try:
            r = _tw_public_session().post(url, data=data, headers=_TW_PUBLIC_HEADERS, timeout=30)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and attempt < tries - 1:
            time.sleep(2 ** (attempt + 1))
            continue
        r.raise_for_status()
        return r


def _taifex_inst_raw(commodity, s, e):
    """TAIFEX 三大法人-區分各期貨契約 CSV (cp950) for [s, e). TAIFEX answers an HTML page when
    queryEndDate is past its last published day, so near today the end steps back a day at a
    time (12 days covers the Lunar New Year closure); an HTML answer for a window that ended
    longer ago than that is an error, not 'no data'."""
    datetime = _dt()
    today = datetime.now(_TPE).date()
    first = pd.Timestamp(s).date()
    last = min(pd.Timestamp(e).date() - timedelta(days=1), today)
    while last >= first:
        r = _tw_public_post(_TAIFEX_FUT_INST, {'commodityId': commodity,
                                               'queryStartDate': first.strftime('%Y/%m/%d'),
                                               'queryEndDate': last.strftime('%Y/%m/%d')})
        lines = [ln for ln in r.content.decode('cp950', errors='replace').splitlines() if ln.strip()]
        if lines and '身份別' in lines[0]:
            break
        if (today - last).days >= 12:
            raise TwPublicUnavailable(f'TAIFEX futContractsDateDown {commodity} {first}–{last}: not a CSV answer')
        last -= timedelta(days=1)
    else:
        return pd.DataFrame(columns=_TWFUT_INST_COLUMNS)
    rows = list(csv.reader(lines))
    col = {name.strip(): i for i, name in enumerate(rows[0])}
    need = ('日期', '身份別', '多方交易口數', '空方交易口數', '多方未平倉口數', '空方未平倉口數')
    if any(n not in col for n in need):
        raise TwPublicUnavailable(f'TAIFEX futContractsDateDown: unexpected header {sorted(col)[:6]}')
    out = {}
    for x in rows[1:]:
        who = _TAIFEX_INVESTOR.get(x[col['身份別']].strip())
        if who is None:
            continue
        rec = out.setdefault(pd.Timestamp(x[col['日期']].strip().replace('/', '-')), {})
        lo, so = _tw_num(x[col['多方未平倉口數']]), _tw_num(x[col['空方未平倉口數']])
        rec[f'{who}_net_oi'], rec[f'{who}_long_oi'], rec[f'{who}_short_oi'] = lo - so, lo, so
        rec[f'{who}_net_deal'] = _tw_num(x[col['多方交易口數']]) - _tw_num(x[col['空方交易口數']])
    df = pd.DataFrame.from_dict(out, orient='index').reindex(columns=_TWFUT_INST_COLUMNS).sort_index()
    df.index.name = 'date'
    return df.astype(float)


def fetch_twfutures_institutional_public(futures_id, start, end):
    """fetch_twfutures_institutional from TAIFEX futContractsDateDown (same 12 columns, 口數).
    'TX'/'TXF', 'MTX'/'MXF', 'TMF' only; attrs['source'] = 'TAIFEX'."""
    fid = _TWFUT_INST_ALIASES.get(futures_id.upper(), futures_id.upper())
    commodity = _TAIFEX_INST_COMMODITY.get(fid)
    if commodity is None:
        raise TwPublicUnavailable(f'TAIFEX institutional: {futures_id} not supported on the key-free path')
    return _public_series(f'futinst_{fid}', lambda s, e: _taifex_inst_raw(commodity, s, e), start, end, 'TAIFEX')


# ── TWSE open data (openapi.twse.com.tw) ─────────────────────────────────────

_TWSE_OPENAPI = 'https://openapi.twse.com.tw/v1'


def _twse_openapi(path):
    """One TWSE open-data JSON list (openapi.twse.com.tw). Keys are stripped: the feeds carry
    stray spaces in field names (t187ap04_L's 「主旨 」)."""
    _tw_market_public_gate()
    rows = _tw_public_get(f'{_TWSE_OPENAPI}/{path}', {}).json()
    if not isinstance(rows, list):
        raise TwPublicUnavailable(f'TWSE openapi {path}: not a list')
    return [{str(k).strip(): v for k, v in r.items()} for r in rows if isinstance(r, dict)]


def fetch_tw_announcements_public():
    """上市公司重大訊息 (TWSE open data t187ap04_L) — the latest publication day only, straight
    from TWSE, desktop only (BLAVE_AGENT_LOCAL=1; TwPublicUnavailable elsewhere). DataFrame,
    newest first: time (Taipei, tz-aware), stock_id, name, subject, clause (「第51款」),
    fact_date ('YYYY-MM-DD' or None). The long 說明 text is left out.
    attrs['source'] is the attribution line to keep with anything that shows it."""
    cols = ['time', 'stock_id', 'name', 'subject', 'clause', 'fact_date']
    out = []
    for r in _twse_openapi('opendata/t187ap04_L'):
        try:
            day = _roc_ymd(r.get('發言日期'))
            hms = str(r.get('發言時間') or '0').strip().zfill(6)
            t = day + pd.Timedelta(hours=int(hms[:2]), minutes=int(hms[2:4]), seconds=int(hms[4:6]))
        except (TypeError, ValueError):
            continue
        subject = ' '.join(str(r.get('主旨') or '').split())
        if not subject:
            continue
        try:
            fact = _roc_ymd(r.get('事實發生日')).strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            fact = None
        out.append({'time': t.tz_localize('Asia/Taipei'), 'stock_id': str(r.get('公司代號') or '').strip(),
                    'name': str(r.get('公司名稱') or '').strip(), 'subject': subject,
                    'clause': str(r.get('符合條款') or '').strip(), 'fact_date': fact})
    df = pd.DataFrame(out, columns=cols).sort_values('time', ascending=False).reset_index(drop=True)
    df.attrs['source'] = _TWSE_OPENDATA_SOURCE_ZH
    return df


def fetch_twse_day_all_public():
    """Every TWSE-listed security's last trading day (TWSE open data STOCK_DAY_ALL), desktop
    only. DataFrame indexed by stock_id: name, value (成交金額, NTD), volume (股), close, change
    (points), trades; attrs['date'] ('YYYY-MM-DD'), attrs['source'] (attribution). ETFs and
    other listed securities are in it — the feed has no type column."""
    rows, day = [], None
    for r in _twse_openapi('exchangeReport/STOCK_DAY_ALL'):
        code = str(r.get('Code') or '').strip()
        if not code:
            continue
        day = day or r.get('Date')
        rows.append({'stock_id': code, 'name': str(r.get('Name') or '').strip(),
                     'value': _tw_num(r.get('TradeValue')), 'volume': _tw_num(r.get('TradeVolume')),
                     'close': _tw_num(r.get('ClosingPrice')), 'change': _tw_num(r.get('Change')),
                     'trades': _tw_num(r.get('Transaction'))})
    df = pd.DataFrame(rows, columns=['stock_id', 'name', 'value', 'volume', 'close', 'change', 'trades'])
    df = df.set_index('stock_id')
    df.attrs['date'] = _roc_ymd(day).strftime('%Y-%m-%d') if day else None
    df.attrs['source'] = _TWSE_OPENDATA_SOURCE_ZH
    return df


def _roc_ymd(s):
    """民國 'YYYMMDD' ('1150925') → Timestamp 2026-09-25."""
    s = str(s).strip()
    if not s.isdigit() or len(s) not in (6, 7):
        raise ValueError(f'not a ROC yyyMMdd date: {s!r}')
    return pd.Timestamp(year=int(s[:-4]) + 1911, month=int(s[-4:-2]), day=int(s[-2:]))



# ── 個股層級的 public 日線回退(exchange → FinMind free → Blave) ───────────────

# 逐字抄自官方 lib/data.py。桌面版(BLAVE_AGENT_LOCAL=1)上,Blave 帳號沒有餘額時,

# fetch_twstock_price / fetch_twstock_price_adj 仍能從 TWSE/TPEx 或 FinMind 免費

# 額拿到日線;雲端機器永遠直連 Blave,不給 twse.com.tw / tpex.org.tw 增加流量。

# _twstock_daily_source() 是分派器,與 tw_market_public_allowed() 的判斷同源。



_TWSE_STOCK_DAY      = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY'
_TWSE_STOCK_DAY_ALL  = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL'
_TWSE_EXRIGHT        = 'https://www.twse.com.tw/rwd/zh/exRight/TWT49U'
_TPEX_TRADING_STOCK  = 'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock'
_TPEX_MAINBOARD      = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes'
_TPEX_EXRIGHT        = 'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ'
_FINMIND_DATA        = 'https://api.finmindtrade.com/api/v4/data'
_TWSE_STOCK_DAY_FROM = '2010-01'      # STOCK_DAY: 「查詢日期小於99年1月4日」 before this



_TW_DAILY_COLS   = ['Open', 'High', 'Low', 'Close', 'Volume']

_TW_EXRIGHT_COLS = ['stock_id', 'prev_close', 'ref_price']



def _twstock_daily_source():
    """'public' (exchange → FinMind free → Blave) or 'blave' (the Blave endpoint only).

    The free sources run only on the user's own computer: the desktop build marks itself
    with BLAVE_AGENT_LOCAL=1 (shell/daemon.js, runtime/agent_turn.py — the same flag
    lib/venue.py reads), and there the chain is the default; a cloud fleet machine (flag
    absent) stays on Blave exactly as before, so no Blave server ever hits twse.com.tw,
    tpex.org.tw or FinMind. BLAVE_TWSTOCK_DAILY_SOURCE=public|blave overrides either way."""
    forced = os.environ.get('BLAVE_TWSTOCK_DAILY_SOURCE', '').strip().lower()
    if forced in ('public', 'blave'):
        return forced
    return 'public' if os.environ.get('BLAVE_AGENT_LOCAL') == '1' else 'blave'


def _tw_daily_frame(rows):
    """rows (date, open, high, low, close, shares) → the fetch_twstock_price frame: naive
    Taipei dates, floats, zero/blank prices forward-filled exactly as the Blave path does."""
    if not rows:
        return pd.DataFrame(columns=_TW_DAILY_COLS)
    df = pd.DataFrame(rows, columns=['date'] + _TW_DAILY_COLS).set_index('date').sort_index()
    return df.astype(float).replace(0, float('nan')).ffill()


def _twse_stock_day(stock_id, ym):
    """One TWSE stock-month (STOCK_DAY, date=YYYYMM01). Empty when the month has no rows
    for the id; any other non-OK answer raises so it can never be cached as an empty month."""
    r = _tw_public_get(_TWSE_STOCK_DAY, {'response': 'json', 'date': f'{ym[:4]}{ym[5:7]}01',
                                         'stockNo': stock_id})
    j = r.json()
    stat = str(j.get('stat', ''))
    if stat != 'OK':
        if '沒有符合條件' in stat:
            return _tw_daily_frame([])
        raise TwPublicUnavailable(f'TWSE STOCK_DAY {stock_id} {ym}: {stat[:60]}')
    # 日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, …
    return _tw_daily_frame([(_roc_date(x[0]), _tw_num(x[3]), _tw_num(x[4]), _tw_num(x[5]),
                             _tw_num(x[6]), _tw_num(x[1])) for x in j.get('data', [])])


def _tpex_trading_stock(stock_id, ym):
    """One TPEx stock-month (tradingStock, date=YYYY/MM/01). 成交仟股 → shares (×1,000, so
    volume is rounded to the thousand — TWSE and FinMind carry exact shares)."""
    r = _tw_public_get(_TPEX_TRADING_STOCK, {'code': stock_id, 'date': f'{ym[:4]}/{ym[5:7]}/01',
                                             'response': 'json'})
    tables = r.json().get('tables') or []
    if not tables:
        raise TwPublicUnavailable(f'TPEx tradingStock {stock_id} {ym}: no tables in the answer')
    # 日 期, 成交仟股, 成交仟元, 開盤, 最高, 最低, 收盤, …
    return _tw_daily_frame([(_roc_date(x[0]), _tw_num(x[3]), _tw_num(x[4]), _tw_num(x[5]),
                             _tw_num(x[6]), _tw_num(x[1]) * 1000) for x in tables[0].get('data', [])])



def _fetch_twstock_daily_public_raw(stock_id, market, start, end):
    fetch = _twse_stock_day if market == 'twse' else _tpex_trading_stock
    frames = [f for f in (fetch(stock_id, ym) for ym in _tw_public_months(start, end)) if not f.empty]
    return pd.concat(frames) if frames else _tw_daily_frame([])


def _tw_market_file():
    return _cache_dir() / 'twstock_public_market.json'


def _twse_all_codes():
    r = _tw_public_get(_TWSE_STOCK_DAY_ALL, {'response': 'open_data'})
    rows = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
    if not rows or '證券代號' not in rows[0]:
        raise TwPublicUnavailable('TWSE STOCK_DAY_ALL: unexpected layout')
    col = rows[0].index('證券代號')
    return sorted({row[col].strip() for row in rows[1:] if len(row) > col})


def _tpex_all_codes():
    r = _tw_public_get(_TPEX_MAINBOARD, {})
    codes = {str(x.get('SecuritiesCompanyCode', '')).strip() for x in r.json()} - {''}
    if not codes:
        raise TwPublicUnavailable('TPEx mainboard quotes: no rows')
    return sorted(codes)



def _tw_public_market(stock_id):
    """'twse' / 'tpex' from the exchanges' latest full-market files (TWSE STOCK_DAY_ALL open
    data, TPEx mainboard quotes — the two daily sets registered on data.gov.tw), or the
    market an earlier probe settled on; None when the id is in neither (delisted or
    unknown). A hit in a stale file still counts — listing status does not flip overnight —
    so the two files are re-fetched only on a miss, at most once a day."""
    datetime = _dt()
    path = _tw_market_file()
    try:
        data = json.loads(path.read_text())
    except Exception:
        data = {}

    def _lookup():
        for m in ('twse', 'tpex'):
            if stock_id in data.get(m, ()):
                return m
        return data.get('resolved', {}).get(stock_id)

    market = _lookup()
    day_ago = (datetime.utcnow() - timedelta(days=1)).strftime(_META_TS_FMT)
    if market is None and data.get('fetched_at', '') < day_ago:
        data.update(twse=_twse_all_codes(), tpex=_tpex_all_codes(),
                    fetched_at=datetime.utcnow().strftime(_META_TS_FMT))
        _write_market_file(data)
        market = _lookup()
    return market


def _write_market_file(data):
    path = _tw_market_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # same per-thread tmp as cache._atomic_to_parquet — market files get written from
    # parallel report bricks too
    tmp = _tmp_path(path)
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _tw_public_probe_market(stock_id, ym):
    """Neither exchange lists the id today (delisted?): ask each for the first requested
    month, and the one with rows is the market — remembered so this runs once per id."""
    for market, fetch in (('twse', _twse_stock_day), ('tpex', _tpex_trading_stock)):
        if market == 'twse' and ym < _TWSE_STOCK_DAY_FROM:
            continue
        if not fetch(stock_id, ym).empty:
            try:
                data = json.loads(_tw_market_file().read_text())
            except Exception:
                data = {}
            data.setdefault('resolved', {})[stock_id] = market
            _write_market_file(data)
            return market
    raise TwPublicUnavailable(f'{stock_id}: no rows on TWSE or TPEx for {ym}')


def _twse_exright_rows(year, end_day):
    r = _tw_public_get(_TWSE_EXRIGHT, {'response': 'json', 'startDate': f'{year}0101',
                                       'endDate': end_day.replace('-', '')})
    j = r.json()
    stat = str(j.get('stat', ''))
    if stat != 'OK':
        if '沒有符合條件' in stat:
            return []
        raise TwPublicUnavailable(f'TWSE TWT49U {year}: {stat[:60]}')
    # 資料日期, 股票代號, 股票名稱, 除權息前收盤價, 除權息參考價, …
    return [(_roc_date(x[0]), x[1].strip(), _tw_num(x[3]), _tw_num(x[4])) for x in j.get('data', [])]


def _tpex_exright_rows(year, end_day):
    r = _tw_public_get(_TPEX_EXRIGHT, {'startDate': f'{year}/01/01', 'endDate': end_day.replace('-', '/'),
                                       'response': 'json'})
    tables = r.json().get('tables') or []
    if not tables:
        raise TwPublicUnavailable(f'TPEx exDailyQ {year}: no tables in the answer')
    # 除權息日期, 代號, 名稱, 除權息前收盤價, 除權息參考價, …
    return [(_roc_date(x[0]), x[1].strip(), _tw_num(x[3]), _tw_num(x[4])) for x in tables[0].get('data', [])]


def _tw_exright_events(market, start, end):
    """The exchange's whole-market 除權息計算結果表 (TWSE TWT49U / TPEx exDailyQ) rows with
    ex-dates in [start, end]: index = ex-date, columns stock_id / prev_close / ref_price.
    One parquet per month under cache/twstock_exright_{market}/: a past month is fetched
    once (completed once if written before the month ended, like every monthly cache), the
    current month re-fetched when older than an hour. Both sites answer a whole year per
    request, so a missing month costs one request and fills the year's other months too."""
    datetime = _dt()
    cache_dir = _cache_dir() / f'twstock_exright_{market}'
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(_TPE)
    current_ym = today.strftime('%Y-%m')
    end_str = end or today.strftime('%Y-%m-%d')
    months = [ym for ym in _iter_months(start, end_str) if ym <= current_ym]

    def _needs(ym):
        path = cache_dir / f'{ym}.parquet'
        if not path.exists():
            return True
        if ym < current_ym:
            return _written_before_month_end(path, ym)
        return time.time() - path.stat().st_mtime > 3600

    rows_of = _twse_exright_rows if market == 'twse' else _tpex_exright_rows
    for year in sorted({ym[:4] for ym in months if _needs(ym)}):
        rows = rows_of(year, min(f'{year}-12-31', today.strftime('%Y-%m-%d')))
        df = pd.DataFrame(rows, columns=['date'] + _TW_EXRIGHT_COLS)
        df = df.astype({'stock_id': str, 'prev_close': float, 'ref_price': float})
        df.index = pd.DatetimeIndex(df.pop('date'), name='date')
        for ym in _iter_months(f'{year}-01', min(f'{year}-12', current_ym)):
            _atomic_to_parquet(df[df.index.strftime('%Y-%m') == ym], cache_dir / f'{ym}.parquet')
    frames = [pd.read_parquet(cache_dir / f'{ym}.parquet') for ym in months]
    out = pd.concat(frames) if frames else pd.DataFrame(columns=_TW_EXRIGHT_COLS)
    return out[(out.index >= pd.Timestamp(start)) & (out.index <= pd.Timestamp(end_str))]


def _tw_exright_for(stock_id, market, start, end):
    markets = (market,) if market else ('twse', 'tpex')
    events = pd.concat([_tw_exright_events(m, start, end) for m in markets])
    return events[events['stock_id'] == stock_id]



def _tw_forward_adjust(df, events):
    """後復權 by the api's forward_adjust rule (api/tw/twstock/adjuster.py): rows before an
    ex-date keep their price, rows from the ex-date on are multiplied by 除權息前收盤價 ÷
    除權息參考價. For cash and stock dividends that is prev_close × (1 + stock_ratio) ÷
    (prev_close − cash), the Blave factor; the exchange tables also carry other ex-rights
    events (現金增資 and the like) that Blave's adjustment leaves out, so from such a date
    the two series differ by that event's factor. An event with no bar before it in the
    frame is skipped and OHLC is rounded to 2, both as there."""
    factor = pd.Series(1.0, index=df.index)
    for ex_date, prev_close, ref in events[['prev_close', 'ref_price']].sort_index().itertuples():
        if not (prev_close > 0 and ref > 0) or ex_date <= df.index[0]:
            continue
        factor[df.index >= ex_date] *= prev_close / ref
    out = df.copy()
    cols = [c for c in ('Open', 'High', 'Low', 'Close') if c in out.columns]
    out[cols] = out[cols].mul(factor, axis=0).round(2)
    return out



def _fetch_twstock_daily_public(stock_id, start, end, adjust=False):
    """Daily OHLCV for one stock from its own exchange — TWSE STOCK_DAY (listed, 2010-01-04
    on) or TPEx tradingStock (OTC) — month by month through the monthly cache: a past month
    is fetched once, the current month re-fetched per call. adjust=True forward-adjusts with
    the exchange's 除權息計算結果表 (_tw_forward_adjust). Raises TwPublicUnavailable / a
    requests error when the exchange cannot serve it; the caller falls back."""
    datetime = _dt()
    # A window that has not happened yet (end left to us, start past Taipei tomorrow) is an
    # empty answer, not a request — the same rule as _extend_cache_single.
    if end is None and (datetime.now(_TPE) + timedelta(days=1)).strftime('%Y-%m-%d') < start:
        df = _tw_daily_frame([])
        df.attrs['source'] = 'TWSE/TPEx'
        return df
    market = _tw_public_market(stock_id) or _tw_public_probe_market(stock_id, start[:7])
    if market == 'twse' and start[:7] < _TWSE_STOCK_DAY_FROM:
        raise TwPublicUnavailable(f'TWSE STOCK_DAY has no data before 2010-01-04 (asked from {start})')
    df = _extend_cache_monthly(
        'twstock_daily', {'id': stock_id, 'src': market},
        lambda s, e: _fetch_twstock_daily_public_raw(stock_id, market, s, e), start, end,
        # TWSE says 「沒有符合條件」 for a month the id had no rows — and, unverified, maybe
        # when it throttles too; an empty month is re-asked once a day, never cached for good
        empty_marker_ttl_hours=24, month_by_month=True)
    if adjust and not df.empty:
        df = _tw_forward_adjust(df, _tw_exright_for(stock_id, market, start, end))
    df.attrs['source'] = 'TWSE' if market == 'twse' else 'TPEx'
    return df


def _fetch_twstock_daily_finmind_raw(stock_id, start, end):
    """FinMind free tier, raw TaiwanStockPrice: no token, 300 requests/hour, the whole range
    in one answer, numbers identical to the exchanges'. It has no adjusted series (that is
    the Sponsor tier), so factors still come from the exchanges' tables."""
    r = _tw_public_get(_FINMIND_DATA, {'dataset': 'TaiwanStockPrice', 'data_id': stock_id,
                                       'start_date': start, 'end_date': end})
    j = r.json()
    if j.get('status') != 200:
        raise TwPublicUnavailable(f"FinMind TaiwanStockPrice {stock_id}: {str(j.get('msg'))[:80]}")
    return _tw_daily_frame([(pd.Timestamp(x['date']), x['open'], x['max'], x['min'], x['close'],
                             x['Trading_Volume']) for x in j.get('data', [])])


def _fetch_twstock_daily_free(stock_id, start, end, adjust=False):
    """The two key-free sources in order (exchange, then FinMind free); → frame with
    attrs['source'], or raises when both fail so the caller can try Blave."""
    try:
        return _fetch_twstock_daily_public(stock_id, start, end, adjust)
    except Exception as e:
        print(f"  ⚠️  {stock_id} daily bars: exchange path failed ({type(e).__name__}: "
              f"{str(e)[:120]}) — trying FinMind free")
    df = _extend_cache_monthly(
        'twstock_daily', {'id': stock_id, 'src': 'finmind'},
        lambda s, e: _fetch_twstock_daily_finmind_raw(stock_id, s, e), start, end)
    if adjust and not df.empty:
        df = _tw_forward_adjust(df, _tw_exright_for(stock_id, _tw_public_market(stock_id), start, end))
    df.attrs['source'] = 'FinMind'
    return df


def _twstock_daily(stock_id, start, end, headers, adjust, blave_fn):
    """Source chain for the two daily entries: exchange → FinMind free → Blave (`blave_fn`),
    logging which one served; BLAVE_TWSTOCK_DAILY_SOURCE=blave skips the free ones, and so
    does a malformed `start` — the Blave 400 names the expected format."""
    try:
        datetime.strptime(start, '%Y-%m-%d')
        well_formed = True
    except (TypeError, ValueError):
        well_formed = False
    free_err = None
    if well_formed and _twstock_daily_source() != 'blave':
        try:
            df = _fetch_twstock_daily_free(stock_id, start, end, adjust)
            logging.info('%s daily bars served by %s', stock_id, df.attrs['source'])
            return df
        except Exception as e:
            free_err = e
            print(f"  ⚠️  {stock_id} daily bars: free sources failed ({type(e).__name__}: "
                  f"{str(e)[:120]}) — trying Blave")
    try:
        df = blave_fn()
    except DataAccessError as e:
        # Keep the free chain's failure on the gate error: without it a caller reads "no Blave
        # access" where the true cause is the exchange / FinMind being down.
        if free_err is not None:
            raise e from free_err
        raise
    df.attrs['source'] = 'Blave'
    logging.info('%s daily bars served by Blave', stock_id)
    return df


