# AGENTS.md 扩展段(详细参考)

> 🔴 **AGENTS.md 必读;本文档按需查阅。** AGENTS.md 是 agent 启动必读,本文档是详细参考。
> 所有"长说明"和"具体函数列表"都在这里,AGENTS.md 只放"硬规则"。

---

## Examples(参考策略)

`examples/` 包含 7 个完整参考策略,需要具体模式时读:

- `btc_sma_cross/` — Type A,SMA crossover,含 `scan.py` 参数搜索
- `btc_ti_5min/` — Type A,Taker Intensity 阈值(Blave alpha),5min kline
- `cl_sma/` — Type A,WTI 原油(CL)1h SMA crossover + NYMEX 结算退出;`fetch_db_kline` + `settlement_signals_from_db()`
- `tsmc_ma/` — Type A,台股(2330)SMA crossover
- `txf_ma_1m/` — Type A,台指期(TXF)1m SMA crossover
- `tw100_foreign_zscore/` — Type C,台股 100 标的组合,外资 z-score
- `twstock_momentum/` — Type C,台股动量,top-N 等权

**这些不是用户策略。** 用户策略在 `strategies/`。

---

## Strategy Types(分类决策树)

写策略前先分类:

### Type A — Signal Strategy(单币种信号)

- 单一固定 symbol + 固定 interval
- 入出场由指标/价格信号驱动(MA cross、RSI 等)
- **回测有意义,上 live 前必跑**
- 用 `references/strategy-code.md` + `strategies/TEMPLATE_A.py`
- 用 Blave alpha 指标时,读 `references/strategy-code.md` 的 **Alpha Indicators** 段:`lib.data` fetchers 写在 `fetch_data(hdrs)` 内,join df index,ffill。**不要**自己写 fetch 逻辑
- `END`:backtest 模式 hardcode 固定过去日期(如 `"2026-05-21"`);**绝不用 dynamic expression**。固定日期保 cache 命中;`end=None` 必触发 delta API。live 模式设 `END = None`
- 三个函数:
  - **`_add_indicators(df, param1=DEFAULT1, ...)`**:加指标列;参数默认 = 模块级常量
  - **`fetch_data(hdrs) → df`**:拉 kline + 辅助数据。**不**调 `_add_indicators`
  - **`compute_signals(df, param1=DEFAULT1, param2=DEFAULT2, ...) → pd.Series | (pd.Series, exec_at_close)`**:接受 scan 参数 kwargs;先调 `_add_indicators(df, param1, param2)`;返回 signal 或 `(signal, settle)` tuple。这个签名让 `scan_grid` 能驱动 scan
- `run(locals(), fetch_data, compute_signals, send_telegram_fn=make_sender())` — runner 处理其他
- `WARMUP`(可选)— 跳过开头的 N 根 bar(指标未稳定)= 所有 rolling window 之和
- 信号值:正 float = long(仓位比例),负 float = short,0.0 = flat/cover,nan = hold(ffill)
- 期货 settlement exit:用 0.0(同 flat),**不要**用 -1.0(那是开空)
- **执行模型(2 种,必须显式):**
  - **next-bar open**(默认):`close[t]` 信号在 `open[t+1]` 执行。plain `pd.Series`,不需要 exec_at_close
  - **this-bar close**:`close[t]` 信号在 `close[t]` 执行(如期货结算)。返回 `(signals, exec_at_close)`,exec_at_close 是 bool `pd.Series`(settlement bar 为 True)
  - 期货策略用 `fetch_db_kline`:调 `settlement_signals_from_db(df, signal)`,返回 `(signal, exec_at_close)`。直接 `return` 即可。自动标记 settlement bar

### Type B — Everything else(screener、grid、套利、one-off)

- 从零写,**无模板**
- **不做回测**
- 写交易所 API 调用前:**必读** `skills/blave-quant/references/{exchange}-skill.md`(错 endpoint / 缺 broker header / 错参数名 = 静默失败 + 丢 attribution)
- 部署前**必**要用户确认

### Type C — Portfolio Strategy(多标的组合)

Examples:外资 z-score、多因子轮动、跨市场配置、ETF 周期 rebalance

- **必读** `strategies/TEMPLATE_C.py`,复制到 `strategies/[name]/strategy.py` 填空。**不要**从零写
- 写前读 `examples/tw100_foreign_zscore/strategy.py`
- 跨**多标的**用 weight vector 配置
- 周期 rebalance(每日/周/月);weight 变化驱动交易
- 外部预计算信号(如 Z-Score DataFrame);build weight matrix
- **不要** pre-shift weights(runner 自动处理时序)
- **`compute_signals` 必须返回 `(weights_mat, price_df)`**:
  - `weights_mat`:numpy array `(n_days, n_stocks)` — 每 close 时刻目标权重
  - `price_df`:MultiIndex DataFrame 用 `pd.concat({'close': close_df, 'open': open_df}, axis=1)`;'open' 层可选但保执行价准确
  - 可选 3rd `exec_at_close`:bool array `(n_days,)` for 此 bar 用 close 不用 next open
- **回测 REQUIRED** 上 live 前
- 部署前**必**要用户确认
- `END`:同 Type A
- **台股 universe 必须按 sector 分层抽样** — 不要 `[:N]` head-slice(代码按 sector 排序,head 会集中在水泥/食品/纺织)。用 `references/twstock.md` 的 sector-stratified helper
- **候选池 — NO lookahead bias**:`fetch_data` 拿的 universe 必须只来自回测开始时可获得的信息。**绝不**用全周期聚合过滤(如 `nlargest(N)` on cumulative net buy) — 那会泄露未来数据。改用所有曾经出现过的标的,让 `compute_signals` 用 per-rebalance 当时可得的 lookback 窗口做排名

**决策树:**

```
策略交易 ONE 固定 symbol(如 BTCUSDT)固定 interval?
  → YES → Type A  (lib/runner.py + TEMPLATE_A.py)

策略分配权重跨 MULTIPLE 标的 / 一篮子资产,周期 rebalance?
  → YES → Type C  (lib/runner.py + TEMPLATE_C.py,见 examples/tw100_foreign_zscore)

其他(screener、grid、套利、one-off 执行、alert bot)?
  → Type B  (从零写,无回测)
```

不确定 A vs C:**Type A 1 个 symbol 1 个 position(long/short/flat);Type C N 个 symbol + weight vector 总和 ≤ 1**

---

## Shared Library (lib/) — 12 个模块速查

工作区有共享库在 `lib/`,避免策略间重复代码。**永远 import,不要 inline 写:**

### `lib/data.py` — 数据抓取(chunking + cache 内建)

**Kline / 加密 alpha:**
- `fetch_db_kline(dataset, symbol, schema, start, end, headers)` → CME/NYMEX/ICE OHLCV + `instrument_id` 列;datasets: `GLBX.MDP3` (CL, GC), `IFEU.IMPACT` (BRN);schemas: `ohlcv-1m` / `ohlcv-1h` / `ohlcv-1d`
- `settlement_signals_from_db(df, signal)` → 期货策略:返回 `(signal, exec_at_close)`,直接 `return` 出 compute_signals
- `fetch_kline(symbol, interval, start, end, headers)` → OHLCV DataFrame
- `fetch_holder_concentration(symbol, interval, start, end, headers)` → alpha 列
- `fetch_taker_intensity(symbol, interval, start, end, headers, timeframe='24h')` → alpha
- `fetch_whale_hunter(symbol, interval, start, end, headers, timeframe='24h', score_type='score_oi')` → alpha
- `fetch_squeeze_momentum(symbol, start, end, headers)` → alpha(1d fixed)
- `fetch_liquidation(symbol, interval, start, end, headers, timeframe='24h')` → alpha
- `fetch_market_direction(interval, start, end, headers)` → alpha(无 symbol)
- `fetch_capital_shortage(interval, start, end, headers)` → alpha(无 symbol)
- `fetch_market_sentiment(symbol, interval, start, end, headers)` → alpha
- `fetch_top_trader_exposure(interval, start, end, headers)` → alpha(BTC only,无 symbol)

**台股价格(raw vs adjusted,关键区别):**
- `fetch_twstock_price(sid, start, end, headers)` → OHLCV,**actual market prices**(画图用)
- `fetch_twstock_price_adj(sid, start, end, headers)` → Open/Close,**复权向后价**(回测用)
- 绝不为了画图用 `fetch_twstock_price_adj`

**台股数据(universe、batch、fundamental、lookahead-bias):** `references/twstock.md`

**台股期货:**
- `fetch_twfutures_ohlcv(symbol, schema, start, end, headers)` → TW 期货 OHLCV;symbol='TXF';schema='1d'/'1m'/'5m'/'15m'/'30m'/'60m';Volume 是合约数;数据从 2020-03-22
- `txf_settlement_mask(index)` → bool Series,True 是 TXF 月度结算前最后 1min bar(每月第 3 星期三 13:30 TWN)

### `lib/execute.py` — 交易执行 + 状态

- `from lib.execute import update_state, load_state, save_state` — 交易执行和状态管理
- `state.json` schema: `{"position": float, "symbol": str}` — `position` 是当前 signal 值(正=long,负=short,0=flat);部署配置(exchange, asset_spec)在 `portfolio_config.json`,不在 state
- **`run_twap(symbol, side, total_qty, duration_min, n_slices, place_slice_fn, strategy_name, signal_price=None, send_telegram_fn=None)`** — TWAP 拆单(2026-06 新增,exchange-agnostic)
- **`load_twap_log(strategy_name)`** — 读 `strategies/{name}/twap_log.jsonl`,返回 (slices, summaries) for impact 分析

### `lib/analysis.py` — PnL / 统计 / 制度

- `from lib.analysis import regime_analysis, plot_regime` — 制度分析 + 制度图
- `precise_pnl` / `compute_stats` — 自定义 loop 时用(罕见)

### `lib/param_scan.py` — 参数扫描 + plateau

- `from lib.param_scan import percentile_thresholds` — 用 p5/p95 当 bounds,linspace n_parts 值 → (entry_vals, exit_vals);打印分布统计
- `from lib.param_scan import scan_grid` — 跑 2D scan,返回 Sharpe grid。支持所有策略类型:
  - `row_param`/`col_param`:转发给 `compute_signals_fn` 的 kwarg(默认 `'entry_th'`/`'exit_th'`)
  - `warmup`:跳过的开头 bar(rolling 窗口 warmup);`compute_signals_fn` 收完整 df,扫 PnL 在 `df.iloc[warmup:]`
  - `valid_fn`: `(row_val, col_val) → bool` 跳过无效组合(默认 `row > col`;SMA 用 `lambda f, sl: f < sl`)
  - `compute_signals_fn` 返回 `(signal, settle)` tuple 时,settle 自动作 `exec_shifted`
  - **所有策略类型用同一个 `scan_grid` 调用** — 见 `examples/btc_sma_cross/scan.py`(SMA-scan 模式) 和 `examples/btc_ti_5min/scan.py`(threshold-scan 模式)
- `from lib.param_scan import find_plateau, plot_heatmap` — plateau 检测和热图
  - `find_plateau` 返回 5 值: `best_idx, nbr_mean, best_row, best_col, best_sharpe` — 用 `best_row`, `best_col`, `best_sharpe`;`nbr_mean` 是 2D array(**不要**当 scalar 格式化)
  - 标准用法: `best_idx, _, best_row, best_col, best_sharpe = find_plateau(grid, ROW_VALS, COL_VALS)`
  - `plot_heatmap` `output_path` **必填** — 永远 `output_path='strategies/{strategy_name}/heatmap.png'`,不要 `/tmp/`

**参数扫描工作流:**
1. 跑 `scan.py` 找最佳参数
2. **直接更新现有 `strategy.py` 的参数** — **不要**新建 strategy 文件夹
3. 跑同一 `strategy.py` 的回测验证

### `lib/validation.py` — MCPT

- `from lib.validation import mcpt, plot_mcpt` — Monte Carlo Permutation Test;`mcpt(close, position, n=2000, fee=..., target_vol=...)` → `(actual_sharpe, p_value, dist)`

### `lib/notify.py` — Telegram

- `from lib.notify import make_sender, send_text, send_photo`
- `make_sender()` → text sender(broadcast 到所有配对 chat ID)
- `make_sender(photo=True)` → photo sender
- `send_telegram_fn=make_sender()` when calling `run()`
- **🔴 CRITICAL — 配对检查(session 开始时跑,任何操作前):** Telegram 配对在独立 Telegram session — 你不能从对话上下文推断状态。先检查文件:
  ```python
  import json, os
  allow_path = "/root/.openclaw/credentials/telegram-default-allowFrom.json"
  paired = (
      os.path.exists(allow_path)
      and bool(json.load(open(allow_path)).get("allowFrom"))
  )
  ```
  `paired = False`:告诉用户 "Telegram is not paired yet. Please complete the pairing flow via the bot." 在配对确认前不跑任何 strategy run 或 notification

### `lib/strategy.py` — 波动率

- `from lib.strategy import add_realized_vol` — 原地算 `realized_vol`。**标准窗口 30 天** — 按 interval 转 bar(1d→30, 1h→720, 5min→8640)
- `from lib.strategy import apply_vol_scaling` — 信号缩放 `(target_vol / realized_vol).clip(vol_cap)`;long/short 都 work;在 `compute_signals` 末尾调
  - 标准默认: `target_vol=0.30`, `vol_cap=2.0`
  - **必**先调 `add_realized_vol` 保 df 有 `realized_vol` 列
- **所有 risk-parity 策略必用这两个函数 — 不要 strategy 文件内自己算 vol**

### `lib/pnl.py` — 日收益

- `from lib.pnl import daily_returns_typeA, daily_returns_typeC` — 从 pf_series 抽 daily returns(runner 自动调,无需手动)
- `from lib.pnl import load_all_stats` — 读所有 `strategies/*/stats.json`(含 daily_returns)给 manager 用

**写新可复用逻辑**(新 exchange order helper、新 alpha fetcher 等):
- **先**加到对应 `lib/` 文件(或新建,如 `lib/orders_binance.py`)
- **再**在 strategy 里 import

**策略特定逻辑**(信号计算、指标、某 exchange 的 place_order)留在 strategy 文件里。

**Marketplace lib rule** — 走 marketplace 共享的策略必须严格守这个边界:
- `compute_signal`、指标计算、所有影响 trade 决策的逻辑 **必须**留在 strategy 文件 — 永不在自定义 lib
- 自定义 `lib/` 文件只放 IO utilities(exchange order helper、data fetcher)— 能完整描述为"接口 + 行为"的逻辑
- 这让接收方能根据 description 重建缺失的 custom lib,不冒策略不一致风险

---

## Strategy Code Structure

**写策略前** 必读对应的 reference(见 Strategy Types 段):
- Type A → `references/strategy-code.md` + `strategies/TEMPLATE_A.py`
- Type C → `references/strategy-code.md` + `strategies/TEMPLATE_C.py` + `examples/tw100_foreign_zscore/strategy.py`
- Type B → 无模板,按需求写

---

## Charts (matplotlib)

**永远用英文** 所有 matplotlib 文本 — titles、axis labels、legends、annotations。中文在服务器渲染为乱码方块(□□□)因为默认字体无 CJK glyph。

```python
# ✓ correct
plt.title("Cumulative Return")
plt.xlabel("Date")
plt.ylabel("Return (%)")
plt.legend(["Strategy", "Benchmark"])

# ✗ wrong — will show □□□
plt.title("累積報酬")
```

---

## Sending Images

生成图表时**必须**发到 Telegram:

```python
from lib.notify import send_photo, send_text
send_photo("/tmp/chart.png")
send_text("Backtest complete — Sharpe 1.42, MDD -12%")
```

Token 和 chat_id 自动从 `/root/.openclaw/openclaw.json` 读。

---

## Shell Commands

- **绝不写 `except Exception: pass`** — 静默失败藏所有错误。至少 `except Exception as e: print(f"Error: {e}")`。适用:scan.py, strategy.py, notify calls, exchange API calls
- **绝不**用 `&&` 或 `||` 或 `;` 串命令 — 一次跑一个
- 用 `python3 file.py [args]` 或 `node file.js` 直接跑 — 传参 OK,但**不**用 `&&` `||` `;` 串
- 装包:`pip install x` 单独跑一次
- **跑需要特定 workdir 的策略**:用绝对路径 + `workdir`(如果 exec 工具支持)。**不要** `cd path && python3 ...`:
  - 正确: `python3 /root/.openclaw/workspace/strategies/my_strategy/strategy.py` with `workdir=/root/.openclaw/workspace`
  - 或: `python3 strategies/my_strategy/strategy.py` with `workdir=/root/.openclaw/workspace`

---

## Skill Install / Update

用 non-interactive flags(裸 `npx skills add <url>` 触发 TUI 在 tmux 里会 fail):

```
npx -y skills add https://github.com/Blave-TW/blave-quant-skill -a openclaw -s blave-quant -y
```

其他 skill: `npx -y skills add <github-url> -a openclaw -s <skill-name> -y`

---

## Backtest Output

**绝不** 调 `bt.plot()` — 生成 20-30 秒的 HTML 交互文件,Telegram 无用。

每次回测后 `run()` 自动:
1. 写 `strategies/{name}/stats.json` — 含 Sharpe、MDD、daily_returns
2. 生成 `strategies/{name}/pnl.png` 发到 Telegram

注:runner 在内部从 precise PnL 算 result_d — 无需手动 array reconstruction。

---

## Strategy Marketplace

所有 marketplace 操作(browse、upload private、submit、share、backup/restore、download)读 `references/marketplace.md`。

- **绝不**代用户买策略
- 用户问共享策略 / 哪些可用:调 `GET /openclaw/marketplace/my/shared-with-me`
- 下载时:存到 `strategies/{name}/strategy.py`;`ImportError` on custom lib 时从 description 的 "Custom lib dependencies" 段重建

---

## Manager & Reconciler

完整工作流读 `references/manager.md`。

**🔴 CRITICAL:**
- **绝不手动 edit `portfolio_config.json["weights"]`**。Weights 必须用 `python3 manager/manager.py` 设。手动 weights 绕过 optimizer,下次 manager 跑时被覆盖。用户要调 allocation:用 `manager.py --account` 或 `--target-vol` — 不要手改
- Exchange routing 在 `portfolio_config.json`(`"exchanges"` dict),**不在** strategy 文件 — 见 `references/manager.md`
- **绝不**在 `manager/` 内创建文件或子目录。删 strategy 时**不**删 `manager/` 任何文件
- 任何 reconcile 前:show pending order summary + 必请用户确认
- 用户要 backtest portfolio:用 `manager/management_backtest.py`,不用单 strategy backtest
- 用户要删 strategy:只删其自己的目录(如 `strategies/btc_kd_long/`)。**不**动 `manager/`
- **OKX `get_positions()` pitfall:** OKX positions API 对某些 instrument type 返回 `ctVal` 为 `None`。**不要**算 notional 用 `pos * markPx * ctVal` — 直接用 `notionalUsd` 字段。Zero notional 导致 position 被忽略、reconciliation 跳过
- **Order library → reconciler 是一个 atomic task:** 写或更新任何 `lib/order_*.py`(如 `lib/order_okx.py`)**必**在同一 session 也更新 `manager/reconciler.py` import 它、把 `get_positions()` / `place_order()` stubs 换成真调用。写库不 wire `reconciler.py` = 自动化交易永久坏。`place_order(symbol, signed_diff, asset_spec, reduce_only=False)` — 接受 `reduce_only` kwarg 转发给 exchange 的 reduce-only / close-only flag
- **Account library — 创建 `lib/account_{exchange}.py`:** 给某 exchange wire snapshot,copy `lib/account_TEMPLATE.py` 到 `lib/account_{exchange}.py`,实现 `get_equity(env)` 和 `get_positions(env)`。`snapshot.py` 按名 auto-discover — **不**改 `snapshot.py`。API keys 放 `.env`(如 `okx_api_key`, `okx_secret_key`, `okx_passphrase`)
- **`portfolio_config.json["messages"]`** — Telegram 消息模板 for reconciler and watchdog。Keys: `order_buy`, `order_sell`, `order_close_long`, `order_close_short`, `order_error`, `watchdog_started`, `watchdog_restart`。Placeholders: `{symbol}`, `{amount}`, `{error}`, `{code}`。部署时改匹配用户语言
- **`manager/snapshot.py`** — daily account equity snapshot。读 `portfolio_config.json["exchanges"]` 唯一 exchanges,auto-import `lib/account_{exchange}.py`,记到 `manager/snapshots.jsonl`,发 Telegram report。Cron: `0 8 * * * cd /root/.openclaw/workspace && python3 manager/snapshot.py`。**`cd` 必填** — cron 从 `/root` 跑,所有路径是 relative,没有 `cd` 每个 file open 静默 fail 在 Telegram 之前
- **永远用 watchdog wrapper 在 tmux session 启 reconciler**,不要直接,永不用 `nohup &`。`nohup &` background 进程在 shell session 结束时被杀。tmux 让进程跨 session 活:
  ```
  tmux new-session -d -s reconciler 'cd /root/.openclaw/workspace && bash manager/start_reconciler.sh'
  ```
  看状态: `tmux attach -t reconciler`。停: `tmux kill-session -t reconciler`
- **报 inconsistency 前 trace 完整计算链**。`state.json` 显示非零 position 但 `portfolio_config.json` 某字段(如 `weight=0`)看起来矛盾,先读 `lib/portfolio.py`。`contribution = account_value * leverage * weight * position` — 零 weight 按设计归零 contribution。**不**要先报 bug,先跟完所有变量聚合逻辑

---

## Response Style

- 响应简洁,Telegram-friendly
- 用 Telegram 支持的 markdown
- 数据表:短,或发图片
- 代码:clean,well-commented
