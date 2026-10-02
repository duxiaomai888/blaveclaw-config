"""HTTP transport layer — session pooling, rate limiting, Blave key rotation.

拆分自原 lib/data.py(见 lib/data/__init__.py)。这里放的是所有数据端点共享的
传输层,不放任何具体端点逻辑。
"""
import os
import time
import threading
from pathlib import Path

import requests

from ._shared import DataAccessError, _NO_ACCESS_MSG, _check_data_access, _daemon_on_desktop

__all__ = [
    'BASE', '_CACHE_DIR',
    '_thread_local', '_session', '_RateLimiter', '_get',
    '_HEADERS_LOCK', '_KEY_PAIRS', '_KEY_INDEX', '_load_key_pairs',
    '_default_headers', '_current_headers', '_rotate_headers',
    '_drop_bad_key', '_retry_get', '_desktop_denied',
]


# ── Connection-pooled HTTP session (per thread) ──────────────────────────────
# A bare requests.get() does a fresh TCP+TLS handshake every call. On a cold
# batch fetch of 539 coins × 7 endpoints × N month-chunks, the handshake
# overhead alone is 20-40% of wall time. A threading.local Session keeps the
# keep-alive pool on the thread that owns it (requests.Session is not thread-
# safe), so chunk workers each get their own persistent connection pool while
# the header (Blave key rotation) logic is unchanged — _session() only swaps
# the transport, not the credentials.
_thread_local = threading.local()


def _session():
    """A thread-local requests.Session with keep-alive connection pooling.

    Created lazily per thread and reused for the process lifetime. Callers
    pass this to requests-style calls instead of the module-level
    requests.get so the underlying urllib3 PoolManager reuses connections to
    api.blave.org / open-api.bingx.com. Headers are still set per-request by
    the caller (key rotation / signing), so the empty default here is fine."""
    s = getattr(_thread_local, 'session', None)
    if s is None:
        s = requests.Session()
        a = requests.adapters.HTTPAdapter(
            pool_connections=10, pool_maxsize=20, max_retries=0)
        s.mount('https://', a)
        s.mount('http://', a)
        _thread_local.session = s
    return s


class _RateLimiter:
    """Token bucket: allows max_calls requests per period seconds."""
    def __init__(self, max_calls, period):
        self._max   = max_calls
        self._period = period
        self._calls  = []
        self._lock   = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.time()
            self._calls = [t for t in self._calls if now - t < self._period]
            if len(self._calls) >= self._max:
                wait = self._period - (now - self._calls[0])
                if wait > 0:
                    time.sleep(wait)
                    now = time.time()
                    self._calls = [t for t in self._calls if now - t < self._period]
            self._calls.append(time.time())

# The transport contract the checks patch is lib.data.requests.get (a check that fakes the
# wire sets it). A pristine attr means no patch, so the pooled session is used; a patched one
# wins, so every fetcher — not just the ones calling requests.get bare — sees the fake.
# Captured at import: requests.get itself is the same module attr a patch rebinds.
_PURE_GET = requests.get


def _get(url, **kwargs):
    fn = requests.get
    if fn is not _PURE_GET:
        return fn(url, **kwargs)
    return _session().get(url, **kwargs)


BASE      = 'https://api.blave.org'
# lib/data/http.py → 上三层才是 workspace 根(原 lib/data.py 只需上两层)。
_CACHE_DIR = Path(__file__).parent.parent.parent / 'cache'

# ── Blave API key rotation ─────────────────────────────────────────────────
# .env may hold up to 20 key pairs (blave_api_key / blave_secret_key through
# _key20/_secret_key20). Each pair is a distinct per-IP rate-limit budget, so a
# cold multi-year fetch that fans out over chunk workers hammers one key —
# hitting 429s and exponential backoff. Rotating per REQUEST spreads the same
# traffic over every key present (request-level rotation). We also drop a key
# once it starts returning 403s so the pool stays healthy and the next requests
# keep on rotating through valid credentials.
_HEADERS_LOCK = threading.Lock()
_KEY_PAIRS    = None          # [(api_key, secret_key), ...], built lazily
_KEY_INDEX    = 0             # current rotation index for the active pool


def _load_key_pairs():
    """All Blave key pairs found in the environment, key1 first.

    Reads os.environ (every entry point load_dotenv()s before fetching); falls
    back to loading the workspace .env directly so the rotation also works when
    a caller forgot to. Missing secret tolerated — same shape the callers use.
    """
    try:
        import dotenv
        # lib/data/http.py → 上三层到 workspace 根(原 lib/data.py 上两层)。
        dotenv.load_dotenv(Path(__file__).parent.parent.parent / '.env')
    except Exception:
        pass
    pairs = []
    for i in [''] + [str(n) for n in range(2, 21)]:
        api = os.environ.get(f'blave_api_key{i}')
        if api:
            pairs.append((api, os.environ.get(f'blave_secret_key{i}', '') or ''))
    return pairs


def _default_headers():
    """First key pair as a headers dict — for legacy callers that fetch without headers."""
    if _KEY_PAIRS is None:
        return None
    if not _KEY_PAIRS:
        return None
    return {'api-key': _KEY_PAIRS[0][0], 'secret-key': _KEY_PAIRS[0][1]}


def _current_headers(headers=None):
    """Return the currently active key pair for the caller's headers."""
    if not headers or 'api-key' not in headers:
        return None, None
    global _KEY_PAIRS, _KEY_INDEX
    with _HEADERS_LOCK:
        if _KEY_PAIRS is None:
            _KEY_PAIRS = _load_key_pairs()
        if not _KEY_PAIRS:
            return None, None
        if _KEY_INDEX >= len(_KEY_PAIRS):
            _KEY_INDEX = 0
        return _KEY_PAIRS[_KEY_INDEX]


def _rotate_headers(headers=None):
    """Request-level rotation: swap a Blave header dict onto the next key pair.

    No-op when the headers are not Blave's (no 'api-key'), only one pair
    exists, or rotation was never enabled. Thread-safe — concurrent chunk
    workers take distinct pairs. Returns a copy; the caller's dict is untouched.
    """
    global _KEY_PAIRS, _KEY_INDEX
    if not headers or 'api-key' not in headers:
        return headers
    with _HEADERS_LOCK:
        if _KEY_PAIRS is None:
            _KEY_PAIRS = _load_key_pairs()
    if len(_KEY_PAIRS) <= 1:
        return headers
    with _HEADERS_LOCK:
        if not _KEY_PAIRS:
            return headers
        api, secret = _KEY_PAIRS[_KEY_INDEX]
        _KEY_INDEX = (_KEY_INDEX + 1) % len(_KEY_PAIRS)
        print(f"  Blave key rotation -> idx={_KEY_INDEX}/{len(_KEY_PAIRS)}")
    h = dict(headers)
    h['api-key'] = api
    if 'secret-key' in h:
        h['secret-key'] = secret
    return h


def _drop_bad_key(headers=None):
    """Prune the active key from the pool when the server rejects it with 403."""
    global _KEY_PAIRS, _KEY_INDEX
    if not headers or 'api-key' not in headers:
        return
    with _HEADERS_LOCK:
        if _KEY_PAIRS is None:
            _KEY_PAIRS = _load_key_pairs()
        active = headers.get('api-key')
        if not active:
            return
        _KEY_PAIRS = [(api, secret) for api, secret in _KEY_PAIRS if api != active]
        if not _KEY_PAIRS:
            _KEY_INDEX = 0
            return
        _KEY_INDEX = min(_KEY_INDEX, len(_KEY_PAIRS) - 1)


def _desktop_denied(r):
    """Scheduled run on the desktop: the key in `.env` stopped working since the shell last
    synced it (hour fee not chargeable ERR007, key revoked ERR005, 401). Same meaning as an
    empty key. A chat turn keeps the raw 403 — its body carries what the user must be told."""
    if not _daemon_on_desktop():
        return
    if r.status_code == 401 or (r.status_code == 403 and any(c in r.text for c in ('ERR007', 'ERR005'))):
        raise DataAccessError(_NO_ACCESS_MSG)


def _retry_get(url, max_retries=6, **kwargs):
    """GET with exponential backoff on transient failures (2, 4, 8, 16, 32, 64 s).

    Retries 429 (Blave per-IP rate limit, 500/5min — authoritative figure per
    official blaveclaw-config lib/data.py; the blave-quant SKILL.md's
    "100 req / 5 min" is a legacy/conservative value superseded by the config
    repo), 5xx (incl. 503, which the
    API returns when upstream FinMind itself rate-limits), and connection/read
    timeouts (a slow batch endpoint under load — e.g. a big multi-symbol crypto
    kline request — reads exactly like this; previously an unlucky timeout just
    silently dropped that whole chunk's symbols with no retry). 403 is NOT
    retried — the API returns it only for a missing/invalid api-key, a permanent
    error that backing off would just delay surfacing. A 403 causes the active
    key to be dropped from the rotation pool so later requests move on to a valid
    one immediately.

    A non-retried 4xx raises requests.HTTPError with the response body appended
    (truncated to 200 chars) — the 4xx bodies carry the only explanation there is.
    """
    blave = url.startswith(BASE)   # BingX klines share this helper and stay public
    if blave:
        _check_data_access(kwargs.get('headers') or {})
    hdrs = kwargs.get('headers')
    if hdrs is not None:
        kwargs['headers'] = _rotate_headers(hdrs)
    for attempt in range(max_retries):
        try:
            r = _get(url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"  {type(e).__name__} transient — retrying in {wait}s ({url.split('/')[-2]}/{url.split('/')[-1]})")
            time.sleep(wait)
            continue
        if r.status_code != 429 and r.status_code < 500:
            if blave:
                _desktop_denied(r)
            if r.status_code == 403:
                _drop_bad_key(kwargs.get('headers') or {})
            try:
                r.raise_for_status()
            except requests.HTTPError as exc:
                # raise_for_status()'s message is status + URL only. The API puts the
                # reason in the body ("start must not be after end", "Invalid start
                # date, expected YYYY-MM-DD"), and a strategy author who never sees it
                # cannot tell a bad argument from a broken endpoint. Same type and
                # .response as before so the callers switching on status still work.
                raise requests.HTTPError(f'{exc} — {r.text[:200]}', response=r) from exc
            return r
        wait = 2 ** (attempt + 1)
        print(f"  {r.status_code} transient — retrying in {wait}s ({url.split('/')[-2]}/{url.split('/')[-1]})")
        time.sleep(wait)
    r.raise_for_status()
    return r
