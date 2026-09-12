# vendor/chan — 内嵌的 chan.py 缠论框架

本目录是 [chan.py](https://github.com/yijixiuxin/chan.py) 的**完整副本**(commit: 见 git log),
用于 BBAC-D 项目的缠论可视化,**不依赖**外部 `C:/Users/Blaw-D/Desktop/chan.py-main`。

## 为什么内嵌?

BBAC-D 项目目标是"独立可移植",不应假设用户桌面上的 chan.py-main 仍在固定路径。
把框架嵌入 vendor 目录后:

- ✅ 整仓可 zip 打包,在任何机器都能直接跑
- ✅ chan.py 框架 API 锁定,不会因为桌面版升级而突然破坏
- ✅ AGENTS.md 里强调"复用现成的"原则 — 我们只用了框架的子集,
   内嵌后能精确控制范围

## 内嵌的子集(53 个 .py 文件)

| 子目录 | 用途 | 必需? |
|---|---|---|
| `Chan.py` `ChanConfig.py` | 入口主类 | ✅ |
| `Bi/` `Seg/` `ZS/` | 笔/段/中枢 | ✅ |
| `BuySellPoint/` | 买卖点 | ✅ |
| `Combiner/` `KLine/` | K 线合并 | ✅ |
| `Common/` | 枚举/时间/异常 | ✅ |
| `Math/` | MACD/RSI/BOLL 等指标 | ✅ |
| `Plot/` | matplotlib 绘图 | ✅ |
| `ChanModel/` | ML 特征(预留) | ✅(占位) |
| `DataAPI/` | ⚠️ **stub only** | 详见下 |

## DataAPI 是 stub

BBAC-D 通过 `trigger_load()` **直接喂 CKLine_Unit**,完全不走 chan.py 的 DataAPI
数据源适配层(`baostock`/`akshare`/`ccxt`)。所以:

- `DataAPI/CommonStockAPI.py` — 空 stub 基类,只为了让 `Chan.py` 的 type hint 不报错
- **没有** `BaoStockAPI.py` / `AkshareAPI.py` / `ccxt.py` / `csvAPI.py` — 这些会拉入外部依赖
- 如果未来需要从某个交易所拉数据,直接在 BBAC-D 的 `lib.data` 取数,然后喂 `trigger_load()`

## 升级

如果上游 chan.py 有重大 bug 修复,手动同步:

```bash
cd C:/Users/Blaw-D/Desktop
rsync -av --delete --exclude='DataAPI' --exclude='App' --exclude='Debug' \
    --exclude='__pycache__' --exclude='Image' --exclude='*.gif' --exclude='*.png' \
    chan.py-main/ C:/Users/Blaw-D/Desktop/BBAC-D/vendor/chan/
```

然后恢复 `DataAPI/CommonStockAPI.py` 的 stub(同步会被覆盖)。

## 使用入口

调用方是 [core/chan_plot.py](../core/chan_plot.py):

```python
# core/chan_plot.py 第 17-23 行
_VENDOR_CHAN = Path(_bootstrap._ROOT) / "vendor" / "chan"
if str(_VENDOR_CHAN) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CHAN))

from Chan import CChan
from Plot.PlotDriver import CPlotDriver
```

## 文件大小

- 53 个 `.py` 文件
- ~150 KB 源码
- 不带测试/Demo/Image/数据
