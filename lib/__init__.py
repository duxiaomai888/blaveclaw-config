"""
lib/ — BBAC-D shared library (post-pivot minimal base).

The 庄家-system pivot stripped the directional-strategy apparatus (backtest
engine, runner, param scan, validation, exits, execution, order/account libs,
guard, reports, watchboard, etc.). What remains here is the data-access layer
plus its single dependency:

  data/        data access (Blave API, BingX, TWSE / TPEx, Taiwan stocks,
               futures, market-wide, alpha, snapshots, feeds) → pandas DataFrames.
               All fetchers are re-exported at the package level, so
               `from lib.data import fetch_kline` works without naming the sub-module.
               Docs: references/{lib,twstock,twfutures,cache}.md
  progress.py  Progress indicator used by the long-running data fetchers
               (lib/data/alpha.py, lib/data/kline.py). Standalone — imports only stdlib.

Dependency rule: lib/data → only stdlib + third-party (pandas/numpy/requests) +
lib.progress. Nothing else in lib/ is imported by the data layer.

The 庄家 (market-maker) system lives in archive/live_eval/.
The command-conception layer (发出指令构思) is to be built on top of the data layer.
"""
