"""
BBAC-D × chan.py bridge (BTCUSDT)
==================================
把 BBAC-D 缓存的 BTCUSDT parquet K 线数据接入 chan.py 框架,
在 BBAC-D 项目内做缠论可视化。

与 live_eval/ 独立:本工具基于 chan.py(笔/段/中枢/买卖点),
live_eval/ 基于形态识别 + 量价评分,两套思路不同,产物分开存放。

当前聚焦单一标的 BTCUSDT(暂不扩展到多币种)。

Usage:
    python core/chan_plot.py --freq 1h --days 30  --x-range 500 --tag 30d
    python core/chan_plot.py --freq 4h --days 180 --x-range 200 --tag 180d
    python core/chan_plot.py --freq 1d --days 1095 --x-range 200 --tag 3y
    python core/chan_plot.py --freq 1h --days 730 --x-range 600 --tag 2y

(symbol 参数可省略,默认 BTCUSDT。传其他值会报错退出)

默认输出: BBAC-D/chan_view/chan_<SYMBOL>_<FREQ>_<TAG>.png
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# --- path setup:BBAC-D 项目根 + 内嵌的 vendor/chan 框架 ---
import _bootstrap  # noqa: F401  — BBAC-D sys.path
import os, argparse
from pathlib import Path

# chan.py 已经内嵌在 BBAC-D/vendor/chan/ 下(随项目走,完全独立,不依赖桌面原版)
_VENDOR_CHAN = Path(_bootstrap._ROOT) / "vendor" / "chan"
if str(_VENDOR_CHAN) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CHAN))

import matplotlib
matplotlib.use("Agg")  # 服务器无显示器,只 save 不弹窗

# ── 字体策略:YaHei 优先(SimHei 无 bold 字重) ───────
# SimHei 在 Windows 上只有 weight=400(没有 bold)。
# Microsoft YaHei 有完整的 weight=400 和 weight=700(bold = msyhbd.ttc)。
# 把 Microsoft YaHei 放在 sans-serif 第一位 → 中文字符正常渲染,
# 且 fontweight='bold' 时会自动用 msyhbd.ttc(真正的粗体),不再回退。
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei',
                                          'PingFang SC', 'Arial Unicode MS',
                                          'DejaVu Sans']
matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['axes.unicode_minus'] = False

import matplotlib.pyplot as plt
import pandas as pd

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE
from Common.CTime import CTime
from Common.CEnum import DATA_FIELD
from KLine.KLine_Unit import CKLine_Unit
from Plot.PlotDriver import CPlotDriver


# ── 1. K 线频率映射 ──────────────────────────────────
# FREQ_MAP 是「目标频率 → KL_TYPE 枚举」
# 4h 在 chan.py 里没有原生枚举(最小是 1h),但 KLine 合并与笔/段计算不依赖精确频率,
# 只用 K_60M 喂 K 线即可 — 缠论只关心「相邻关系」,不关心「精确到小时」语义。
FREQ_MAP = {
    "1m":  KL_TYPE.K_1M,
    "5m":  KL_TYPE.K_5M,
    "15m": KL_TYPE.K_15M,
    "30m": KL_TYPE.K_30M,
    "1h":  KL_TYPE.K_60M,
    "4h":  KL_TYPE.K_60M,   # 用 K_60M 喂「4h 聚合后的 K 线」
    "1d":  KL_TYPE.K_DAY,
    "1w":  KL_TYPE.K_WEEK,
}


# ── 2. 数据加载 ─────────────────────────────────────
def load_kline_parquet(symbol: str, freq: str) -> pd.DataFrame:
    """
    从 BBAC-D/cache/ 加载某 symbol 在指定频率下的 K 线 parquet。

    **严格匹配 freq**:只找 kline_<freq>_<SYMBOL>_*.parquet,找不到直接报错。
    不允许静默降级(避免"1d 视图用 1h 合成"这种隐含失真)。
    如果缺数据,先跑 core/fetch_btcusdt.py 拉对应频率。

    返回 DataFrame:index=datetime, columns=[Open,High,Low,Close,Volume]
    """
    cache_dir = Path(_bootstrap._ROOT) / "cache"
    patterns = [
        f"kline_{freq}_{symbol}_*.parquet",
        f"kline_{freq}_{symbol.lower()}_*.parquet",
    ]
    files = []
    for pat in patterns:
        files = sorted(cache_dir.glob(pat))
        if files:
            break

    if not files:
        raise FileNotFoundError(
            f"cache/ 下找不到 kline_{freq}_{symbol}_*.parquet\n"
            f"  → 请先拉取该频率数据:\n"
            f"     python core/fetch_btcusdt.py --freq {freq}\n"
            f"  → 已有频率:1h (4.5 年数据)"
        )

    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    # 兜底:tz-aware → tz-naive
    if df.index.tz is not None:
        df.index = df.index.tz_convert('UTC').tz_localize(None)
    return df


def resample_ohlcv(df: pd.DataFrame, target_freq: str) -> pd.DataFrame:
    """
    重采样到目标频率。target_freq 支持:
      '4h' / '1d' / '1w' / '15m'(原样返回)

    Volume 全为 0 → 不聚合,直接取 last 即可。
    """
    if target_freq == "15m":
        return df

    rule_map = {
        "30m": "30min",
        "1h":  "1h",
        "4h":  "4h",
        "1d":  "1D",
        "1w":  "1W",
    }
    rule = rule_map.get(target_freq)
    if rule is None:
        raise ValueError(f"不支持的重采样频率: {target_freq}")

    # OHLC 标准聚合(Volume 是 0,这里用 sum 也无所谓)
    agg = {
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }
    resampled = df.resample(rule).agg(agg).dropna(subset=["Open"])
    return resampled


# ── 3. K 线 → CKLine_Unit 列表 ─────────────────────
def df_to_klu_list(df: pd.DataFrame) -> list:
    """
    DataFrame 转 chan.py 内部 CKLine_Unit。
    注意:分钟级必须 CTime(auto=False),否则 0 点 0 分数据会被错位到前一天 23:59。
    """
    klu_list = []
    for ts, row in df.iterrows():
        ctime = CTime(ts.year, ts.month, ts.day, ts.hour, ts.minute, auto=False)
        # Volume 是 0 → 传 None(可选字段,None 不会被画/不会被用到)
        vol = float(row["Volume"]) if row["Volume"] > 0 else None
        klu = CKLine_Unit({
            DATA_FIELD.FIELD_TIME:   ctime,
            DATA_FIELD.FIELD_OPEN:   float(row["Open"]),
            DATA_FIELD.FIELD_HIGH:   float(row["High"]),
            DATA_FIELD.FIELD_LOW:    float(row["Low"]),
            DATA_FIELD.FIELD_CLOSE:  float(row["Close"]),
            DATA_FIELD.FIELD_VOLUME: vol,
            # 没有 amount / turnover_rate → 不传
        })
        klu_list.append(klu)
    return klu_list


# ── 3.5 Blave 7 维 alpha 注入 ──────────────────
# 支持 6 个算法维度(ms/cs/hc/ti/wh/bt,sm 固定 1d 不在此处注入)
BLAVE_ALPHA_INDICATORS = ("ms", "cs", "hc", "ti", "wh", "bt")


def load_alpha_dict(symbol: str, freq: str) -> dict:
    """读取所有 Blave alpha parquet → {indicator: DataFrame(time, alpha)}"""
    cache_dir = Path(_bootstrap._ROOT) / "cache"
    result = {}
    for ind in BLAVE_ALPHA_INDICATORS:
        if ind in ("bt", "cs"):  # bt/cs 无 symbol
            files = sorted(cache_dir.glob(f"{ind}_*_*.parquet"))
        else:
            files = sorted(cache_dir.glob(f"{ind}_{freq}_{symbol}_*.parquet"))
        # 同 freq 不同起点的文件
        files += sorted(cache_dir.glob(f"{ind}_{freq}_*_{symbol}_*.parquet"))
        if not files:
            continue
        dfs = [pd.read_parquet(f) for f in files]
        df = pd.concat(dfs).sort_index()
        df = df[~df.index.duplicated(keep='last')]
        if df.index.tz is not None:
            df.index = df.index.tz_convert('UTC').tz_localize(None)
        result[ind] = df
    return result


def inject_alpha_into_klu(klu_list: list, alpha_dict: dict):
    """把 alpha 值注入到每根 KLU 的 self.alpha 字典。"""
    if not alpha_dict:
        return 0  # 没数据,跳过
    n_injected = 0
    for klu in klu_list:
        for ind, df in alpha_dict.items():
            # 找到最接近的时间点(<= klu.time 的最新 alpha)
            ts = pd.Timestamp(klu.time.year, klu.time.month, klu.time.day,
                              klu.time.hour, klu.time.minute)
            try:
                # 用 asof 找最近的历史 alpha
                val = df['alpha'].asof(ts)
                if pd.notna(val):
                    klu.inject_alpha(ind, float(val))
                    n_injected += 1
            except Exception:
                pass
    return n_injected


# ── 4. 喂数据 + 计算缠论元素 ─────────────────────
def compute_chan(klu_list: list, lv: KL_TYPE, symbol: str, config_overrides: dict = None) -> CChan:
    """
    直接用 trigger_load 喂数据(无需 DataAPI / csv 文件)。
    返回算好缠论元素的 CChan 实例。
    """
    if config_overrides is None:
        config_overrides = {}

    defaults = {
        "bi_strict":      True,
        # trigger_step=True → CChan.__init__ 不读数据、不计算
        # 后面用 trigger_load() 喂数据 + 一次性算完所有元素
        "trigger_step":   True,
        "divergence_rate": 0.9,
        "min_zs_cnt":     1,
        "max_bs2_rate":   0.618,
        "bs_type":        "1,1p,2,2s,3a,3b",
        "print_warning":  False,
        # BBAC-D:不合并中枢(让每个原始中枢都显示)
        "need_combine":   False,
    }
    defaults.update(config_overrides)

    config = CChanConfig(defaults)

    chan = CChan(
        code=symbol,
        begin_time=None,
        end_time=None,
        data_src=DATA_SRC.CSV,  # 占位,trigger_step=True 时不会触发
        lv_list=[lv],
        config=config,
        autype=AUTYPE.NONE,
    )
    # trigger_load 会一次性算完所有缠论元素
    chan.trigger_load({lv: klu_list})
    return chan


# ── 5. 画图 ────────────────────────────────────────
PLOT_CONFIG_DEFAULT = {
    "plot_kline":         True,
    "plot_kline_combine": True,
    "plot_bi":            True,
    "plot_seg":           True,
    "plot_eigen":         False,
    "plot_zs":            True,
    "plot_bsp":           True,
    "plot_segbsp":        True,
    "plot_demark":        False,
    "plot_marker":        False,
    "plot_rsi":           False,
    "plot_kdj":           False,
    "plot_macd":          False,
    "plot_mean":          False,
    "plot_channel":       False,
    "plot_alpha":         True,   # BBAC-D:Blave 7 维 alpha 副图
}


def write_stats_json(symbol: str, freq: str, tag: str,
                     df: pd.DataFrame, chan: CChan, out_dir: Path,
                     bi_strict: bool, divergence_rate: float):
    """把单张视图的统计信息写到 chan_view/stats_<SYMBOL>_<FREQ>_<TAG>.json。

    包含:视图参数、缠论元素计数、最近 BSP、当前价、关键中枢。
    """
    import json

    kl_data = chan[0]
    last_close = _get_last_close(chan)

    # 全部 BSP(按时间序)— 用于核查图上是否都画了
    bsp_list = kl_data.bs_point_lst.getSortedBspList()
    all_bsp = []
    for bsp in bsp_list:
        # bsp.features 里有 chan.py 计算的特征(背驰度、回撤率等)
        features = {}
        try:
            for k, v in bsp.features.items():
                if isinstance(v, (int, float, str, bool)):
                    features[k] = v
        except Exception:
            pass
        all_bsp.append({
            "is_buy":   bool(bsp.is_buy),
            "types":    [t.value for t in bsp.type],   # e.g. ["1", "1p"]
            "klu_idx":  bsp.klu.idx,
            "time":     str(bsp.klu.time),
            "price":    round(float(bsp.klu.low if bsp.is_buy else bsp.klu.high), 2),
            "features": features,  # 背驰度、回撤率等(由 chan.py 计算)
        })

    # 全部多笔中枢
    multi_zs = [zs for zs in kl_data.zs_list if not zs.is_one_bi_zs()]
    all_zs = []
    for zs in multi_zs:
        all_zs.append({
            "low":     round(float(zs.low), 2),
            "high":    round(float(zs.high), 2),
            "is_sure": bool(zs.is_sure),
            "begin":   str(zs.begin.time) if hasattr(zs.begin, 'time') else None,
            "end":     str(zs.end.time)   if hasattr(zs.end,   'time') else None,
        })

    # 当前段(最后一段,如果存在)
    current_seg = None
    if kl_data.seg_list:
        seg = kl_data.seg_list[-1]
        current_seg = {
            "is_sure": bool(seg.is_sure),
            "dir":     seg.dir.name,           # UP / DOWN
            "bi_count": int(seg.end_bi.idx - seg.start_bi.idx + 1),
        }

    stats = {
        "symbol":     symbol,
        "freq":       freq,
        "tag":        tag,
        "generated":  pd.Timestamp.now().isoformat(),
        "data_source": "native",  # 原生 freq 数据(不再有合成)
        "params": {
            "bi_strict":       bi_strict,
            "divergence_rate": divergence_rate,
        },
        "data_range": {
            "start":    str(df.index.min()),
            "end":      str(df.index.max()),
            "n_kline":  int(len(df)),
        },
        "last_close": round(last_close, 2),
        "elements": {
            "n_bi":  len(kl_data.bi_list),
            "n_seg": len(kl_data.seg_list),
            "n_zs":  len(kl_data.zs_list),
            "n_zs_multi_bi": len(multi_zs),
            "n_bsp": len(bsp_list),
        },
        "current_seg": current_seg,
        "all_bsp":      all_bsp,       # 全部 BSP(用于核查 vs 图上标注数)
        "all_zs":       all_zs,        # 全部多笔中枢
        "recent_bsp":   all_bsp[-3:],  # 兼容旧字段:最近 3 个
        "key_zs":       all_zs[-3:],   # 兼容旧字段:最近 3 个中枢
    }

    out_path = out_dir / f"stats_{symbol}_{freq}_{tag}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"      stats: {out_path.name}")


def plot_chan(chan: CChan, title_suffix: str = "", x_range: int = 200) -> CPlotDriver:
    """画缠论图。返回 driver 实例,图存在 driver.figure。"""
    plot_para = {
        "figure": {"x_range": x_range, "w": 24, "h": 10, "x_tick_num": 10,
                   "alpha_subplot": True},  # BBAC-D:加 alpha 副图
        "bi":    {"color": "black", "disp_end": True},
        "seg":   {"color": "g", "width": 5, "disp_end": False},
        "zs":    {"color": "orange", "linewidth": 2, "sub_linewidth": 0.5},
        "bsp":   {"buy_color": "r", "sell_color": "g", "fontsize": 15},
        "kl":    {"width": 0.4, "rugd": True},
        "alpha": {"indicators": ("ms", "cs", "hc", "ti", "wh", "bt")},
    }
    driver = CPlotDriver(chan, plot_config=PLOT_CONFIG_DEFAULT, plot_para=plot_para)

    # 改标题
    code = chan.code
    lv_name = chan.lv_list[0].name.split("K_")[1] if chan.lv_list else "?"
    ax = driver.figure.axes[0]
    ax.set_title(f"{code} / {lv_name} {title_suffix}",
                 fontsize=18, loc="left", color="red")

    # ── Overlay 增强:当前价高亮 + 最近买卖点放大 ──
    overlay_current_price(driver, ax, chan)
    highlight_recent_bsp(driver, ax, chan, n=2)

    return driver


def _get_last_close(chan: CChan) -> float:
    """从 chan 取最后一根 K 线的 close(真实当前价)。"""
    try:
        klu_list = chan[0].lst  # CKLine_List
        # 最后一根合并 K 线的最后一根 klu
        last_klc = klu_list[-1]
        return float(last_klc.lst[-1].close)
    except Exception:
        return 0.0


def overlay_current_price(driver: CPlotDriver, ax: "plt.Axes", chan: CChan):
    """在右侧画一条"当前价"水平虚线 + 价格文字(蓝色,高对比)。"""
    last_close = _get_last_close(chan)
    if last_close <= 0:
        return

    y_bot, y_top = ax.get_ylim()
    # 当前价在视窗内 → 才画(否则虚线会跑到图外)
    if y_bot <= last_close <= y_top:
        ax.axhline(y=last_close, color="blue", linestyle="--", linewidth=1.2, alpha=0.7)
        # 右侧文字标签
        x_max = ax.get_xlim()[1]
        ax.text(x_max, last_close, f" {last_close:,.0f} ",
                color="white", fontsize=11, fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="blue", edgecolor="blue"))


def highlight_recent_bsp(driver: CPlotDriver, ax: "plt.Axes", chan: CChan, n: int = 2):
    """把最近 n 个买卖点画一个加粗的圆圈 + 大字体(在原有 BSP 之上叠加)。"""
    bsp_list = chan[0].bs_point_lst.getSortedBspList()
    if not bsp_list:
        return

    # 取最近 n 个(按 K 线 idx 排序)
    recent = bsp_list[-n:]
    x_max = ax.get_xlim()[1]
    y_bot, y_top = ax.get_ylim()

    for bsp in recent:
        # 只画在当前 x_range 视窗内的
        if bsp.klu.idx < ax.get_xlim()[0]:
            continue
        color = "red" if bsp.is_buy else "green"
        # 文字描述
        label = bsp.type2str()  # e.g. "1", "2,3b"
        bsp_y = bsp.klu.low if bsp.is_buy else bsp.klu.high

        # 在 Y 方向上偏移一点(避开 K 线)
        y_offset = (y_top - y_bot) * 0.02
        plot_y = bsp_y - y_offset if bsp.is_buy else bsp_y + y_offset

        # 加粗圆圈
        ax.scatter([bsp.klu.idx], [plot_y], s=350, facecolors="none",
                   edgecolors=color, linewidths=2.8, zorder=10)
        # 加粗文字
        ax.text(bsp.klu.idx, plot_y, label, fontsize=18, fontweight="bold",
                color=color, zorder=11, ha="center", va="center")


# ── 6. 主入口 ─────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="BBAC-D × chan.py: 缠论可视化")
    ap.add_argument("symbol", default="BTCUSDT", nargs="?",
                    help="标的(当前仅支持 BTCUSDT)")
    ap.add_argument("--freq", default="1d", choices=list(FREQ_MAP.keys()),
                    help="目标 K 线频率(默认 1d,需从 15m 重采样)")
    ap.add_argument("--days", type=int, default=None,
                    help="只看最近 N 天(默认全部;数据量大时建议 90-180)")
    ap.add_argument("--tag", default=None,
                    help="输出文件名的附加标签(如 '30d' '180d' '3y'),不指定则用日期")
    ap.add_argument("--max-rows", type=int, default=3000,
                    help="最大 K 线根数(默认 3000,防 chan.py 递归栈溢出)")
    ap.add_argument("--x-range", type=int, default=200,
                    help="绘图 X 轴显示最近 N 根 K 线(默认 200)")
    ap.add_argument("--out", default=None,
                    help="输出 PNG 路径(默认: 项目根/chan_<symbol>_<freq>_<date>.png)")
    ap.add_argument("--bi-strict", type=lambda v: v.lower() == "true", default=True)
    ap.add_argument("--divergence-rate", type=float, default=0.9)
    ap.add_argument("--macd-algo", default=None,
                    choices=[None, "slope", "amp", "ms", "cs", "hc", "ti", "wh", "bt"],
                    help="力度算法(默认 None=ChanConfig 默认,即 amp;可选 slope/amp/ms/cs/hc/ti/wh/bt)")
    args = ap.parse_args()

    # 单一标的硬限制:当前只支持 BTCUSDT
    if args.symbol.upper() != "BTCUSDT":
        print(f"✗ 当前仅支持 BTCUSDT,收到 '{args.symbol}'", file=sys.stderr)
        sys.exit(1)

    # 1) 加载:严格匹配 freq,不允许降级
    print(f"[1/5] 加载 {args.symbol} {args.freq} 数据...")
    df_raw = load_kline_parquet(args.symbol, freq=args.freq)
    print(f"      原始: {len(df_raw)} 行, {df_raw.index.min()} → {df_raw.index.max()}")
    df = df_raw  # 不再重采样 — 必须用原生数据

    print(f"      最终: {len(df)} 行(原生 {args.freq},无重采样)")

    # 3) 取最近 N 天
    if args.days:
        cutoff = df.index.max() - pd.Timedelta(days=args.days)
        df = df[df.index >= cutoff]
        print(f"      取最近 {args.days}d → {len(df)} 行")

    # 3.5) 数据量保护(超过 max-rows 自动截断)
    if len(df) > args.max_rows:
        before = len(df)
        df = df.iloc[-args.max_rows:]
        print(f"      [limit] 截断 {before} → {len(df)} 行(max-rows={args.max_rows})")

    # 4) 计算缠论
    lv = FREQ_MAP[args.freq]
    print(f"[3/5] 计算缠论元素(KL_TYPE={lv})...")
    klu_list = df_to_klu_list(df)
    # BBAC-D:注入 Blave 7 维 alpha(ms/cs/hc/ti/wh/bt)
    alpha_dict = load_alpha_dict(args.symbol, args.freq)
    n_alpha = inject_alpha_into_klu(klu_list, alpha_dict)
    if alpha_dict:
        print(f"      [alpha] 注入 {len(alpha_dict)} 维 Blave alpha, 共 {n_alpha} 个 KLU 赋值")
    chan = compute_chan(klu_list, lv, args.symbol, {
        "bi_strict":      args.bi_strict,
        "divergence_rate": args.divergence_rate,
        **({"macd_algo": args.macd_algo} if args.macd_algo else {}),
    })
    n_bi  = len(chan[0].bi_list)
    n_seg = len(chan[0].seg_list)
    n_zs  = len(chan[0].zs_list)
    n_bsp = len(chan[0].bs_point_lst)
    print(f"      Bi={n_bi}, Seg={n_seg}, ZS={n_zs}, BSP={n_bsp}")

    # 4.5) 自动放大 x_range:如果用户传的 x_range 太窄导致 BSP 被切,自动放大
    n_kline = len(df)
    xlim_left_default = max(0, n_kline - args.x_range)
    bsp_in_xlim = [bsp for bsp in chan[0].bs_point_lst.getSortedBspList()
                   if bsp.klu.idx >= xlim_left_default]
    bsp_out_xlim = [bsp for bsp in chan[0].bs_point_lst.getSortedBspList()
                    if bsp.klu.idx < xlim_left_default]
    if bsp_out_xlim:
        first_bsp_idx = min(bsp.klu.idx for bsp in bsp_out_xlim)
        x_range_eff = n_kline - first_bsp_idx + 10
        print(f"      [xrange] {len(bsp_out_xlim)}/{n_bsp} 个 BSP 在 x_range 之外 → "
              f"自动放大 {args.x_range} → {x_range_eff}")
    else:
        x_range_eff = args.x_range

    # 5) 画图
    print(f"[4/5] 画图(x_range={x_range_eff})...")
    title_suffix = f"| strict={args.bi_strict} div={args.divergence_rate}"
    driver = plot_chan(chan, title_suffix=title_suffix, x_range=x_range_eff)

    # 6) 保存
    if args.out:
        out_path = Path(args.out)
    else:
        # 用 --tag 当文件标识(如 '30d' / '180d' / '3y'),否则用截止日期
        tag = args.tag if args.tag else df.index.max().strftime("%Y%m%d")
        # 默认输出到 chan_view/(独立的缠论可视化目录,不与 live_eval 混)
        out_path = Path(_bootstrap._ROOT) / "chan_view" / f"chan_{args.symbol}_{args.freq}_{tag}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[5/5] 保存到 {out_path}...")
    driver.figure.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(driver.figure)

    # 7) 配套 stats.json(便于程序读取,不依赖 OCR 图)
    tag = args.tag if args.tag else df.index.max().strftime("%Y%m%d")
    write_stats_json(
        symbol=args.symbol, freq=args.freq, tag=tag,
        df=df, chan=chan,
        out_dir=Path(_bootstrap._ROOT) / "chan_view",
        bi_strict=args.bi_strict, divergence_rate=args.divergence_rate,
    )
    print(f"✓ 完成: {out_path}")


if __name__ == "__main__":
    main()
