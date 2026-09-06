# Core 核心入口

> 本目录是 BBAC-D 的 **7 个核心 Python 入口**(下表)。另有 ~10 个遗留/绘图脚本
> (`chan_plot*.py`、`run_all.py`、`fetch_*.py`、`workflow_btcusdt.py` 等),
> 从项目根目录调用,自动处理 sys.path。

## 7 个入口

| 脚本 | 用途 | 用法 |
|---|---|---|
| `single_symbol_backtest.py` | 单币种回测,带方向校准 | `python core/single_symbol_backtest.py BTCUSDT --days 90 --top 10` |
| `run_batch.py` | ★ 统一批量入口 | `python core/run_batch.py --symbols BTCUSDT,ETHUSDT` |
| `cross_period_analysis.py` | 跨周期配对统计 | `python core/cross_period_analysis.py` |
| `analyze_results.py` | 反向工程 → 出 v4.4 报告 | `python core/analyze_results.py` |
| **`coin_screener.py`** | **批量币种筛选(找候选)** | **`python core/coin_screener.py --rules D01 --top 20`** |
| `validate_strategy.py` | 策略三层验证(IS/OOS/MCPT) | `python core/validate_strategy.py strategies/<name>` |
| `update_doc_v43.py` | 报告写回文档(幂等) | `python core/update_doc_v43.py` |

## 工作流

```
阶段 1 — 找候选:
  coin_screener.py            (扫 539 币 × 多规则,找 top 候选)
       ↓
阶段 2 — 验证候选:
  single_symbol_backtest.py   (单币种精细回测,带双方向)
       ↓
  run_batch.py                (多币种批量)
       ↓
  cache/csv/{sym}_{days}d.csv
       ↓
阶段 3 — 落盘:
  strategies/{name}/strategy.py  (照抄 eth_a08_short 模板)
       ↓
阶段 4 — 验证:
  validate_strategy.py        (3 层验证 → validation.json)
       ↓
阶段 5 — 校准文档:
  cross_period_analysis.py    (跨周期统计)
       ↓
  analyze_results.py          (出 v4.4 实测校准报告)
       ↓
  cache/v4.4_calibration.md
       ↓
  update_doc_v43.py           (幂等写回 文档模板.md 校准附录章节)
```

> 阶段 5 依赖阶段 2 的产物:`cache/csv/batch_50_summary.csv`(90d 合并)
> 和 `cache/csv/*_180d.csv`(跨周期配对)。这两个被清掉就得重跑 `run_batch.py`,
> `cross_period_analysis.py` / `analyze_results.py` 没有输入。

## coin_screener 限流(2026-06 新增)

8 keys × 2.0 req/s = 16 req/s 默认。**详细见 `references/coin_screener.md`。**

| 参数 | 默认 | 说明 |
|---|---|---|
| `--rps-per-key` | 2.0 | 每 key 每秒请求数 |
| `--batch-size` | 100 | 每批多少币 |
| `--batch-sleep` | 5.0 | 批间 sleep |
| `--no-batch` | — | 不分批 |

```bash
# 5 币 smoke test
python core/coin_screener.py --rules D01 --coins test5.txt --top 5

# 全市场 539 币
python core/coin_screener.py --rules D01,E04,G05 --top 20
```

## 路径处理

每个脚本头部都加了:
```python
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
```

→ 在任何目录下跑 `python core/xxx.py` 都能 import `lib.*` 和 `rules_catalog.*`。
