# Coin Screener (core/coin_screener.py)

> 🔴 **必读** — 批量币种筛选(539 USDT × 50 规则 × 双方向)是项目"找候选埋伏币"的核心入口。
> 任何"扫一下哪个币值得做"的需求,先来这里看用法,不要写新脚本。

## 用途

对一组币种(默认 539 真币 from `symbols.xlsx`)应用 catalog 规则,统计每(币, 规则)组合的触发率,输出 top N 候选埋伏币。

**两类典型场景:**

1. **找候选币** — 给定 1 条规则,找全市场触发率最高的 20 个币(埋伏买入)
2. **规则适配性测试** — 给定 50 币,看哪条规则在某币种上 Sharpe 最高

## 数据源与规则源

| 类型 | 来源 |
|---|---|
| 币池 | `symbols.xlsx`(539 真 USDT 永续,过滤 `*USDT`) |
| K-line | `lib.data.fetch_kline` |
| Alpha 指标 | `fetch_holder_concentration` / `market_sentiment` / `taker_intensity` / `whale_hunter` |
| 规则 | `rules_catalog.catalog.ALL_RULES`(50 条) |

## 5 个典型命令

```bash
# 1) 单规则,top 20,做多信号(D01 默认 long)
python core/coin_screener.py --rules D01 --top 20 --direction long

# 2) 多规则(逗号分隔)
python core/coin_screener.py --rules D01,F03,A06 --top 20

# 3) 做空规则(显式指定 direction)
python core/coin_screener.py --rules A05,A08 --direction short --top 10

# 4) 自适应阈值(每币 p90,小币公平)
python core/coin_screener.py --rules D01 --threshold adaptive --top 30

# 5) 自定义币池(20 个币 + 7 天窗口)
python core/coin_screener.py --rules D01 --coins my20.txt --days 7 --top 10
```

## 限流参数(防止触发 429)

> 539 币 × 5 req(1 kline + 4 alpha)= **2695 总请求**。不加限流会撞 Blave 限流。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--rps-per-key` | **2.0** | 每 key 每秒请求数(8 keys → 16 req/s 总) |
| `--key-cooldown` | 0.3 | 同 key 连续请求最小间隔秒 |
| `--batch-size` | 100 | 每批多少币 |
| `--batch-sleep` | 5.0 | 批间 sleep,等限流冷却 |
| `--no-batch` | — | 不分批(单批跑完所有币) |
| `--workers` | 20 | 线程池大小 |

**推荐配置:**

| 场景 | 命令片段 |
|---|---|
| **保守稳跑**(不踩限流) | `--rps-per-key 1.0 --batch-size 50 --batch-sleep 10` |
| **快速 539 币** | `--rps-per-key 2.0 --batch-size 100 --batch-sleep 5`(默认) |
| **急跑接受 429** | `--rps-per-key 5.0 --no-batch` |

## API Keys 配置

`.env` 文件支持 1-10 个 Blave API key(最多 10 个,KeyRotator 硬编码 `range(1, 11)`):

```bash
# 第一个用 blave_api_key / blave_secret_key(无后缀)
blave_api_key=...
blave_secret_key=...

# 后续用 _2, _3, ... _10
blave_api_key2=...
blave_secret_key2=...
...
blave_api_key10=...
blave_secret_key10=...
```

**当前默认 8 keys**(`key1`-`key8`)。**重启 Python 进程**才能加载新 key(KeyRotator 是模块级单例)。

**判断够不够:**
- 6 keys:够用(12 req/s)
- 8 keys:留 2x 余量(16 req/s,推荐)
- 10 keys:上限(20 req/s)
- 12+ keys:需改 `lib/data.py` 的 `_KeyRotator._load_keys` 循环

## 输出

- `cache/csv/screener_{rules}_{end_date}.csv` — 全部(币, 规则)行的明细
  - 列:`coin, rule, rule_name, direction, trigger_rate(%), n_triggers, n_bars, alpha_HC/MS/TI/WH_avg`
- 控制台:Top N per rule + 跨规则综合排名

## 常见问题

**Q1: 跑得很慢 / 卡住?**
- 看 `[safe_get] 429` 出现频率
  - 0-5 个:正常,在安全范围
  - >10 个:降 `--rps-per-key` 到 1.0 或加 key
- 看 cache 命中率:`cache/kline_1h_*USDT_*.parquet` 文件数 ≈ 缓存覆盖度

**Q2: 12% 失败率正常吗?**
- 12% 偏高,通常是**新币/低流动性币** 1h kline 拿不到
- 单跑 `--coins test5.txt` 主流币成功率应 > 99%

**Q3: 跑过一次的 cache 怎么重置?**
- 删除 `cache/{prefix}_{symbol}_{date}.parquet` 即可
- 或直接 `del cache\{prefix}_*` 通配

**Q4: 跟 `core/run_batch.py` 啥区别?**
- `coin_screener.py`:筛选"哪个币值得做"(输出 top 候选)
- `run_batch.py`:对**已知币+已知规则**做精细回测(输出 Sharpe/MDD/PnL)
- 工作流:coin_screener 选币 → run_batch 验证 → strategies/ 落盘

## 典型工作流

```
1. python core/coin_screener.py --rules D01,E04,G05 --top 20
   → 拿到 3 条规则各自的 top 20 候选币

2. python core/run_batch.py --symbols BTCUSDT,ETHUSDT,SOLUSDT
   → 对候选币做精细回测,出 Sharpe / MDD

3. 对 top 1 候选,按 AGENTS.md 4 步走:
   精扫 → 落盘 → 验证(IS/OOS/MCPT)→ 上 live
```
