# live_eval/ 架构(现状 2026-10-02)

庄家(主力)系统 —— 实时行情评估,识别庄家操盘手法(建仓/试盘/洗盘/拉升/出货等),
输出 15 段 markdown 评估报告。基于《庄家克星》方法学。

## 位置与定位

- 顶层 `live_eval/`(2026-10-02 从 `archive/` 移出,作为在建一等包)。
- 自包含:只 import 标准库 + 第三方(requests/pandas/numpy/matplotlib)+ 自己的 `core/`。
  **设计上不依赖 `lib/`**(见 `core/config.py` 文档串)。未来嫁接 `lib.data` 时,替换
  `core/blave_client.py` + `core/binance_client.py` 为 `lib.data` 的 fetcher 即可。

## 已建

### core/ (8 模块)
- `config.py` — 常量/路径/钥匙/时区(`BLAVE_BASE_URL`、`BINANCE_*`、`PROJECT_ROOT`、
  `DATA/REPORTS/TEMPLATES_DIR`、`BJ_TZ`、`to_bj`、`load_blave_keys`)
- `blave_client.py` — Blave client:`fetch_kline`/`fetch_alpha_series`/`fetch_alpha_table`/
  `fetch_liquidation_map`/`fetch_7tier_kline`/`fetch_7dim_alpha`/`HDRS`/`TIERS`
- `binance_client.py` — Binance 公共 client:`fetch_klines`/`fetch_agg_trades`/
  `fetch_funding_rate`/`fetch_open_interest`/`fetch_long_short_ratio`/
  `fetch_top_trader_ratio`/`fetch_ticker_24h`/`INTERVAL_MAP`
- `indicators.py` — 量价指标:`calc_obv`/`calc_ad_line`/`calc_taker_buy_ratio`/
  `calc_vwap`/`calc_volume_trend`
- `patterns.py` — 6 经典形态:`detect_patterns`(头肩/双顶/三角/旗/楔/通道)
- `volume_profile.py` — 筹码集中度:`build_volume_profile`(POC/Value Area/HVN/LVN)
- `breakdown.py` — 突破/跌破 + 量能确认:`detect_breakdown`
- `scoring.py` — 综合评分:`compute_total_score`(alpha + 形态 + 突破 + 量价 + funding)

### scripts/
- `realtime_eval.py` — 入口:`python scripts/realtime_eval.py BTCUSDT` → 取数 → 算 →
  出 15 段 markdown 报告到 `reports/{SYMBOL}_{YYYYMMDD_HHMMSS_BJ}.md`

### zhuangjia_poc/
- `zhuangjia_btc_poc.py` — BTC POC(自包含,matplotlib,出 dashboard png + signals parquet)

### 其他
- `reports/` — 生成的报告(2 份 BTC,2026-06-16/17)
- `patterns.md`、`zhuangjia-summary.md` — 形态说明 + 庄家手法研究笔记

## 提议未建(待「做好」阶段补)

- `core/data_normalizer.py` — 统一两源数据格式
- `scripts/backtest.py` — 1y 回测(每根 K线模拟)
- `scripts/pattern_scanner.py` — 扫描所有币种找形态
- `scripts/update_docs.py` — 文档同步
- `data/`(score_history/、backtest/)、`templates/`、`README.md`、`FINAL_OVERVIEW.md`

## realtime_eval 报告段实现状态

15 段提议,当前实现 0-8 / 12 / 13 / 15;**9-11 / 14 待建**:
- 0 header、1 当前价、2 7档价格矩阵、3 形态、4 Volume Profile、5 量价指标、
  6 7维alpha×7档矩阵、7 Funding+Liquidation、8 关键支撑/阻力 — ✅
- 9 跨期变化、10 爆仓地图、11 历史趋势 — ⏳ 待建
- 12 综合评分、13 开多/开空实战分析 — ✅
- 14 决策对比 — ⏳ 待建
- 15 复盘位 — ✅

## 数据流

```
realtime_eval.py
  → core.config (钥匙/路径/时区)
  → core.blave_client (Blave: 7档kline + 7维alpha + liquidation + alpha_table)
  → core.binance_client (Binance: 1d OHLCV + ticker)
  → core.indicators / patterns / volume_profile / breakdown (计算)
  → core.scoring (综合评分)
  → reports/{SYMBOL}_{ts}_BJ.md
```
