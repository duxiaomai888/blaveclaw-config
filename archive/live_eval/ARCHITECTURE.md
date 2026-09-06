# live_eval/ 架构提议 v2

## 目录结构

live_eval/
├── core/                          # 共享模块
│   ├── __init__.py
│   ├── config.py                  # BASE_URL, BJ_TZ, paths
│   ├── blave_client.py            # Blave API 客户端 (7维 alpha)
│   ├── binance_client.py          # Binance API 客户端 (OHLCV + trades)
│   ├── data_normalizer.py         # 统一两源数据格式
│   ├── patterns.py                 # 形态识别 (H&S, 双顶, 三角, 旗, 楔, 通道)
│   ├── volume_profile.py          # 筹码集中度计算
│   ├── indicators.py               # OBV, A/D, VWAP, 主动买/卖比
│   ├── breakdown.py                # 突破/跌破 + 量能确认
│   └── scoring.py                  # 形态 + 量价 + 7维 alpha 综合评分
│
├── scripts/                       # CLI 入口
│   ├── realtime_eval.py           # 实时评估(单次,出 markdown 报告)
│   ├── backtest.py                 # 1y 回测(每根 K线模拟)
│   ├── pattern_scanner.py          # 扫描所有币种找形态
│   └── update_docs.py              # 文档同步
│
├── data/                          # 数据存储
│   ├── score_history/             # 每日/每次运行快照
│   └── backtest/                  # 回测结果
│
├── reports/                       # 生成的报告
│
├── templates/                     # 报告模板
│
├── README.md                      # 项目说明
└── FINAL_OVERVIEW.md              # 总览

## 报告结构提议 (15 段)

1. 当前价格 + 24h 变化
2. 7 档价格矩阵 (Blave K-line)
3. 形态识别 (Binance OHLCV)
   - 6 个经典形态 + 识别结果
4. Volume Profile (筹码集中度)
   - POC, Value Area, HVN/LVN
5. 量价指标
   - OBV 趋势, 主动买/卖比, A/D Line
6. 7 维 alpha × 7 档趋势矩阵 (Blave)
7. Funding + Liquidation Map (Blave)
8. 关键支撑/阻力
9. 跨期变化
10. 跨期变化 (爆仓地图)
11. 跨期历史趋势
12. 框架综合评分
13. 形态 + 量价 + 7维 综合判断
14. 决策对比
15. 复盘位
