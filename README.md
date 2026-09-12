# BBAC-D: 市场状态规则量化系统

> **量化策略反向工程框架** — 50 条规则 + 自动参数扫描 + 跨周期验证 + 文档校准

## 🎯 项目目标

把"业务规则"(文档定义)→"实测验证"(回测数据)→"文档校准"(v4.4 附录)形成闭环,
让 50 条规则有**数据支撑**,不再靠业务假设。

## 📋 系统状态(2026-09-06)

| 模块 | 状态 | 备注 |
|---|---|---|
| 规则文档 v4.0 | ✅ | 50 条,见 `文档模板.md`(v4.4:口径明文,版本号统一) |
| 规则结构化 catalog | ✅ | 50 条(45 active),见 `rules_catalog/catalog.py` |
| 单币种回测框架 | ✅ | catalog-driven + 双方向 + 死币跳过 |
| 批量回测框架(统一入口) | ✅ | `core/run_batch.py`,增量/多周期 |
| 回测执行口径 | ✅ | 明文契约:下一根开仓 / 收盘价成交(上界) / 参数锁定,见 `lib/analysis.py` › `backtest()` |
| 跨周期验证 | ✅ | 36 币(90d)+ 14 币(180d) |
| 反向工程报告 v4.4 | ✅ | 见 `cache/v4.4_calibration.md` |
| 跨周期稳健规则 | ✅ | **13 条 Stab=100%**(2026-09-06 重建) |
| ★★★ 强推 | ✅ | **2 条**(报告按当前数据动态生成) |
| **币对筛选器 coin_screener** | ✅ | `core/coin_screener.py`,8 keys 轮询 + 分 key 限流 |
| **用户策略** | ✅ | `strategies/`:4 个已落盘 + `apps/vote_dashboard/`(桌面投票仪表盘) |
| 三层验证 | ✅ | IS/OOS/MCPT,`core/validate_strategy.py` |
| 实盘部署 | ⏸️ 测试阶段 | 组合配置在 `manager/portfolio_config.json` |
| 止损/止盈 | ⏸️ 暂不用 | 用户决定 |

### 策略现状

| 策略 | 符号 | IS Sharpe | OOS Sharpe | MCPT p | 结论 |
|---|---|---|---|---|---|
| `btc_a05_short` | BTCUSDT 1h | 1.40 | — | — | ✅ 通过,在组合内(74.6%) |
| `eth_a08_short` | ETHUSDT 1h | 0.65 | — | — | ✅ 通过,在组合内(18.6%) |
| `btc_ti_hc_wh_1h_long` | BTCUSDT 1h | 1.42 | — | — | ⏸️ 未跑三层验证 |
| `btc_hc_2side_1h` | BTCUSDT 1h | **1.02** | **0.63** | 0.003 | ❌ **IS+OOS FAIL,不可部署** |

## 📁 项目结构

```
BBAC-D/
├── 文档模板.md                  50 条规则 v4.0 + v4.4 实测校准附录(自动生成)
├── VERSION                      当前版本标记
│
├── core/                         ★ 7 个主入口 + 10 个遗留/绘图脚本
│   ├── single_symbol_backtest.py 单币种回测(catalog 驱动)
│   ├── run_batch.py             统一批量入口
│   ├── cross_period_analysis.py 跨周期统计
│   ├── analyze_results.py       出 v4.4 校准报告
│   ├── update_doc_v43.py        报告写回 文档模板.md(幂等)
│   ├── coin_screener.py         批量币种筛选
│   └── validate_strategy.py     三层验证(IS/OOS/MCPT)
│
├── rules_catalog/               50 条结构化规则(catalog 唯一真理源)
├── symbols.xlsx                 币池(539 币)
├── midcap_symbols.csv           币池(40 币,coin_screener 默认源)
│
├── lib/                         共享库:数据/执行/PnL/扫描/验证/通知/报告
├── manager/                     组合管理 + reconciler
├── allocators/                  自定义权重分配器
│
├── rules_catalog/               50 条结构化规则(catalog 唯一真理源)
├── examples/                    ─┐ 参考策略(Type A/C)
├── strategies/                  │ 用户策略 + TEMPLATE_A/C
├── skills/                      │ Blave API / 交易所 / 台股
├── references/                  │ 部署/策略/市场参考文档
└── vendor/chan/                 ┘ 缠论框架(内嵌,chan_plot 用)
│
├── cache/
│   ├── v4.4_calibration.md      ★ 实测校准报告(analyze_results 产物)
│   ├── csv/                     回测结果(batch_*_summary.csv 等)
│   └── *.parquet                Blave 原始数据缓存
│
└── .github/workflows/ci.yml     CI(跑 tests/check_*.py)
```

## 🚀 快速开始

### 1. 配置环境
```bash
uv venv --python 3.12 .venv
# requirements.lock 是带 hash 锁定的依赖快照(可复现);requirements.in 是宽松声明
uv pip install --python .venv -r requirements.lock

cp .env.example .env
# 编辑 .env 填入 Blave API key(支持 blave_api_key / _key2 .. _key8)
```

> **Windows 必须**:设系统环境变量 `PYTHONUTF8=1`
> (否则中文文件读写、`tests/check_*` 会因 GBK 编码崩溃)。
> Linux/Mac 无需设置。

### 2. 单币种回测
```bash
python core/single_symbol_backtest.py ETHUSDT --days 90 --hold 12 --top 10
```

### 3. 多币种批量(改币种即可跑)
```bash
# 默认 50 币种 90d
python core/run_batch.py

# 任意币种
python core/run_batch.py --symbols BTCUSDT,ETHUSDT,SOLUSDT

# 从文件读币池(自动跳过表头与非 USDT 行)
python core/run_batch.py --symbols-file midcap_symbols.csv

# 多周期跨周期验证
python core/run_batch.py --periods 90,180

# 增量跑(跳过已有 CSV)
python core/run_batch.py --incremental
```

> 改名/下市的币(MATICUSDT、RNDRUSDT 等)在 Blave 端返回 400,
> 会自动跳过并在结尾汇总,不会中断批量。

### 4. 反向工程(校准文档)
```bash
python core/cross_period_analysis.py   # 跨周期统计 → cross_period_rule_summary.csv
python core/analyze_results.py         # 出 v4.4 校准报告 → cache/v4.4_calibration.md
python core/update_doc_v43.py          # 写回 文档模板.md(可重复运行,幂等)
```

### 5. 币对筛选器(找候选埋伏币)
```bash
# 单规则,top 20 候选
python core/coin_screener.py --rules D01 --top 20

# 多规则 + 自适应阈值
python core/coin_screener.py --rules D01,E04,G05 --threshold adaptive --top 20

# 全市场(symbols.xlsx 539 币)
python core/coin_screener.py --rules D01,E04,G05 --top 20
# 详细:references/coin_screener.md
```

### 6. 跑已落盘策略
```bash
# 跑策略 + 自动写 stats.json + pnl.png
python strategies/<name>/strategy.py

# 三层验证(IS/OOS/MCPT)→ validation.json
python core/validate_strategy.py strategies/<name>
```

> 三层阈值:IS Sharpe > 2.0、OOS Sharpe 衰减 < 30%、MCPT p < 0.05。
> 任一层 FAIL 就不可部署。`END = None` 的活策略按"数据到今天"处理。

## 🛠️ 详细文档

- `AGENTS.md` — Claude Agent 工作指令(系统提示,带 TOC + 🔴 标记)
- `文档模板.md` — 50 条规则 + v4.4 实测校准附录
- `core/README.md` — 核心入口用法与限流参数
- `references/` — 部署/策略/TW 股票/**coin_screener** 参考
- `cache/v4.4_calibration.md` — 最新实测校准报告
