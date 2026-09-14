# 扫描操作指南

`bbac-rules` 技能本身不下单 —— 它扫描市场,报告"此刻哪些规则在哪些币上触发",供用户决策。
扫描由 BBAC-D 项目的 `core/coin_screener.py` 执行;技能包只定义规则 + 读结果。

## 前置条件

1. `blave-quant` skill 已装(提供 Blave API 数据层)
2. `.env` 有 `blave_api_key` / `blave_secret_key`(支持 `_key2 .. _key8` 多 key 轮询)
3. BBAC-D 项目根目录可访问 `core/coin_screener.py` + `rules_catalog/catalog.py`

## 命令

```bash
# 扫描 40 币(midcap)× 指定规则,看此刻触发
python core/coin_screener.py --rules D01,F01,A08 --top 20

# 扫描全市场 539 币
python core/coin_screener.py --rules D01,F01,A08 --coins symbols.xlsx --top 20

# 校准方向(默认就用 direction_best,无需额外参数)
python core/coin_screener.py --rules B04,B01 --direction both --top 20

# 自适应阈值(每币按自身 p90,公平对待小币)
python core/coin_screener.py --rules D01 --threshold adaptive --top 20

# 回看窗口 / 并发 / 限流
python core/coin_screener.py --rules D01 --days 60 --workers 20 --top 30
python core/coin_screener.py --rules D01 --rps-per-key 1.5 --batch-size 50 --batch-sleep 8
```

## 参数解析

| 参数 | 默认 | 说明 |
|---|---|---|
| `--rules` | (必填) | 逗号分隔规则 ID,如 `D01,F01,A08` |
| `--coins` | `symbols.xlsx` | 币池文件;.xlsx 取首列,.csv 无表头取首列,只留 `*USDT` |
| `--days` | 30 | 回看天数(决定触发率统计窗口) |
| `--direction` | `long` | `long`/`short`/`both`;`both` = 不覆盖,用每条规则自己的方向 |
| `--top` | 20 | 每规则输出 top N |
| `--out` | (无) | 结果写 CSV 的路径;不给就只打印到终端 |
| `--threshold` | `default` | `default` = 规则固定阈值;`adaptive` = 每币 p90 |
| `--workers` | 20 | 并行 worker |
| `--rps-per-key` | 2.0 | 每 key 每秒请求数(6 keys → 12 req/s) |
| `--key-cooldown` | 0.3 | 同 key 连续请求最小间隔 |
| `--batch-size` | 100 | 分批跑,每批多少币 |
| `--batch-sleep` | 5.0 | 批间 sleep 秒 |
| `--no-batch` | off | 不分批,单批跑完(小币池用) |

## 输出读法(最重要)

扫描输出三段,**含义不同,别混**:

### 1. 末根(已收盘)bar 触发 — "此刻该不该动手"

```
=== 末根(已收盘)bar 触发 — 12 个(币,规则)对 ===
  BTCUSDT       B04  TI 动能加速       做多   N=47
  SOLUSDT       D01  主力吸筹          做空   N=23
```

- 这才是**当前可执行信号**。
- 方向用 `direction_best`(校准后),不是文档方向 —— 一半规则两者相反。
- `N` = 历史触发次数,不是此刻强度。

### 2. Top N per rule — "历史触发率排名"

按 `trigger_rate`(历史累计频率)排,看哪些币**经常**触发某规则。
触发率高 ≠ 现在该做。它只说明这条规则对该币"不挑食",区分度可能差。

### 3. Top N cross-rule — "跨规则综合触发"

每币在所有规则上的平均触发率 + 命中规则数。命中多规则 ≠ 信号强(规则间可能高度相关),
但可作为"这个币最近异常活跃"的粗筛。

## 限流与失败处理

- **多 key 轮询**:`get_all_headers()` 给每个 worker 分不同 key,单 key 不扛 429。
- **分 key 限流**:`_KeyAwareRateLimiter` 按 key 独立计数,默认 2 req/s/key。
- **分批冷却**:默认每 100 币 sleep 5s。币池 >100 时不要 `--no-batch`。
- **死币/改名币**:MATICUSDT、RNDRUSDT 等 Blave 端返 400,自动跳过并在结尾汇总,不中断。
- **静默失效最危险**:若一条规则在**所有**币上都返回 None,结尾会显式报出哪条规则、
  提示检查 `cond_builder` 依赖的列(`abs_HC`/`hc_delta`/`ret_1h` 等)是否在 `fetch_coin_data` 生成。
  修过 31/45 条规则静默失效的 bug,靠的就是这个显式报告。

## K/L/M 新规则(已部分校准,小样本)

K01-K03 / L01-L03 / M01-M03 共 9 条是 v4.4 新增。其中 8 条已从 `batch_50_summary.csv` 投票校准 `direction_best`(M01 因触发不足 min_trades 无数据,回退 `direction_doc`)。
- 校准已写进 `rules_catalog/calibration.json` → `direction_best` 自动注入。
- **样本小**(每规则 1–7 个币种),置信度低于 A–J(后者每规则也只有 1–16 个币种)。
- K/L/M 在 `cross_period_rule_summary.csv` 里**一行都没有** —— 这 9 条只有单周期数据,没有跨周期验证。
- 扫描可用;方向置信度请对照 `references/rules-catalog.md` 方向校准表的「小样本」标注。
- 5 条翻转(K01/K03/L01/L03/M03)、3 条一致(K02/L02/M02)、1 条无数据(M01)。
