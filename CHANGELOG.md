# Changelog

All notable changes to BBAC-D are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [v4.6] — 2026-09-29

Upgraded the data and execution layers to the official `blave-agent` 2026-09-29 snapshot,
compared line-by-line. The package layout (`lib/data/` as a package, not the official monolith) is
kept; every official internal name a check patches is re-read through the package per call, so the
official test suite sees the same patch surface it was written against.

### Added
- **`_get` transport seam** (`lib/data/http.py`) — the checks fake the wire by patching
  `lib.data.requests.get`; a pristine attr means no patch (use the pooled session), a patched one wins,
  so every fetcher — not just the ones that called `requests.get` bare — sees the fake. Let
  `tests/check_data_access_gate.py` return to the official file verbatim and pass.
- **`_tmp_path`** (`lib/data/cache.py`) — tmp name unique per process *and* thread. Two threads writing
  the same month (a batch with a repeated symbol, parallel report bricks) used to share one tmp and the
  first one's cleanup deleted the second one's file before its `os.replace`. Ported from official.
- **`month_by_month` cache mode** (`_extend_cache_monthly`) — a month that is empty *and recent* may
  just not be published yet; this writes no hole that the next call would then trust.
- **`_BROKER_RECENT_EMPTY_DAYS = 3`** (`lib/data/twstock.py`) — the same recent-empty rule for the
  broker/trader day caches, ported from official.
- **`fetch_twstock_market_value_all`** — ported official's full version: `market` / `is_etf` columns,
  the `twse_ex_etf_market_value` denominator, cache-hit field validation with refetch.
- **`fetch_liquidation_coin`** exported at package level; **Fear & Greed** and per-stock free-daily
  chain ports from official.
- `_shared._call_through` — the hop a submodule uses to call `lib.data.<name>` at call time (never at
  import, no cycle): restores the official patch surface across a package boundary.

### Changed
- VERSION bumped from v4.5 to v4.6.
- `lib/data/alpha.py` `_fetch_alpha_raw` restored to the official shape: `HTTPError` propagates instead
  of being wrapped into `RuntimeError`, `max_retries=3`, and the `Progress` tick back.
- `ARCHITECTURE.md` — the `lib/data` module box now lists all 12 modules (4 were missing: `_shared`,
  `http`, `batch`, `feeds`).

### Fixed
- **Patch-surface bridges** in 8 data modules — patching `lib.data.<name>` was invisible to a
  submodule that had re-bound the name in its own globals: `_retry_get`, `_raw_snapshot`,
  `_fetch_batch_cached`, `_fetch_twstock_cached_batch`, `fetch_kline`, `_CACHE_DIR`, `datetime`,
  the Binance limiter/inflight counters, and the two TW public limiters all now resolve through the
  package per call. Before this, 19 suite checks failed on invisible patches alone.
- `_tpex_all_codes` was missing its `return sorted(codes)` — returned `None` and the caller treated
  the whole board as missing.
- 5 unused imports removed (ruff F401) in the official test files; ruff is now clean over
  `lib/ manager/ core/ strategies/ examples/ tests/ apps/`.

### Removed (archived, not deleted)
- **60 official checks → `archive/tests_official_full_product/`** with a per-file reason. They test
  layers this deployment does not ship: 47 need the official `runtime/` agent layer (the LLM turn
  loop, the command listener, the desktop/cloud daemons), 3 need the `shell/` Electron app + node, 5
  are POSIX/macOS-only (`fcntl`, `SIGKILL`, the reconciler lock-fd read), 5 need a git repository or
  out-of-repo release artifacts. Two of them (`check_drift_band`, `check_restart_stop_order_gate`)
  fail identically on the official snapshot itself — pre-existing upstream failures, verified by
  running the same files there. Nothing deleted; move a file back to `tests/` to run it.
- `tests/` is now **77 checks, all passing** (`run_suite.sh` from the repo root).

## [v4.5] — 2026-09-16

### Added
- `ARCHITECTURE.md` — full layer diagram, data flow, module inventory, key contracts
- `LICENSE` — MIT
- `state/deployments.json` — deployment registry for healthcheck.py
- `CHANGELOG.md` — version history (Keep a Changelog format)
- `manager/README.md` — portfolio operations technical doc (10 scripts, architecture diagram, usage)
- `lib/__init__.py` — module index (was empty file, now 33-line docstring with all 37 modules)
- `.gitignore` — added `state/` runtime artifacts (HALT/audit/heartbeat/snapshots/execution) while keeping skeleton tracked
- README.md — structure tree updated with new files + cross-references to ARCHITECTURE.md and manager/README.md

### Changed
- VERSION bumped from v4.4 to v4.5
### Fixed
- 25 unused imports removed (ruff F401, auto-fix)
- 28 `except Exception: pass` → `except Exception as e: print(f"Error: ...: {e}")` across 12 files
  - 2 real defects: `lib/order_binance.py:547` + `lib/order_gateio.py:337` silently zeroed fees on failed commission lookup (PnL corruption)

## [v4.4] — 2026-09-12

### Added
- v4.4 calibration appendix (auto-generated by `core/analyze_results.py`)
- 13 cross-period stable rules (Stab=100%)
- Coin screener with 9-key rotation (safe 1.67 req/s/key)
- Vote dashboard (`apps/vote_dashboard/`)
- 4 user strategies in `strategies/` (btc_a05_short, eth_a08_short, btc_ti_hc_wh_1h_long, btc_hc_2side_1h)
- 3-layer validation (IS/OOS/MCPT) via `core/validate_strategy.py`
- Parameter scan with plateau detection (`lib/param_scan.py`)
- Watchboard widget API (`lib/watch.py`)
- Workspace report writer + templates (`lib/report.py`, `lib/report_templates.py`)
- Capital (群益) broker support (`lib/order_capital.py`, `lib/capital_worker.py`)
- Paper trading venue (`lib/account_paper.py`, `lib/order_paper.py`, `lib/paper_data.py`)

### Changed
- `lib/data.py` (single 3,250-line file) → `lib/data/` package (http/cache/batch/kline/alpha/twstock/twmarket/twfutures)
- Ruff config narrowed to F401/F811/E9 only (zero false-positive, high-value rules)

## [v4.0] — 2026-09-06

### Added
- 59 structured rules in `rules_catalog/catalog.py` (54 active, 5 skip)
- Catalog-driven batch backtest framework (`core/run_batch.py`)
- Direction calibration (`core/calibrate_directions.py`)
- Dead-coin skip (MATICUSDT, RNDRUSDT etc. return 400 → auto-skip)
- Cross-period validation (36 coins 90d + 14 coins 180d)
- Chan theory plotting (`core/chan_plot.py` with `vendor/chan/` framework)
- 8 reference strategies in `examples/` (crypto + Taiwan stocks + Taiwan futures)
- CI with hash-locked dependencies (Python 3.11 + 3.12 matrix)
- `AGENTS.md` — 246-line AI agent operating manual
- 26 reference docs in `references/`

### Infrastructure
- `pyproject.toml` with ruff lint config
- `.github/workflows/ci.yml` — dual Python version matrix
- `.gitignore` covering cache/strategies/env/logs
