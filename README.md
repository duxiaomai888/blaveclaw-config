# BBAC-D: 市场状态规则量化系统

> **量化策略反向工程框架** — 59 条规则 + 自动参数扫描 + 跨周期验证 + 文档校准

## 🎯 项目目标

把"业务规则"(文档定义)→"实测验证"(回测数据)→"文档校准"(v4.4 附录)形成闭环,
让 59 条规则有**数据支撑**,不再靠业务假设。

## 📋 系统状态(2026-09-12)

| 模块 | 状态 | 备注 |
|---|---|---|
| 规则文档 v4.5 | ✅ | 59 条(A-J 原始 50 + K-M 新增 9;54 active + 5 skip),见 `文档模板.md`(v4.5:K/L/M 定义补全,标题/统计修正) |
| 规则结构化 catalog | ✅ | 59 条(54 active,5 skip),见 `rules_catalog/catalog.py` |
| 单币种回测框架 | ✅ | catalog-driven + 双方向 + 死币跳过 |
| 批量回测框架(统一入口) | ✅ | `core/run_batch.py`,增量/多周期 |
| 回测执行口径 | ✅ | 明文契约:下一根开仓 / 收盘价成交(上界) / 参数锁定,见 `lib/analysis.py` › `backtest()` |
| 跨周期验证 | ✅ | 36 币(90d)+ 14 币(180d) |
| 反向工程报告 v4.4 | ✅ | 见 `cache/v4.4_calibration.md` |
| 跨周期稳健规则 | ✅ | **13 条 Stab=100%**(2026-09-12 重建) |
| ★★★ 强推 | ✅ | **2 条**(报告按当前数据动态生成) |
| **币对筛选器 coin_screener** | ✅ | `core/coin_screener.py`,9 keys 轮询 + 分 key 限流(安全 1.67 req/s/key) |
| **用户策略** | ❌ 已清空 | 既有策略已全部删除(2026-10-01);`strategies/` 现仅剩 `TEMPLATE_A/C.py`,新策略从模板新建 |
| 三层验证 | ✅ | IS/OOS/MCPT,`core/validate_strategy.py` |
| 实盘部署 | ⛔ 无在跑策略 | `manager/portfolio_config.json` 权重已清空 |
| 止损/止盈 | ⏸️ 暂不用 | 用户决定 |

### 策略现状

既有用户策略已全部删除(2026-10-01),`strategies/` 下无任何策略。历史回测结果保留在 `archive/backups/strategies_backup_2026-09-03/`。

## 📁 项目结构

```
```
BBAC-D/
├── 文档模板.md                  59 条规则 v4.0 + v4.4 实测校准附录(自动生成)
├── VERSION                      当前版本标记(版本号单一来源)
├── AGENTS.md                    给 AI agent 的项目操作说明
├── ARCHITECTURE.md              ★ 项目架构总览(分层图/数据流/模块清单/关键契约)
├── CHANGELOG.md                 版本变更历史(Keep a Changelog 格式)
├── LICENSE                      MIT 许可证
├── auth_service.py              独立鉴权微服务(token/session);仅旧 openclaw 运行时启动,
│                                Blave Agent 运行时不运行它,不属于主系统
│
├── core/                         ★ 9 个主入口(下),其余为辅助/绘图/治理脚本
│   ├── single_symbol_backtest.py 单币种回测(catalog 驱动)
│   ├── run_batch.py             统一批量入口(--workers 并行)
│   ├── cross_period_analysis.py 跨周期统计
│   ├── analyze_results.py       出 v4.4 校准报告
│   ├── update_doc_v43.py        报告写回 文档模板.md(幂等)
│   ├── coin_screener.py         批量币种筛选
│   ├── validate_strategy.py     三层验证(IS/OOS/MCPT)
│   ├── prune_cache.py           cache 治理(默认干跑,--apply 才动手)
│   └── calibrate_directions.py  从批量回测投票方向,回写 catalog 的 direction_best
│
├── rules_catalog/               59 条结构化规则(catalog 唯一真理源)
├── symbols.xlsx                 币池(539 币)
├── midcap_symbols.csv           币池(40 币,coin_screener 默认源)
│
├── lib/                         共享库:数据/执行/PnL/扫描/验证/通知/报告(详见 ARCHITECTURE.md)
├── manager/                     组合管理 + reconciler(详见 manager/README.md)
├── allocators/                  自定义权重分配器
│
├── examples/                    独立参考策略(btc/台股/台指期,写同类前先读)
├── strategies/                  用户策略 + TEMPLATE_A/C
├── apps/vote_dashboard/         vote 面板(Flask + desktop,独立于 strategies/)
├── skills/                      blave-quant(Blave API/交易所/台股) + bbac-rules(59 条规则扫描)
├── references/                  部署/策略/市场参考文档
├── tests/                       check_*.py 自包含校验(CI 全跑,无需网络)
├── vendor/chan/                 缠论框架(内嵌,chan_plot 用)
│
├── state/                       运行时状态(HALT/heartbeat/audit/deployments.json/notes/)
│
├── cache/
│   ├── v4.4_calibration.md      ★ 实测校准报告(analyze_results 产物)
│   ├── csv/                     回测结果(平铺布局是硬编码契约,勿移动文件)
│   └── *.parquet                Blave 原始数据缓存
│
├── archive/                     已归档,不参与运行:
│   ├── live_eval/               实时行情评估系统(自带 core/ + 形态识别 + 综合打分;
│   │                            方法学不同于主系统,未合并)
│   └── backups/                 旧策略快照(仅 stats/validation.json,无 strategy.py)
│
├── requirements.in              依赖宽松声明(改依赖只动这里)
├── requirements.lock            3.12 hash 锁定快照(uv pip compile 生成)
└── .github/workflows/ci.yml     CI(check_*.py + ruff,lock 按 Python 版本选)
```
```

## 🚀 快速开始

### 1. 配置环境
```bash
uv venv --python 3.12 .venv
# requirements.lock 是带 hash 锁定的依赖快照(可复现);requirements.in 是宽松声明
uv pip install --python .venv -r requirements.lock

cp .env.example .env
# 编辑 .env 填入 Blave API key(支持 blave_api_key / _key2 .. _key20;安全 1.67 req/s/key)
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
- `ARCHITECTURE.md` — ★ 项目架构总览(分层图/数据流/模块清单/关键契约)
- `文档模板.md` — 59 条规则 + v4.4 实测校准附录
- `core/README.md` — 核心入口用法与限流参数
- `manager/README.md` — 组合管理 10 个脚本用法与协作架构
- `references/` — 部署/策略/TW 股票/**coin_screener** 参考
- `cache/v4.4_calibration.md` — 最新实测校准报告
