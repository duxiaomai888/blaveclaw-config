---
name: bbac-rules
description: "Use for: scanning the crypto market with 59 structured trading rules across 13 categories (A-M). Each rule defines a precise market condition using Blave alpha indicators (Holder Concentration, Taker Intensity, Whale Hunter, Market Sentiment, Liquidation, Squeeze Momentum) + OHLCV price action + Volume. The skill covers: (1) Rule definitions — 59 rules with cond_builder logic, document direction (direction_doc) and calibrated direction (direction_best, from cross-period backtest of 50 coins); (2) Market scanning — scan any coin pool (40 midcap or 539 full market) for which rules are currently triggering (last_hit); (3) Direction calibration — 19 of 38 backtested rules had flipped directions (document said long, backtest said short), so the skill uses calibrated directions by default; (4) Cross-period stability — 13 rules are Stab=100% (both 90d and 180d positive Sharpe); (5) Strong recommendations — 2 rules (B04 TI 动能加速, B01 顺势做多动能) with dual-period Sharpe ≥ 3.5. Works with the blave-quant skill for data fetching. The skill does NOT place orders — it scans and reports signals for the user to act on."
version: 1.0.0
metadata:
  openclaw:
    emoji: "📡"
    requires:
      skill:
        - blave-quant
    optional:
      env:
        - blave_api_key
        - blave_secret_key
---

# BBAC-Rules: 59 条规则市场扫描技能

## 核心能力

本技能包含 **59 条结构化交易规则**（45 原始活跃 + 9 新增 + 5 skip），覆盖 13 个维度，
用 Blave alpha 指标（HC/WH/TI/MS/LM/SM）+ OHLCV + Volume 定义精确的入场条件。
每条规则有：

| 字段 | 含义 |
|---|---|
| `id` | 规则编号（A01–M03） |
| `category` | 类别（A–M） |
| `direction_doc` | 文档方向（业务逻辑推演的方向） |
| `direction_best` | 实测校准方向（回测验证后的最优方向） |
| `cond_builder` | 条件构建函数 `(df, params) → bool Series` |

## ⚠️ 方向校准（最重要）

**50% 规则的文档方向与实测方向相反。** 如果直接用 `direction_doc` 交易，一半规则会亏钱。

扫描时 **必须用 `direction_best`**（校准后的方向），不要用 `direction_doc`。

19 条翻转规则见 `references/rules-catalog.md` › 方向校准表。

## 规则类别总览

| 类别 | 维度 | 条数 | 说明 |
|---|---|---|---|
| A | 主力动作 | 8 | HC + WH 判断主力开/平仓 |
| B | 动能确认 | 6 | TI 确认多空动能 |
| C | 动量突破 | 5 | SM 挤压动量突破 |
| D | 主力 vs 散户 | 4 | HC + MS 主力散户背离 |
| E | 板块联动 | 6 | MS 板块情绪联动（3 条 skip） |
| F | 价格确认 | 6 | 价格行为确认信号 |
| G | 极端反转 | 5 | 极端筹码/波动反转 |
| H | 爆仓 | 4 | LM 爆仓极端信号 |
| I | 连续模式 | 3 | K 线连续形态 |
| J | 跨币种联动 | 3 | BTC/板块联动（全 skip） |
| K | 量价配合 | 3 | Volume 成交量维度（新增） |
| L | 跨指标背离 | 3 | 多指标背离揭示隐藏派发/吸筹（新增） |
| M | 动量反转 | 3 | 均值回归/反转模式（新增） |

## 扫描流程

### 前置条件
- 需要 `blave-quant` skill 提供 Blave API 数据
- 需要 `.env` 中的 `blave_api_key` / `blave_secret_key`

### 方法 1：用 core/coin_screener.py（完整批量扫描）

```bash
# 扫描 40 币 × 指定规则
python core/coin_screener.py --rules D01,F01,A08 --top 20

# 扫描全市场 539 币
python core/coin_screener.py --rules D01,F01,A08 --coins symbols.xlsx --top 20

# 用校准方向扫描（默认就用 direction_best）
python core/coin_screener.py --rules B04,B01 --direction both --top 20
```

### 方法 2：Agent 直接调用（快速单币扫描）

当用户问「BTC 现在有什么信号」时，agent 可以：

1. 用 `blave-quant` skill 获取该币的 kline + alpha 数据
2. 对每条规则运行 `cond_builder` 检查 `last_hit`
3. 报告触发的规则 + 校准方向

```python
# 伪代码
from rules_catalog.catalog import ALL_RULES
from lib.data import fetch_kline, fetch_holder_concentration, ...

df = fetch_kline(symbol, '1h', start, end, hdrs)
df = add_all_alpha(df, hdrs)  # HC, WH, TI, MS, LM, SM
df = add_derived_cols(df)      # hc_delta, ret_1h, new_high_24h, ...

for rule in ALL_RULES:
    if rule.get('skip'): continue
    params = resolve_param_space(rule, df)
    cond = rule['cond_builder'](df, params)
    if cond.iloc[-1]:  # last_hit
        print(f"{rule['id']} {rule['name_cn']} → {rule['direction_best']}")
```

## 输出格式

扫描结果应报告：

| 规则 | 名称 | 触发币种 | 校准方向 | 文档方向 | 是否翻转 |
|---|---|---|---|---|---|
| B04 | TI 动能加速 | BTCUSDT, ETHUSDT | long | long | ✅ 一致 |
| D02 | 主力派发 | SOLUSDT | long | short | ⚠️ 翻转 |

## ★★★ 强推规则（双周期 Sharpe ≥ 3.5）

| 规则 | 名称 | 90d Sharpe | 180d Sharpe | 方向 |
|---|---|---|---|---|
| B04 | TI 动能加速 | 6.45 | 3.84 | long |
| B01 | 顺势做多动能 | 4.61 | 3.52 | long |

这两条文档方向与实测一致，可直接按文档方向使用。

## 13 条跨周期稳健规则（Stab=100%）

A08, B01, B04, B06, D01, D02, E01, E02, E04, F01, F06, G02, I02

这些规则在 90d 和 180d 回测中都是正 Sharpe，方向一致。

## 参考

- `references/rules-catalog.md` — 59 条规则完整定义 + 方向校准表
- `references/scanning-guide.md` — 扫描操作指南 + 参数解析 + 输出读法
- Blave 指标解读见 `blave-quant` skill 的 `references/blave-indicator-guide.md`(不重复造)
- BBAC-D 项目 `rules_catalog/catalog.py` — 规则代码（唯一真理源）
- BBAC-D 项目 `cache/v4.4_calibration.md` — 实测校准报告
