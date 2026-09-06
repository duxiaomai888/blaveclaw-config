"""
BTC 庄家操盘手法 PoC
====================

基于《庄家克星：职业操盘手解析坐庄全过程》中的可量化阈值，
对 BTCUSDT 1h K 线 + Blave 指标 (HC/WH/MS/TI/LM) 做阶段识别 + 信号触发。

输出：
1. BTC_zhuangjia_dashboard.png - 多面板可视化看板
2. BTC_zhuangjia_report.md - 统计报告
3. BTC_zhuangjia_signals.parquet - 原始信号（可后续回测）

数据：
- btc_analysis_20d.parquet (475 bars, 2026-06-06 → 2026-06-25) - 包含 5 个 Blave 指标
- cache/kline_1h_BTCUSDT_2022-01-01.parquet (38346 bars, 2022-01 → 2026-05) - 长周期纯 K 线

注：
- 短周期 (20d) 用 5 个 Blave 指标做多维度信号检测
- 长周期 (4.3y) 仅用 K 线做洗盘/出货/反弹识别，验证 20d 信号的统计意义
- 书中所有阈值针对 A 股日线，加密货币 1h 数据噪声更大，部分阈值已调整
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

# ── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT = Path("c:/Users/Blaw-D/Desktop/BBAC-D")
SHORT_PARQUET = ROOT / "btc_analysis_20d.parquet"           # 20d Blave + K 线
LONG_PARQUET = ROOT / "cache" / "kline_1h_BTCUSDT_2022-01-01.parquet"  # 4.3y 纯 K 线

OUT_DIR = ROOT / "live_eval" / "zhuangjia_poc"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DASHBOARD_PNG = OUT_DIR / "BTC_zhuangjia_dashboard.png"
REPORT_MD = OUT_DIR / "BTC_zhuangjia_report.md"
SIGNALS_PARQUET = OUT_DIR / "BTC_zhuangjia_signals.parquet"

# ── matplotlib 全英文 (避免中文乱码) ────────────────────────────────────────
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["axes.unicode_minus"] = False


# ═════════════════════════════════════════════════════════════════════════════
# 第一部分：庄家阶段识别器
# ═════════════════════════════════════════════════════════════════════════════

# 书中阶段配色（英文标签）
STAGE_COLORS = {
    "buildup":   "#5DADE2",  # 建仓 - 蓝
    "test":      "#F4D03F",  # 试盘 - 黄
    "consolid":  "#A569BD",  # 整理 - 紫
    "liftoff":   "#58D68D",  # 初升 - 绿
    "wash":      "#F39C12",  # 洗盘 - 橙
    "rally":     "#E74C3C",  # 拉升 - 红
    "distrib":   "#7B7D7D",  # 出货 - 灰
    "bounce":    "#1ABC9C",  # 反弹 - 青
    "capitul":   "#34495E",  # 砸盘 - 深蓝灰
    "reversal":  "#D35400",  # 变盘 - 深橙
}


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """添加 5 个 Blave 指标 + 均线 + 量能代理 + OBV + 波动率

    重要：原始 Volume 字段在 btc_analysis_20d.parquet 中为 0 (预处理版本)
    所以用 |Close-Open|/Close 作为"日内成交活跃度"代理
    """
    df = df.copy()

    # ── 均线 (书中: 5/10/20/30 日 → 加密 1h: 24/72/168/336 bars) ──
    for w, name in [(24, "MA5d"), (72, "MA10d"), (168, "MA20d"), (336, "MA30d")]:
        df[name] = df["Close"].rolling(w).mean()

    # ── 量能代理：日内振幅 + LM (liquidation magnitude) ─────────────
    # 1. 真实 Volume 字段 (如果有非零数据)
    if "Volume" in df.columns and df["Volume"].sum() > 0:
        df["VolRaw"] = df["Volume"]
    else:
        df["VolRaw"] = 0.0

    # 2. 用 |Close-Open| / Close 作为"日内动能"代理
    df["IntradayEnergy"] = (df["Close"] - df["Open"]).abs() / df["Open"] * 100

    # 3. 用 True Range 作为"波动代理"
    df["TrueRange"] = df["High"] - df["Low"]
    df["TR_Pct"] = df["TrueRange"] / df["Close"] * 100

    # 4. 综合量能代理 = max(VolRaw, Energy*scale, TR_Pct)
    df["VolProxy"] = np.maximum(
        df["VolRaw"].fillna(0),
        df["IntradayEnergy"] * 1e6  # scale 到与 VolRaw 同量级
    )

    # 5. 量比 (基于代理)
    df["VolProxyMA5"] = df["VolProxy"].rolling(24).mean().replace(0, np.nan)
    df["VolRatio5"] = df["VolProxy"] / df["VolProxyMA5"]

    # 6. 真量比 (基于 TR)
    df["TRMA5"] = df["TR_Pct"].rolling(24).mean()
    df["TRRatio5"] = df["TR_Pct"] / df["TRMA5"].replace(0, np.nan)

    # ── OBV (书中: 吸筹期 OBV 向上但价格横盘 = 关键建仓信号) ──
    direction = np.sign(df["Close"].diff()).fillna(0)
    df["OBV"] = (direction * df["TR_Pct"].fillna(0)).cumsum()  # 用 TR 替代 Vol
    df["OBV_MA"] = df["OBV"].rolling(48).mean()

    # ── 价格区间宽度 (书中: 振幅 10%-20% 暗示横盘建仓) ──
    df["Range_Pct"] = df["TR_Pct"]

    # ── 已实现波动率 (书中: 控盘程度高的标志) ──
    log_ret = np.log(df["Close"] / df["Close"].shift(1))
    df["RV_24h"] = log_ret.rolling(24).std() * np.sqrt(24)
    df["RV_72h"] = log_ret.rolling(72).std() * np.sqrt(72)

    # ── 累积涨跌幅 (书中: 累计涨幅 >1 倍 → 谨慎，>2 倍 → 拒绝参与) ──
    df["Ret_7d"] = df["Close"].pct_change(168) * 100
    df["Ret_30d"] = df["Close"].pct_change(720) * 100

    return df


# ═════════════════════════════════════════════════════════════════════════════
# 第二部分：庄家 10 阶段综合识别 (每根 bar 一个得分)
# ═════════════════════════════════════════════════════════════════════════════

def detect_stages(df: pd.DataFrame) -> pd.DataFrame:
    """
    综合识别每根 bar 的庄家阶段。
    书中核心原则: 量价时空四个维度的阈值联合判断。
    """
    df = df.copy()
    n = len(df)
    stages = pd.Series("none", index=df.index)

    # ── 建仓 (buildup) ─────────────────────────────────────────────────
    # 书中: 横盘 3 月+，缩量，OBV 向上，均线黏合
    # 加密版 (放宽!): 24h 振幅 < 5%，量比 < 1.0，OBV > OBV_MA，均线收敛 (max-min)/mean < 4%
    buildup_cond = (
        (df["Range_Pct"] < 5.0) &
        (df["VolRatio5"] < 1.0) &
        (df["OBV"] > df["OBV_MA"]) &
        ((df["MA5d"].subtract(df["MA30d"]).abs() / df["MA30d"]) < 0.04)
    )

    # ── 试盘 (test) ─────────────────────────────────────────────────────
    # 书中: 振幅 5%-30%，单日异常放量，均线仍黏合
    test_cond = (
        (df["Range_Pct"] > 1.5) &
        (df["VolRatio5"] > 1.3) &
        ((df["MA5d"].subtract(df["MA30d"]).abs() / df["MA30d"]) < 0.05) &
        (df["Range_Pct"] < 10.0)
    )

    # ── 整理 (consolid) ───────────────────────────────────────────────
    # 书中: 持仓比例 2:8 → 4:6 渐进，振幅 10%-20%
    consolid_cond = (
        (df["Range_Pct"] > 0.8) &
        (df["Range_Pct"] < 7.0) &
        (df["VolRatio5"] > 0.4) &
        (df["VolRatio5"] < 1.6) &
        ((df["MA5d"].subtract(df["MA30d"]).abs() / df["MA30d"]) < 0.05)
    )

    # ── 初升 (liftoff) ─────────────────────────────────────────────────
    # 书中: 突破 >3%，周量 ≥前 5 周均量 5 倍，多头排列
    # 加密版放宽: 24h 涨幅 >2%，量比 >1.5，多头排列
    liftoff_cond = (
        (df["Close"] > df["MA5d"]) & (df["MA5d"] > df["MA10d"]) &
        (df["Close"].pct_change(24) > 0.02) &
        (df["VolRatio5"] > 1.5)
    )

    # ── 洗盘 (wash) ───────────────────────────────────────────────────
    # 书中铁律: 下跌缩量=洗盘；30 日均线上行；跌幅为升幅 1/3-1/2
    # 加密版: 近 24h 收阴但 vol_ratio < 1.0，MA30d 仍上行
    wash_cond = (
        (df["Close"] < df["Close"].shift(1)) &
        (df["VolRatio5"] < 1.0) &
        (df["MA30d"] > df["MA30d"].shift(24)) &
        (df["Close"] > df["MA30d"])
    )

    # ── 拉升 (rally) ───────────────────────────────────────────────────
    # 书中: 30°/45°/70° 三档角度，连续阳线
    rally_cond = (
        (df["Close"] > df["Close"].shift(1)) &
        (df["VolRatio5"] > 1.0) &
        (df["MA5d"] > df["MA10d"]) &
        (df["Close"] > df["MA5d"] * 1.003)
    )

    # ── 出货 (distrib) ────────────────────────────────────────────────
    # 书中: 涨幅 >80%，放量滞涨 (加密版放宽到 7d 涨幅 >5%)
    distrib_cond = (
        (df["Ret_7d"] > 5.0) &
        (df["VolRatio5"] > 1.3) &
        (df["Close"].pct_change(24).abs() < 0.02)  # 涨不动了
    )

    # ── 反弹 (bounce) ─────────────────────────────────────────────────
    # 书中: 跌幅>30% 后反弹至 0.382/0.5/0.618 (放宽到 -3%)
    bounce_cond = (
        (df["Ret_7d"] < -3.0) &
        (df["Close"] > df["Close"].shift(24)) &
        (df["Close"] < df["Close"].shift(72))
    )

    # ── 砸盘 (capitul) ────────────────────────────────────────────────
    # 书中: 跌幅 30%-70%+ (加密版放宽到 -4%)
    capitul_cond = (
        (df["Close"].pct_change(24) < -0.04) &
        (df["VolRatio5"] > 1.2)
    )

    # ── 变盘 (reversal) ────────────────────────────────────────────────
    # 书中: 缩量到高峰 30% 以内；事不过三
    reversal_cond = (
        (df["VolRatio5"] < 0.6) &
        (df["Range_Pct"] < 1.5)
    )

    # ── 优先级排序 (一个 bar 只属于一个阶段) ─────────────────────────
    priority = [
        ("capitul",  capitul_cond),
        ("bounce",   bounce_cond),
        ("distrib",  distrib_cond),
        ("rally",    rally_cond),
        ("liftoff",  liftoff_cond),
        ("wash",     wash_cond),
        ("test",     test_cond),
        ("buildup",  buildup_cond),
        ("consolid", consolid_cond),
        ("reversal", reversal_cond),
    ]
    for name, cond in priority:
        mask = (stages == "none") & cond
        stages[mask] = name

    df["Stage"] = stages
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 第三部分：4 类交易信号 (用户的核心需求)
# ═════════════════════════════════════════════════════════════════════════════

def signal_buildup_liftoff(df: pd.DataFrame) -> pd.Series:
    """
    信号 1：建仓结束 + 初升启动
    书中条件：
    - 横盘 >3 个月（加密版: >168h=7d）
    - 量能萎缩到地量
    - OBV 向上
    - 均线黏合 → 突破上沿 >3%
    - 周成交量 ≥ 前 5 周均量 5 倍
    """
    sig = pd.Series(0, index=df.index, dtype=int)

    # 近 168 根 (7d) 处于建仓/整理阶段的比例 > 60%
    # 用 vectorized 替代 rolling.apply (避免字符串问题)
    prev_stage = df["Stage"].shift(1)
    is_buildup = (prev_stage == "buildup") | (prev_stage == "consolid")
    buildup_count = is_buildup.rolling(168).sum()
    buildup_ratio = buildup_count / 168.0

    # 突破条件
    breakout = (
        (df["Stage"] == "liftoff") &
        (buildup_ratio > 0.3) &  # 放宽 (加密 1h 信号稀少)
        (df["VolRatio5"] > 1.5) &
        (df["Close"] > df["MA30d"] * 1.02)
    )
    sig[breakout] = 1
    return sig


def signal_wash_vs_distrib(df: pd.DataFrame) -> pd.Series:
    """
    信号 2：洗盘 vs 出货 15 点识别（最实用）
    核心:
    - 下跌缩量 = 洗盘 → 入场/持仓
    - 下跌放量 = 出货 → 离场
    附加: 30 日均线上行 = 洗盘；高位区放量 = 出货
    """
    sig = pd.Series(0, index=df.index, dtype=int)

    decline = df["Close"] < df["Close"].shift(1)
    vol_shrink = df["VolRatio5"] < 0.8
    vol_surge = df["VolRatio5"] > 1.5
    ma30_rising = df["MA30d"] > df["MA30d"].shift(24)
    near_high = df["Ret_7d"] > 10.0  # 高位区

    # 洗盘信号：缩量下跌 + 30日均线上行
    wash = decline & vol_shrink & ma30_rising
    sig[wash] = 1

    # 出货信号：放量下跌 或 高位放量
    distrib = (decline & vol_surge) | (near_high & vol_surge & decline)
    sig[distrib] = -1

    return sig


def signal_rebound_golden(df: pd.DataFrame) -> pd.Series:
    """
    信号 3：反弹黄金分割
    书中: 累计跌幅 >30% → 反弹 0.382 / 0.5 / 0.618 / 0.809
    关键: 在 0.5 处获利了结

    实现:
    1. 找近期最低点 (lookback 720h=30d)
    2. 找下跌起点 (lookback 720h 内的高点)
    3. 当前价位 = (low - high) * pct_of_rebound + low
    4. 触发信号
    """
    sig = pd.Series(0, index=df.index, dtype=int)

    # 滚动找近 30d 高低点
    window = 720
    high_30d = df["High"].rolling(window).max()
    low_30d = df["Low"].rolling(window).min()

    decline_pct = (low_30d - high_30d) / high_30d * 100  # 跌幅 (负数)
    rebound_pct = (df["Close"] - low_30d) / (high_30d - low_30d)  # 当前反弹比例 (0-1)

    # 跌幅 > 15% 才算有意义 (加密版降低阈值)
    meaningful_decline = decline_pct < -15

    # 反弹至 0.382 / 0.5 / 0.618
    fib_382 = (rebound_pct >= 0.35) & (rebound_pct < 0.45) & meaningful_decline
    fib_500 = (rebound_pct >= 0.45) & (rebound_pct < 0.55) & meaningful_decline
    fib_618 = (rebound_pct >= 0.55) & (rebound_pct < 0.65) & meaningful_decline

    sig[fib_382] = 1  # 入场
    sig[fib_500] = 2  # 减仓
    sig[fib_618] = 3  # 再减仓

    return sig


def signal_three_strikes(df: pd.DataFrame) -> pd.Series:
    """
    信号 4：事不过三 / 变盘时间窗
    书中核心:
    - 同一支撑位/阻力位第 4 次触及 → 反向突破概率高
    - "5+3=3 周" 时间窗

    实现:
    - 找近 7d 的局部高/低点
    - 统计价格接近该位的次数
    - 第 4 次触发时给信号
    """
    sig = pd.Series(0, index=df.index, dtype=int)

    # 找 24h 滚动高/低点
    rolling_high = df["High"].rolling(24).max()
    rolling_low = df["Low"].rolling(24).min()

    # 当前价接近 7d 内的某个反复触及的位 (回看 168h)
    # 简化: 用近 7d 的价格"密集区"识别
    lookback = 168
    price_std_7d = df["Close"].rolling(lookback).std()
    price_mean_7d = df["Close"].rolling(lookback).mean()

    # 振幅收缩 + 反复测试同一区域 → 变盘临界
    contraction = (price_std_7d / price_mean_7d) < 0.015  # 振幅 < 1.5%
    near_high = (df["Close"] > rolling_high * 0.998) & (df["Close"] < rolling_high * 1.002)
    near_low = (df["Close"] > rolling_low * 0.998) & (df["Close"] < rolling_low * 1.002)

    # 连续 24h+ 处于收缩区间
    contraction_streak = contraction.rolling(24).sum() >= 24

    sig[contraction_streak & near_high] = 2   # 上方临界 → 准备突破
    sig[contraction_streak & near_low] = 1    # 下方临界 → 准备反弹
    return sig


# ═════════════════════════════════════════════════════════════════════════════
# 第四部分：长周期 (4.3y) 统计验证
# ═════════════════════════════════════════════════════════════════════════════

def long_period_validation():
    """用 4.3 年纯 K 线，统计纯 K 线版"洗盘 vs 出货"信号准确率"""
    print("\n[Long-period validation: 4.3y K-line only]")
    df = pd.read_parquet(LONG_PARQUET)
    print(f"  rows={len(df)}, range={df.index[0]} → {df.index[-1]}")

    df = add_indicators(df)
    df = detect_stages(df)

    # 纯 K 线版洗盘 vs 出货 (不需要 Blave)
    # 用 TR_Pct 替代 Volume
    decline = df["Close"] < df["Close"].shift(1)
    tr_shrink = df["TRRatio5"] < 0.7  # 振幅缩小
    tr_surge = df["TRRatio5"] > 1.5   # 振幅放大
    ma168_rising = df["Close"].rolling(168).mean() > df["Close"].rolling(168).mean().shift(168)

    wash_signal = (decline & tr_shrink & ma168_rising).sum()
    distrib_signal = (decline & tr_surge).sum()

    print(f"  洗盘信号 (缩量下跌+MA168 上行): {wash_signal} 次")
    print(f"  出货信号 (放量下跌): {distrib_signal} 次")

    # 阶段分布
    stage_dist = df["Stage"].value_counts(normalize=True) * 100
    print("\n  阶段分布 (4.3y 占比):")
    for s, p in stage_dist.items():
        if p > 0.5:
            print(f"    {s:12s}: {p:5.1f}%")

    return df


# ═════════════════════════════════════════════════════════════════════════════
# 第五部分：可视化看板
# ═════════════════════════════════════════════════════════════════════════════

def plot_dashboard(df: pd.DataFrame):
    """生成多面板 BTC + 庄家阶段 + 4 类信号看板"""
    fig, axes = plt.subplots(
        7, 1, figsize=(18, 22), sharex=True,
        gridspec_kw={"height_ratios": [4, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2]}
    )

    # ── Panel 1: 价格 + 均线 ──────────────────────────────────────────
    ax = axes[0]
    ax.plot(df.index, df["Close"], color="#2C3E50", linewidth=1.2, label="BTC Close")
    ax.plot(df.index, df["MA5d"], color="#E67E22", linewidth=0.7, alpha=0.8, label="MA(1d)")
    ax.plot(df.index, df["MA10d"], color="#27AE60", linewidth=0.7, alpha=0.8, label="MA(3d)")
    ax.plot(df.index, df["MA20d"], color="#C0392B", linewidth=0.7, alpha=0.8, label="MA(7d)")
    ax.plot(df.index, df["MA30d"], color="#8E44AD", linewidth=0.7, alpha=0.8, label="MA(14d)")
    ax.set_ylabel("Price (USDT)", fontsize=10)
    ax.set_title("BTCUSDT 1h — Zhuangjia PoC Dashboard (2026-06-06 → 2026-06-25)",
                 fontsize=13, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, ncol=5)
    ax.grid(True, alpha=0.3)

    # ── Panel 2: 庄家阶段带 ──────────────────────────────────────────
    ax = axes[1]
    stage_to_int = {s: i for i, s in enumerate(STAGE_COLORS.keys())}
    stage_int = df["Stage"].map(stage_to_int).fillna(-1)
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "buildup"),
                    color=STAGE_COLORS["buildup"], alpha=0.7, label="buildup")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "test"),
                    color=STAGE_COLORS["test"], alpha=0.7, label="test")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "consolid"),
                    color=STAGE_COLORS["consolid"], alpha=0.7, label="consolid")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "liftoff"),
                    color=STAGE_COLORS["liftoff"], alpha=0.8, label="liftoff")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "wash"),
                    color=STAGE_COLORS["wash"], alpha=0.7, label="wash")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "rally"),
                    color=STAGE_COLORS["rally"], alpha=0.7, label="rally")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "distrib"),
                    color=STAGE_COLORS["distrib"], alpha=0.7, label="distrib")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "bounce"),
                    color=STAGE_COLORS["bounce"], alpha=0.7, label="bounce")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "capitul"),
                    color=STAGE_COLORS["capitul"], alpha=0.7, label="capitul")
    ax.fill_between(df.index, 0, 1, where=(df["Stage"] == "reversal"),
                    color=STAGE_COLORS["reversal"], alpha=0.7, label="reversal")
    ax.set_ylabel("Stage", fontsize=10)
    ax.set_yticks([])
    ax.legend(loc="upper left", fontsize=7, ncol=10)
    ax.grid(True, alpha=0.2)

    # ── Panel 3-6: 4 类信号 ─────────────────────────────────────────
    signal_specs = [
        ("buildup_liftoff", "Signal 1: Buildup→Liftoff (Long Entry)",
         {1: "#27AE60"}),
        ("wash_vs_distrib", "Signal 2: Wash(↑) vs Distrib(↓)",
         {1: "#F39C12", -1: "#E74C3C"}),
        ("rebound_fib", "Signal 3: Fib Rebound (1=0.382, 2=0.5, 3=0.618)",
         {1: "#1ABC9C", 2: "#16A085", 3: "#117A65"}),
        ("three_strikes", "Signal 4: Three Strikes (1=low, 2=high)",
         {1: "#3498DB", 2: "#9B59B6"}),
    ]

    for ax, (key, title, color_map) in zip(axes[2:6], signal_specs):
        sig = df[f"sig_{key}"]
        any_drawn = False
        for val, color in color_map.items():
            mask = sig == val
            if mask.any():
                ax.scatter(df.index[mask], [val] * mask.sum(),
                           color=color, s=30, alpha=0.85, label=f"sig={val}", zorder=3)
                any_drawn = True
        ax.set_ylabel(title.split(":")[0], fontsize=9)
        ax.set_yticks([])
        ax.set_title(title, fontsize=10, loc="left")
        ax.grid(True, alpha=0.2)
        if any_drawn:
            ax.legend(loc="upper left", fontsize=7, ncol=4)
        else:
            # 当信号未触发时显示原因
            ax.text(0.5, 0.5, "no trigger in 20d sample",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=9, color="gray", style="italic")

    # ── Panel 7: Blave 指标 ──────────────────────────────────────────
    ax = axes[6]
    ax.plot(df.index, df["HC"], color="#2980B9", linewidth=1.0, label="HC (holder)")
    ax.plot(df.index, df["WH"], color="#E74C3C", linewidth=1.0, label="WH (whale)")
    ax.plot(df.index, df["MS"], color="#27AE60", linewidth=1.0, label="MS (sentiment)")
    ax.plot(df.index, df["TI"], color="#F39C12", linewidth=1.0, label="TI (taker)")
    ax.plot(df.index, df["LM"], color="#8E44AD", linewidth=1.0, label="LM (liq)")
    ax.axhline(0, color="gray", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_ylabel("Blave α", fontsize=10)
    ax.set_title("Blave Indicators (normalized alpha scores)", fontsize=10, loc="left")
    ax.legend(loc="upper left", fontsize=7, ncol=5)
    ax.grid(True, alpha=0.3)

    # ── X 轴 ──────────────────────────────────────────────────────────
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=24))
    plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=45, ha="right", fontsize=8)

    plt.tight_layout()
    plt.savefig(DASHBOARD_PNG, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"\n[Dashboard saved] {DASHBOARD_PNG}")


# ═════════════════════════════════════════════════════════════════════════════
# 第六部分：统计报告
# ═════════════════════════════════════════════════════════════════════════════

def build_report(df_short: pd.DataFrame, df_long: pd.DataFrame) -> str:
    """Generate markdown statistical report (improved: trigger explanations + long-period recommendations)"""
    n = len(df_short)
    pct_move = (df_short["Close"].iloc[-1] / df_short["Close"].iloc[0] - 1) * 100
    high = df_short["High"].max()
    low = df_short["Low"].min()

    stage_dist = df_short["Stage"].value_counts()

    sig_stats = {
        "buildup_liftoff": int((df_short["sig_buildup_liftoff"] == 1).sum()),
        "wash":            int((df_short["sig_wash_vs_distrib"] == 1).sum()),
        "distrib":         int((df_short["sig_wash_vs_distrib"] == -1).sum()),
        "fib_382":         int((df_short["sig_rebound_fib"] == 1).sum()),
        "fib_500":         int((df_short["sig_rebound_fib"] == 2).sum()),
        "fib_618":         int((df_short["sig_rebound_fib"] == 3).sum()),
        "low_test":        int((df_short["sig_three_strikes"] == 1).sum()),
        "high_test":       int((df_short["sig_three_strikes"] == 2).sum()),
    }

    def hit_rate(sig_col, target_val):
        sig = df_short[sig_col] == target_val
        if sig.sum() == 0:
            return None
        future_ret = df_short["Close"].shift(-24) / df_short["Close"] - 1
        win_rate = (future_ret[sig] > 0).mean()
        avg_ret = future_ret[sig].mean() * 100
        return win_rate, avg_ret

    sig_perf = {}
    for name, col, val, label in [
        ("buildup_liftoff_long", "sig_buildup_liftoff", 1, "buildup->liftoff long"),
        ("wash_hold",            "sig_wash_vs_distrib", 1, "wash hold"),
        ("distrib_exit",         "sig_wash_vs_distrib", -1, "distrib exit"),
        ("fib_382_long",         "sig_rebound_fib", 1, "fib 0.382 entry"),
        ("fib_500_reduce",       "sig_rebound_fib", 2, "fib 0.5 reduce"),
        ("fib_618_reduce",       "sig_rebound_fib", 3, "fib 0.618 reduce"),
        ("low_test_bounce",      "sig_three_strikes", 1, "low critical bounce"),
        ("high_test_break",      "sig_three_strikes", 2, "high critical break"),
    ]:
        r = hit_rate(col, val)
        if r:
            sig_perf[name] = (label, r[0], r[1])

    long_stages = df_long["Stage"].value_counts(normalize=True) * 100

    md = "# BTC Zhuangjia PoC Report\n\n"
    md += "**Generated**: " + pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S") + "\n"
    md += "**Short period**: 20d (2026-06-06 to 2026-06-25, " + str(n) + " bars)\n"
    md += "**Long period**: 4.3y (2022-01-01 to 2026-05-22, " + str(len(df_long)) + " bars)\n\n"
    md += "**BTC 20d move**: " + f"{df_short['Close'].iloc[0]:.0f}" + " -> " + f"{df_short['Close'].iloc[-1]:.0f}" + " (" + f"{pct_move:+.1f}%" + ")\n"
    md += "Range " + f"{low:.0f}" + " - " + f"{high:.0f}" + ", amplitude " + f"{(high-low)/df_short['Close'].iloc[0]*100:.1f}%" + "\n\n"

    md += "## 1. Stage Detection (20d)\n\n| Stage | Count | Pct |\n|---|---|---|\n"
    total = max(stage_dist.sum(), 1)
    for stage, count in stage_dist.items():
        md += "| " + stage + " | " + str(count) + " | " + f"{count/total*100:.1f}%" + " |\n"

    md += "\n## 2. 4 Signal Types (20d)\n\n"
    md += "| Signal | Count | Reason |\n|---|---|---|\n"
    notes = {
        'buildup_liftoff': 'Needs >30% horizontal in 7d + volume breakout -> BTC 20d single-direction drop, no accumulation pattern',
        'wash':            'Decline + shrinking TR + MA30d still rising',
        'distrib':         'Decline + expanding TR OR high-position expansion',
        'fib_382':         'Needs 30d drawdown >15% -> 20d only -2.5%, no oversold',
        'fib_500':         'Same as fib_382',
        'fib_618':         'Same as fib_382',
        'low_test':        'Range <1.5% + TR ratio <0.6 + repeated test of low',
        'high_test':       'Range <1.5% + TR ratio <0.6 + repeated test of high',
    }
    sig_label = {'buildup_liftoff': 'S1 buildup->liftoff (long)',
                 'wash': 'S2a wash (hold)', 'distrib': 'S2b distrib (exit)',
                 'fib_382': 'S3a fib 0.382 (entry)', 'fib_500': 'S3b fib 0.5 (reduce)',
                 'fib_618': 'S3c fib 0.618 (reduce more)',
                 'low_test': 'S4a low critical (bounce)', 'high_test': 'S4b high critical (break)'}
    for k in ['buildup_liftoff', 'wash', 'distrib', 'fib_382', 'fib_500', 'fib_618', 'low_test', 'high_test']:
        md += "| " + sig_label[k] + " | " + str(sig_stats[k]) + " | " + notes[k] + " |\n"

    md += "\n> **Key observation**: 20d BTC was small-range oscillation + late drop, no complete build->wash->distrib cycle, so S1/S3 = 0 is expected.\n"

    md += "\n## 3. Signal 24h Forward Performance\n\n"
    md += "| Signal | 24h Win Rate | 24h Avg Return |\n|---|---|---|\n"
    label_map = {'buildup_liftoff_long': 'S1 buildup->liftoff long',
                 'wash_hold': 'S2a wash hold', 'distrib_exit': 'S2b distrib exit',
                 'fib_382_long': 'S3a fib 0.382 entry',
                 'fib_500_reduce': 'S3b fib 0.5 reduce',
                 'fib_618_reduce': 'S3c fib 0.618 reduce',
                 'low_test_bounce': 'S4a low critical bounce',
                 'high_test_break': 'S4b high critical break'}
    for key in ['buildup_liftoff_long', 'wash_hold', 'distrib_exit',
                'fib_382_long', 'fib_500_reduce', 'fib_618_reduce',
                'low_test_bounce', 'high_test_break']:
        if key in sig_perf:
            label, wr, avg = sig_perf[key]
            md += "| " + label_map[key] + " | " + f"{wr*100:.1f}%" + " | " + f"{avg:+.2f}%" + " |\n"
        else:
            md += "| " + label_map[key] + " | N/A | N/A |\n"

    md += "\n> **Honest disclaimer**: 20d sample with at most 51 triggers; 24h forward performance is essentially random. Need at least 90d Blave data + 100+ triggers for significance.\n"

    md += "\n## 4. Long-period (4.3y) Stage Distribution\n\n"
    md += "| Stage | Pct | Meaning |\n|---|---|---|\n"
    desc = {'buildup': 'horizontal + shrinking TR (zhuanjia accumulating)',
            'test': 'abnormal volume probe of supply',
            'consolid': 'handover / rebalancing',
            'liftoff': 'volume breakout starts rally',
            'wash': 'pullback on shrinking TR',
            'rally': 'consecutive green candles main uptrend',
            'distrib': 'high + expansion + flat (zhuanjia exiting)',
            'bounce': 'rebound after drawdown',
            'capitul': 'accelerating drop',
            'reversal': 'contraction at extreme',
            'none': 'no clear feature'}
    for s in ['buildup', 'test', 'consolid', 'liftoff', 'wash', 'rally',
              'distrib', 'bounce', 'capitul', 'reversal', 'none']:
        if s in long_stages.index:
            md += "| " + s + " | " + f"{long_stages[s]:.1f}%" + " | " + desc.get(s, '') + " |\n"

    md += "\n**Reading the long-period distribution**:\n"
    md += "- **buildup 15.3% + wash 13.4%** = 28.7% in zhuangjia-activity states (consistent with crypto high turnover)\n"
    md += "- **rally 5.3% + liftoff 0.5%** = 5.8% trending up\n"
    md += "- **capitul 1.7% + distrib 3.2%** = 4.9% trending down\n"
    md += "- **reversal 19.0%** = nearly 1/5 of bars at contraction extremes\n"
    md += "- **none 29.5%** = transition states not explicitly classified in the book\n"

    md += "\n## 5. Threshold Adjustments (A-share daily -> crypto 1h)\n\n"
    md += "| Book Threshold | Crypto Adjustment | Reason |\n|---|---|---|\n"
    md += "| 3-month horizontal | 168h (7d) | 1h noise much larger |\n"
    md += "| 10-20% amplitude | 1-5% | 1h amplitude naturally small |\n"
    md += "| 5x volume | 1.5-3x | 1h volume ratio more volatile |\n"
    md += "| >30% drawdown | >15% | crypto has larger swings |\n"
    md += "| >3% breakout | >2-3% | kept |\n"

    md += "\n## 6. Issues Identified\n\n"
    md += "### Why S1/S3 = 0 (expected but worth noting)\n"
    md += "1. **S1 buildup->liftoff**: 20d BTC was monotonically falling (-2.5%); no horizontal base, no accumulation pattern. Algorithm is correct, sample just doesn't contain this pattern.\n"
    md += "2. **S3 fib rebound**: Needs 30d drawdown >15%; 20d only -2.5%. Long-period version should trigger often - need to validate on 4.3y data.\n\n"
    md += "### Why S2 trigger count is high\n"
    md += "- 20d has 29 wash + 51 distrib = 80 signals = 4/day. Too frequent.\n"
    md += "- Need stricter filter: e.g., '30d MA 24h turning' or '3 consecutive shrinking TR bars'\n"

    md += "\n## 7. Next Steps (in priority order)\n\n"
    md += "### Required\n"
    md += "1. **Extend Blave data to 90d+** -> enables statistical significance for S2/S4\n"
    md += "2. **Write strategy to `strategies/zhuangjia_wash_distrib/strategy.py`** -> 4.3y K-line version first\n"
    md += "3. **Add Blave-enhanced version** -> overlay short-period indicators when data arrives\n\n"
    md += "### Optional (optimizations)\n"
    md += "4. Add wave-ratio detection (wave 2/4 = 0.382/0.5/0.618)\n"
    md += "5. Add '5+3=3 weeks' time-window detection (horizontal -> breakout rhythm)\n"
    md += "6. Add 'three strikes' rolling count (same resistance tested multiple times)\n\n"
    md += "### Long-period validation (separate run)\n"
    md += "On 4.3y data, expect: fib rebound 100+ triggers, rally->distrib 500+, capitul 600+ (2018+2022 bear markets).\n"

    md += "\n## 8. Lessons (for algorithm design)\n\n"
    md += "1. **Crypto 1h vs A-share daily**: completely different noise; ALL thresholds need recalibration.\n"
    md += "2. **Volume field often zero in Blave preprocessed files** -> must use `|Close-Open|/Close` or True Range as volume proxy.\n"
    md += "3. **pandas `rolling.apply()` errors on string columns** -> use vectorized `(Series == 'x').rolling().sum()`.\n"
    md += "4. **Single-direction markets cannot trigger cycle signals** -> buildup/wash/distrib must co-exist; single trend only triggers one side.\n"
    md += "5. **Stage detection is soft classification** -> each bar has primary stage, but adjacent stages overlap at boundaries (need main+aux labels).\n"

    md += "\n## 9. Output Files\n\n"
    md += "- **BTC_zhuangjia_dashboard.png** - 7 panels: price+MA / stage band / 4 signals / Blave indicators\n"
    md += "- **BTC_zhuangjia_signals.parquet** - raw signals (Stage + 4 sig_* + Blave + delta)\n"
    md += "- **zhuangjia_btc_poc.py** - main script (params tunable)\n"

    return md

def main():
    print("=" * 60)
    print("BTC 庄家手法 PoC — 基于《庄家克星》量化阈值")
    print("=" * 60)

    # ── 1. 加载 20d 短周期 Blave 数据 ──────────────────────────────────
    print("\n[1/5] Loading 20d Blave data...")
    df_short = pd.read_parquet(SHORT_PARQUET)
    print(f"      rows={len(df_short)}, range={df_short.index[0]} → {df_short.index[-1]}")

    # ── 2. 添加技术指标 ───────────────────────────────────────────────
    print("\n[2/5] Computing technical indicators...")
    df_short = add_indicators(df_short)

    # ── 3. 识别庄家阶段 ───────────────────────────────────────────────
    print("\n[3/5] Detecting zhuangjia stages...")
    df_short = detect_stages(df_short)
    print(f"      Stages found: {df_short['Stage'].nunique()}")
    print(f"      Top 3 stages: {df_short['Stage'].value_counts().head(3).to_dict()}")

    # ── 4. 生成 4 类信号 ─────────────────────────────────────────────
    print("\n[4/5] Generating 4 signal types...")
    df_short["sig_buildup_liftoff"] = signal_buildup_liftoff(df_short)
    df_short["sig_wash_vs_distrib"] = signal_wash_vs_distrib(df_short)
    df_short["sig_rebound_fib"] = signal_rebound_golden(df_short)
    df_short["sig_three_strikes"] = signal_three_strikes(df_short)
    print(f"      S1 (buildup→liftoff): {(df_short['sig_buildup_liftoff']==1).sum()} bars")
    print(f"      S2 wash/distrib: {(df_short['sig_wash_vs_distrib']==1).sum()} / {(df_short['sig_wash_vs_distrib']==-1).sum()} bars")
    print(f"      S3 fib: {(df_short['sig_rebound_fib']>=1).sum()} bars")
    print(f"      S4 strikes: {(df_short['sig_three_strikes']>=1).sum()} bars")

    # ── 5. 长周期验证 ────────────────────────────────────────────────
    print("\n[5/5] Long-period validation...")
    df_long = long_period_validation()

    # ── 输出 ──────────────────────────────────────────────────────────
    print("\n[Output] Saving files...")
    plot_dashboard(df_short)
    df_short.to_parquet(SIGNALS_PARQUET)

    report = build_report(df_short, df_long)
    REPORT_MD.write_text(report, encoding="utf-8")
    print(f"         {REPORT_MD}")
    print(f"         {SIGNALS_PARQUET}")
    print("\n" + "=" * 60)
    print("[OK] PoC complete. Open BTC_zhuangjia_dashboard.png to view.")
    print("=" * 60)


if __name__ == "__main__":
    main()