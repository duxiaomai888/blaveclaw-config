# 59 条规则完整目录 + 方向校准表

## 方向校准表（最重要）

> **扫描时必须用 `direction_best`，不要用 `direction_doc`。**
> 共 **24 条**翻转 = 19 条 A–J（`batch_50_summary.csv`，44 个币种）+ 5 条 K/L/M（小样本，1–7 币）。
> 占 58 条已回测规则的 41%。下表「状态」列已逐条标注，K/L/M 另标「小样本」。

| 规则 | 名称 | 文档方向 | 实测方向 | 状态 |
|---|---|---|---|---|
| A01 | 大户开多仓 | long | long | ✅ 一致 |
| A02 | 大户平空仓 | long | long | ✅ 一致 |
| A03 | 大户开空仓 | short | **long** | ⚠️ 翻转 |
| A04 | 大户平多仓 | short | **long** | ⚠️ 翻转 |
| A05 | 大户顺势推空 | short | **long** | ⚠️ 翻转 |
| A06 | 大户低位布局 | long | long | ✅ 一致 |
| A07 | 派发预警 | short | short | ✅ 一致 |
| A08 | HC 高位下降 | short | **long** | ⚠️ 翻转 |
| B01 | 顺势做多动能 | long | long | ✅ 一致 ★★★ |
| B02 | 顺势做空动能 | short | short | ⏸️ 无数据 |
| B03 | 多空博弈 | short | **long** | ⚠️ 翻转 |
| B04 | TI 动能加速 | long | long | ✅ 一致 ★★★ |
| B05 | TI 极强 + 价格横盘 | long | long | ✅ 一致 |
| B06 | TI 强 + 价格反向 | short | **long** | ⚠️ 翻转 |
| C01 | SM+UM 突破 | long | long | ⏸️ 无数据 |
| C02 | SM+UM 启动 | long | long | ✅ 一致 |
| C03 | SM 单边动能 | long | long | ✅ 一致 |
| C04 | SM 极强 + 价格横盘 | long | long | ⏸️ 无数据 |
| C05 | SM 绿柱持续增 | long | long | ⏸️ 无数据 |
| D01 | 主力吸筹 | long | **short** | ⚠️ 翻转 |
| D02 | 主力派发 | short | **long** | ⚠️ 翻转 |
| D03 | 主力吸筹(加强) | long | long | ✅ 一致 |
| D04 | 主力派发(加强) | short | **long** | ⚠️ 翻转 |
| E01 | 主力+板块共振 | long | long | ✅ 一致 |
| E02 | 主力+板块背离 | short | **long** | ⚠️ 翻转 |
| E03 | WH 进区板块联动 | long | long | ✅ 一致 |
| E04 | TI+板块共振 | long | long | ✅ 一致 |
| E05 | BTC + 板块冷 | long | long | 🔒 skip |
| E06 | BTC + 板块热 | short | short | 🔒 skip |
| F01 | HC 强 + 价格回撤 | long | long | ✅ 一致 |
| F02 | HC 强 + 价格新高 | long | **short** | ⚠️ 翻转 |
| F03 | HC 强 + 价格横盘 | long | **short** | ⚠️ 翻转 |
| F04 | MS 极度乐观 + 价格新高 | short | **long** | ⚠️ 翻转 |
| F05 | MS 极度悲观 + 价格新低 | long | long | ✅ 一致 |
| F06 | 长上影线 + 高位 | short | **long** | ⚠️ 翻转 |
| G01 | 极端共识 | long | **short** | ⚠️ 翻转 |
| G02 | 极端波动+筹码集中 | long | long | ✅ 一致 |
| G03 | HC 强势集中 | short | **long** | ⚠️ 翻转 |
| G04 | HC 零轴突破 | long | long | ✅ 一致 |
| G05 | HC 零轴跌破 | short | short | ⏸️ 无数据 |
| H01 | 空头爆仓接刀 | long | **short** | ⚠️ 翻转 |
| H02 | 多头爆仓出清 | short | short | ⏸️ 无数据 |
| H03 | LM 爆仓 + 板块共振 | long | long | ✅ 一致 |
| H04 | LM 极端 + 价格反向 | long | long | ✅ 一致 |
| I01 | 连续 3 根阳线 | long | long | ⏸️ 无数据 |
| I02 | 连续 3 根阴线 | short | **long** | ⚠️ 翻转 |
| I03 | 吸筹转启动 | long | **short** | ⚠️ 翻转 |
| J01 | BTC 联动 | long | long | 🔒 skip |
| J02 | BTC 背离 | long | long | 🔒 skip |
| J03 | 板块内联动 | long | long | 🔒 skip |
| K01 | 放量突破 | long | **short** | ⚠️ 翻转(小样本) |
| K02 | 缩量回调 | long | long | ✅ 一致(小样本) |
| K03 | 量价背离 | short | **long** | ⚠️ 翻转(小样本) |
| L01 | HC-TI 背离 | short | **long** | ⚠️ 翻转(小样本) |
| L02 | 巨鲸逆势建仓 | long | long | ✅ 一致(小样本) |
| L03 | 情绪筹码极端背离 | short | **long** | ⚠️ 翻转(小样本) |
| M01 | TI 极端反转 | long | long | ⏸️ 无数据(fallback) |
| M02 | HC 过度集中回归 | short | short | ✅ 一致(小样本) |
| M03 | 爆仓+筹码反转 | long | **short** | ⚠️ 翻转(小样本) |

---

## 规则定义详情

### A: 主力动作 (8)

| ID | 名称 | 逻辑 | 条件 |
|---|---|---|---|
| A01 | 大户开多仓 | WH 启动区 + HC 持续增 | `HC > hc_th & hc_delta > 0` |
| A02 | 大户平空仓 | WH 观望区 + HC 持续增 | `HC.between(-0.3, hc_th) & hc_delta > 0` |
| A03 | 大户开空仓 | WH 启动区 + HC 持续减 | `HC < -hc_th & hc_delta < 0` |
| A04 | 大户平多仓 | WH 观望区 + HC 持续减 | `HC.between(-hc_th, 0.3) & hc_delta < 0` |
| A05 | 大户顺势推空 | 空头推升 + HC 持续增 | `HC < -hc_th & hc_delta > 0` |
| A06 | 大户低位布局 | WH 强 + 多头建仓区 | `WH > wh_th & HC.between(0, 0.5)` |
| A07 | 派发预警 | 多头推升 + HC 极强 + 持续减 | `HC > 0.5 & hc_delta < 0 & abs_HC > abs_hc_th` |
| A08 | HC 高位下降 | HC 极端区持续减 | `abs_HC > abs_hc_th & hc_delta < 0` |

### B: 动能确认 (6)

| ID | 名称 | 条件 |
|---|---|---|
| B01 | 顺势做多动能 | `TI > ti_th & ti_sign > 0 & HC > 0` |
| B02 | 顺势做空动能 | `TI > ti_th & ti_sign < 0 & HC < 0` |
| B03 | 多空博弈 | `WH.between(-wh_band, wh_band)` |
| B04 | TI 动能加速 | `TI > ti_th & TI > TI.shift(1)` |
| B05 | TI 极强 + 价格横盘 | `TI > ti_th & abs(ret_1h) < 0.003` |
| B06 | TI 强 + 价格反向 | `TI > ti_th & sign(ret_1h) != ti_sign` |

### C: 动量突破 (5)

| ID | 名称 | 条件 |
|---|---|---|
| C01 | SM+UM 突破 | `abs(SM) > sm_th & abs(LM) > 2` |
| C02 | SM+UM 启动 | `abs(SM) > sm_th & abs(LM) < 2 & LM.notna()` |
| C03 | SM 单边动能 | `abs(SM) > sm_th` |
| C04 | SM 极强 + 价格横盘 | `abs(SM) > sm_th & abs(ret_1h) < 0.003` |
| C05 | SM 绿柱持续增 | `SM > sm_th & SM > SM.shift(1)` |

### D: 主力 vs 散户 (4)

| ID | 名称 | 条件 |
|---|---|---|
| D01 | 主力吸筹 | `HC > hc_th & MS < -ms_th` |
| D02 | 主力派发 | `HC < -hc_th & MS > ms_th` |
| D03 | 主力吸筹(加强) | `HC > hc_th & MS < -ms_th & abs(ret_24h) < 0.01` |
| D04 | 主力派发(加强) | `HC < -hc_th & MS > ms_th & new_high_24h` |

### E: 板块联动 (6, 2 skip)

| ID | 名称 | 条件 | skip |
|---|---|---|---|
| E01 | 主力+板块共振 | `HC > hc_th & MS > 1` | |
| E02 | 主力+板块背离 | `HC > hc_th & MS < -1` | |
| E03 | WH 进区板块联动 | `WH > wh_th & MS > 1` | |
| E04 | TI+板块共振 | `TI > ti_th & ti_sign > 0 & MS > 1` | |
| E05 | BTC + 板块冷 | 需 BTC 数据 | ✅ skip |
| E06 | BTC + 板块热 | 需 BTC 数据 | ✅ skip |

### F: 价格确认 (6)

| ID | 名称 | 条件 |
|---|---|---|
| F01 | HC 强 + 价格回撤 | `HC > hc_th & ret_1h < -0.01` |
| F02 | HC 强 + 价格新高 | `HC > hc_th & new_high_24h` |
| F03 | HC 强 + 价格横盘 | `HC > hc_th & abs(ret_24h) < 0.01` |
| F04 | MS 极度乐观 + 价格新高 | `MS > ms_th & new_high_24h` |
| F05 | MS 极度悲观 + 价格新低 | `MS < -ms_th & new_low_24h` |
| F06 | 长上影线 + 高位 | `(High − max(Open,Close)) > shadow_ratio × (max−min)` & `ret_24h > 0.05` |

### G: 极端反转 (5)

| ID | 名称 | 条件 |
|---|---|---|
| G01 | 极端共识 | `abs_HC > abs_hc_th & abs_TI > 2 & hc_sign * ti_sign > 0` |
| G02 | 极端波动+筹码集中 | `abs_HC > abs_hc_th & abs(LM) > 2` |
| G03 | HC 强势集中 | `abs_HC > abs_hc_th` |
| G04 | HC 零轴突破 | `HC > 0 & HC.shift(1) <= 0` |
| G05 | HC 零轴跌破 | `HC < 0 & HC.shift(1) >= 0` |

### H: 爆仓 (4)

| ID | 名称 | 条件 |
|---|---|---|
| H01 | 空头爆仓接刀 | `LM > lm_th & TI > 2 & ti_sign > 0` |
| H02 | 多头爆仓出清 | `LM < -lm_th & TI > 2 & ti_sign < 0` |
| H03 | LM 爆仓 + 板块共振 | `abs(LM) > lm_th & MS > 1` |
| H04 | LM 极端 + 价格反向 | `abs(LM) > lm_th & sign(ret_1h) != lm_sign` |

### I: 连续模式 (3)

| ID | 名称 | 条件 |
|---|---|---|
| I01 | 连续 3 根阳线 | `(Close > Open).rolling(3).sum() >= 3` |
| I02 | 连续 3 根阴线 | `(Close < Open).rolling(3).sum() >= 3` |
| I03 | 吸筹转启动 | `HC > hc_th & HC.shift(1) < hc_th & HC.shift(1) > 0` |

### J: 跨币种联动 (3, 全 skip)

| ID | 名称 | skip 原因 |
|---|---|---|
| J01 | BTC 联动 | 需 BTC 数据 |
| J02 | BTC 背离 | 需 BTC 数据 |
| J03 | 板块内联动 | 需板块多币数据 |

### K: 量价配合 (3, 新增)

| ID | 名称 | 条件 |
|---|---|---|
| K01 | 放量突破 | `Volume > vol_mult * Volume.rolling(24).mean() & new_high_24h` |
| K02 | 缩量回调 | `Volume < vol_mult * Volume.rolling(24).mean() & ret_24h < -0.01` |
| K03 | 量价背离 | `Volume > vol_mult * Volume.rolling(24).mean() & ret_1h < -0.005` |

### L: 跨指标背离 (3, 新增)

| ID | 名称 | 条件 |
|---|---|---|
| L01 | HC-TI 背离 | `HC > hc_th & hc_delta > 0 & TI < TI.shift(1)` |
| L02 | 巨鲸逆势建仓 | `WH > wh_th & WH > WH.shift(1) & ret_24h < -0.01` |
| L03 | 情绪筹码极端背离 | `MS > ms_th & HC < -0.5` |

### M: 动量反转 (3, 新增)

| ID | 名称 | 条件 |
|---|---|---|
| M01 | TI 极端反转 | `abs_TI > ti_th & ti_sign != ti_sign.shift(1)` |
| M02 | HC 过度集中回归 | `abs_HC > abs_hc_th & hc_delta < 0` |
| M03 | 爆仓+筹码反转 | `abs(LM) > lm_th & hc_sign * lm_sign > 0` |

---

## 数据列需求

扫描器需要以下列（由 coin_screener.fetch_coin_data 生成）：

| 列名 | 来源 | 说明 |
|---|---|---|
| Open/High/Low/Close/Volume | fetch_kline | OHLCV K 线 |
| HC | fetch_holder_concentration | 筹码集中度 alpha |
| WH | fetch_whale_hunter | 巨鲸仓位 alpha |
| TI | fetch_taker_intensity | 多空力道 alpha |
| MS | fetch_market_sentiment | 市场情绪 alpha |
| LM | fetch_liquidation | 爆仓 alpha |
| SM | fetch_squeeze_momentum | 挤压动量 alpha |
| abs_HC/abs_TI/abs_SM/abs_LM | 派生 | 绝对值 |
| hc_sign/ti_sign/lm_sign | 派生 | 符号函数 |
| hc_delta | 派生 | HC 符号变化 |
| ret_1h/ret_24h | 派生 | 1h/24h 收益率 |
| new_high_24h/new_low_24h | 派生 | 24h 新高/新低 |
