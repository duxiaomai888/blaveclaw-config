# BBAC-D: 市场状态规则量化系统

> **量化策略反向工程框架** — 50 条规则 + 自动参数扫描 + 跨周期验证 + 文档校准

## 🎯 项目目标

把"业务规则"(文档定义)→"实测验证"(回测数据)→"文档校准"(v4.x 附录)形成闭环,
让 50 条规则有**数据支撑**,不再靠业务假设。

## 📋 系统状态(2026-06-09)

| 模块 | 状态 | 备注 |
|---|---|---|
| 规则文档 v4.0 | ✅ | 50 条,见 `文档模板.md`(速查表已精简) |
| 规则结构化 catalog | ✅ | 50 条,见 `rules_catalog/` |
| 单币种回测框架 | ✅ | catalog-driven + 双方向 |
| 批量回测框架(统一入口) | ✅ | `core/run_batch.py` |
| 跨周期验证 | ✅ | 14-45 币种 × 2 周期 |
| 反向工程报告 v4.3 | ✅ | 见 `cache/v4.x_calibration.md` |
| 22 条跨周期稳健规则 | ✅ | Stab=100%,Sharpe>3 |
| ★★★ 强推 5 条 | ✅ | E04 / G05 / F03 / B04 / B01 |
| **币对筛选器 coin_screener** | ✅ | **`core/coin_screener.py`,8 keys,限流已配** |
| **10 个用户策略** | ✅ | **见 `strategies/`**(BTC/ETH/DOGE 共 5 个币 × 双向) |
| 实盘部署 | ⏸️ 测试阶段 | Telegram 未配对 |
| 止损/止盈 | ⏸️ 暂不用 | 用户决定 |

## 📁 项目结构

```
BBAC-D/
├── 文档模板.md                  50 条规则 v4.0 + v4.2 实测校准附录
│
├── core/                         ★ 核心入口(4 个 Python 脚本)
│   ├── single_symbol_backtest.py
│   ├── cross_period_analysis.py
│   ├── analyze_results.py
│   ├── run_batch.py             统一批量入口
│   └── README.md
│
├── # 规则定义
├── rules_catalog/               50 条结构化规则(catalog 源)
│
├── # 共享库
├── lib/                         共享数据/执行/PnL/通知库
├── manager/                     组合管理 + reconciler
│
├── # 参考
├── references/                  部署/策略/市场参考
├── examples/                    7 个参考策略
├── strategies/                  用户策略(3 个:btc_a05_short 等)
│
├── # 技能
├── skills/                      Blave API / 9 个交易所 / 台股
│
├── # 数据
├── cache/
│   ├── v4.2_calibration.md      ★ 实测校准报告
│   ├── csv/                     回测结果
│   └── *.parquet                Blave 原始数据
│
└── # 系统
    ├── AGENTS.md                Claude Agent 工作指令
    ├── CLAUDE.md                Claude Code 入口
    └── .env.example             环境变量模板
```

## 🚀 快速开始

### 1. 配置环境
```bash
cp .env.example .env
# 编辑 .env 填入 Blave API key
```

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

# 从文件读 500 币种
python core/run_batch.py --symbols-file symbols_500.txt

# 多周期跨周期验证
python core/run_batch.py --periods 90,180

# 增量跑(跳过已有 CSV)
python core/run_batch.py --incremental
```

### 4. 反向工程(校准文档)
```bash
python core/cross_period_analysis.py     # 跨周期统计
python core/analyze_results.py            # 出 v4.x 实测校准报告
# 报告 → cache/v4.x_calibration.md
# 文档 → 文档模板.md (v4.x 校准附录)
```

### 5. 币对筛选器(找候选埋伏币)
```bash
# 单规则,top 20 候选
python core/coin_screener.py --rules D01 --top 20

# 多规则 + 自适应阈值
python core/coin_screener.py --rules D01,E04,G05 --threshold adaptive --top 20

# 全市场 539 币 × 5-10 分钟
python core/coin_screener.py --rules D01,E04,G05 --top 20
# 详细:references/coin_screener.md
```

### 6. 跑已落盘策略(任一已落盘的 strategy/)
```bash
# 跑策略 + 自动写 stats.json + pnl.png
python strategies/<name>/strategy.py

# 三层验证(IS/OOS/MCPT)
python core/validate_strategy.py strategies/<name>
# → validation.json
```

> `<name>` 替换为你的策略目录名,如 `btc_a05_short` / `btc_ti_hc_wh_1h_long` 等。
> 当前已落盘 3 个,见 `strategies/` 目录。

## 🛠️ 详细文档

- `AGENTS.md` — Claude Agent 工作指令(系统提示,带 TOC + 🔴 标记)
- `文档模板.md` — 50 条规则 + v4.2 实测校准(速查表已精简)
- `core/README.md` — 核心入口用法
- `references/` — 部署/策略/TW 股票/**coin_screener** 参考
- `cache/v4.2_calibration.md` — 最新实测校准报告
