---
name: bbac-rules
description: "Use for: scanning the crypto market to see which of 59 structured trading rules are triggering right now, and checking one coin's current signal. Trigger phrases: 扫描 / 掃描, 哪些币在触发, 此刻有信号, BTC 现在有什么信号, 帮我扫一下, coin_screener, last_hit, trigger_rate, 规则扫描, 批量扫描, 全市场 539 币, 40 midcap 币池, direction_best, direction_doc, 校准方向, 方向翻转, 静默失效, cond_builder. (1) Rule catalogue — 59 rules over 13 categories A-M (主力动作 / 动能确认 / 动量突破 / 主力vs散户 / 板块联动 / 价格确认 / 极端反转 / 爆仓 / 连续模式 / 跨币种联动 / 量价配合 / 跨指标背离 / 动量反转), each a cond_builder (df, params) -> bool Series on Blave alpha indicators HC holder concentration / WH whale hunter / TI taker intensity / MS market sentiment / LM liquidation / SM squeeze momentum + OHLCV + Volume; 45 original + 9 new K/L/M + 5 skip. (2) Direction calibration — 24 of 58 backtested rules flipped (19 A-J, 5 K/L/M small-sample), ~41% of backtested rules go against their document direction, so scans default to direction_best, never direction_doc; K/L/M were calibrated on 1-7 coins each (low confidence), M01 has no backtest data and falls back to direction_doc. (3) Cross-period stability — 13 rules are Stab=100% (positive Sharpe in both 90d and 180d on a 50-coin pool); strong picks B04 TI 动能加速 (Sharpe 6.45 / 3.84) and B01 顺势做多动能 (4.61 / 3.52), both document == backtest. (4) Scanning — core/coin_screener.py: batch scans over any coin pool, 7 serial requests per coin, per-key rate limiting, batch cooldown, adaptive thresholds, dead-symbol skip, and an explicit end-of-run report naming any rule that returned None on every coin (a missing derived column silently disables a rule — this caught a 31-of-45 rules silent-failure bug). fetch_coin_data is the only correct data entry and must stay aligned with the backtest side's single_symbol_backtest.load_data. (5) Single source of truth for rule code is rules_catalog/catalog.py — this skill's markdown is a map, not the source. Runtime needs only .env keys blave_api_key / blave_secret_key (supports _key2.._key8 rotation); the blave-quant skill is optional and used only for indicator interpretation docs, not for data. This skill does NOT place orders — it scans and reports signals for the user to act on."
version: 1.1.0
metadata:
  openclaw:
    emoji: "📡"
    requires:
      env:
        - blave_api_key
        - blave_secret_key
    optional:
      skill:
        - blave-quant
      env:
        - blave_api_key2
        - blave_secret_key2
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

**约 41% 已回测规则的文档方向与实测方向相反（24/58：19 条 A–J + 5 条 K/L/M 小样本）。** 如果直接用 `direction_doc` 交易，这些规则会亏钱。

扫描时 **必须用 `direction_best`**（校准后的方向），不要用 `direction_doc`。

24 条翻转规则见 `references/rules-catalog.md` › 方向校准表。

> **⚠️ K/L/M 新规则校准状态**：K/L/M 9 条规则中 8 条已回测校准
> （M01 因触发不足 min_trades 无回测数据，`direction_best` 回退到 `direction_doc`）。
> K/L/M 的校准样本较小（每规则 1–7 个币种），置信度低于 A–J。
> 扫描可用，但方向置信度请对照 `references/rules-catalog.md` 的「🆕 未回测/小样本」标注。

## 规则类别总览

| 类别 | 维度 | 条数 | 说明 |
|---|---|---|---|
| A | 主力动作 | 8 | HC + WH 判断主力开/平仓 |
| B | 动能确认 | 6 | TI 确认多空动能 |
| C | 动量突破 | 5 | SM 挤压动量突破 |
| D | 主力 vs 散户 | 4 | HC + MS 主力散户背离 |
| E | 板块联动 | 6 | MS 板块情绪联动（2 条 skip：E05/E06） |
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
- `.env` 中有 `blave_api_key` / `blave_secret_key`（支持 `_key2 .. _key8` 多 key 轮询）
- **数据层是自包含的**：`fetch_coin_data` → `lib.data.http.get_all_headers()` 直接读 `.env` 调 Blave API，
  不经过 `blave-quant` skill。`blave-quant` 仅在需要指标含义解读时可选（见它的
  `references/blave-indicator-guide.md`）；没装也不影响扫描。
- BBAC-D 项目根目录可访问 `core/coin_screener.py` + `rules_catalog/catalog.py`

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

1. 用 `core/coin_screener.py` 的 `fetch_coin_data` 一次性拉齐 kline + 6 个 alpha + 全部派生列
2. 对每条规则运行 `cond_builder` 检查 `last_hit`
3. 报告触发的规则 + 校准方向

> **不要手写数据组装**：`fetch_coin_data` 是唯一正确的数据入口，
> 它与回测侧 `single_symbol_backtest.load_data` 的列名/派生逻辑保持一致。
> 手拼会缺列（如 `abs_HC`/`hc_delta`/`new_high_24h`），导致依赖这些列的规则静默失效。

```python
# 可运行的最小示例（在 BBAC-D 项目根目录下执行:
#   python -c "import core._bootstrap"  # 或设 PYTHONPATH=.
import core._bootstrap  # noqa: F401  — sys.path setup,使 from core.* / rules_catalog.* 可用
from datetime import datetime, timedelta
from lib.data import get_all_headers
from core.coin_screener import fetch_coin_data
from rules_catalog.catalog import ALL_RULES, resolve_param_space

symbol = 'BTCUSDT'
end   = datetime.now().strftime('%Y-%m-%d')
start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
hdrs  = get_all_headers()[0]   # 取第一组 key(支持多 key 轮询)

df = fetch_coin_data(symbol, start, end, hdrs)  # kline + HC/WH/TI/MS/LM/SM + 派生列
if df is None or len(df) == 0:
    print(f'{symbol}: 无数据(死币或网络)'); raise SystemExit

hits = []
for rule in ALL_RULES:
    if rule.get('skip'): continue
    params = resolve_param_space(rule, df)      # 参数空间 → 取默认组合
    # resolve_param_space 返回 {name: [候选]}, 取每个的默认(第一个)值
    p = {k: v[0] if isinstance(v, list) else v for k, v in params.items()}
    cond = rule['cond_builder'](df, p)
    if bool(cond.iloc[-1]):   # last_hit:最后一根已收盘 bar 触发
        hits.append((rule['id'], rule['name_cn'], rule['direction_best']))

if hits:
    print(f'{symbol} 末根触发:')
    for rid, name, d in hits:
        print(f'  {rid} {name} → {d}')
else:
    print(f'{symbol} 末根无规则触发')
```

> 参数解析规则见 `core/coin_screener.py` 的 `resolve_param_space`；
> 上例取每个参数的默认值(第一个候选),与批量扫描的参数搜索不同(批量会扫整个空间取最优)。

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

> **⚠️ 样本很小**：跨周期配对每条规则只有 1–3 个币种（`cache/csv/cross_period_rule_summary.csv`
> 的 `n_symbols`）。这条结论说的是「方向没翻转」，不是「样本充足」。
> A–J 单周期校准的币种数也是 1–16（不是几十），引用时请带上这个数字。

## 参考

- `references/rules-catalog.md` — 59 条规则完整定义 + 方向校准表
- `references/scanning-guide.md` — 扫描操作指南 + 参数解析 + 输出读法
- Blave 指标解读见 `blave-quant` skill 的 `references/blave-indicator-guide.md`(不重复造)
- BBAC-D 项目 `rules_catalog/catalog.py` — 规则代码（唯一真理源）
- BBAC-D 项目 `cache/v4.4_calibration.md` — 实测校准报告
