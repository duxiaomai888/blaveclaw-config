"""
chan_plot_multi.py — BTCUSDT 多级别联立图
========================================
画两张子图(1d + 4h)并排/上下排,然后用 PIL 拼接成一张 PNG。

对标 chan.py 项目里的 chan.py_image_7.png(多级别联动)。

Usage:
    python core/chan_plot_multi.py
    python core/chan_plot_multi.py --out chan_view/chan_BTCUSDT_multi.png
    python core/chan_plot_multi.py --layout horizontal
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 路径
import _bootstrap  # noqa
from pathlib import Path
import matplotlib

# chan.py 内嵌 vendor/chan
_VENDOR_CHAN = Path(_bootstrap._ROOT) / "vendor" / "chan"
if str(_VENDOR_CHAN) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CHAN))
matplotlib.use("Agg")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei',
                                          'PingFang SC', 'Arial Unicode MS',
                                          'DejaVu Sans']
matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['axes.unicode_minus'] = False

import argparse
import pandas as pd

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit
from Plot.PlotDriver import CPlotDriver

from chan_plot import (load_kline_parquet, resample_ohlcv, df_to_klu_list,
                       compute_chan, PLOT_CONFIG_DEFAULT, _get_last_close,
                       overlay_current_price, highlight_recent_bsp)


# ── 联立视图定义 ─────────────────────────────────
MULTI_VIEW = {
    "top":    dict(freq="1d", days=365,  x_range=200, desc="日线(大级别趋势)"),
    "bottom": dict(freq="4h", days=90,   x_range=300, desc="4小时(中级别进场)"),
}


def render_one_view(freq: str, days: int, x_range: int, desc: str, w: int = 24, h: int = 8):
    """画单级别子图 → 返回 matplotlib Figure。严格用原生 freq 数据。"""
    df = load_kline_parquet("BTCUSDT", freq=freq)  # 严格匹配 freq,不允许合成
    cutoff = df.index.max() - pd.Timedelta(days=days)
    df = df[df.index >= cutoff]
    if len(df) > 3000:
        df = df.iloc[-3000:]
    print(f"  rows={len(df)}  range={df.index.min()} → {df.index.max()}")

    lv = {"1m": KL_TYPE.K_1M, "5m": KL_TYPE.K_5M, "15m": KL_TYPE.K_15M,
          "30m": KL_TYPE.K_30M, "1h": KL_TYPE.K_60M, "4h": KL_TYPE.K_60M,
          "1d": KL_TYPE.K_DAY, "1w": KL_TYPE.K_WEEK}[freq]
    klu_list = df_to_klu_list(df)
    chan = compute_chan(klu_list, lv, "BTCUSDT", {
        "bi_strict": True, "divergence_rate": 0.9,
    })
    print(f"  Bi={len(chan[0].bi_list)}, Seg={len(chan[0].seg_list)}, "
          f"ZS={len(chan[0].zs_list)}, BSP={len(chan[0].bs_point_lst)}")

    plot_para = {
        "figure": {"x_range": x_range, "w": w, "h": h, "x_tick_num": 10},
        "bi":    {"color": "black", "disp_end": True},
        "seg":   {"color": "g", "width": 5, "disp_end": False},
        "zs":    {"color": "orange", "linewidth": 2, "sub_linewidth": 0.5},
        "bsp":   {"buy_color": "r", "sell_color": "g", "fontsize": 15},
        "kl":    {"width": 0.4, "rugd": True},
    }
    driver = CPlotDriver(chan, plot_config=PLOT_CONFIG_DEFAULT, plot_para=plot_para)

    # Overlay
    ax = driver.figure.axes[0]
    overlay_current_price(driver, ax, chan)
    highlight_recent_bsp(driver, ax, chan, n=2)

    # 标题
    code = chan.code
    lv_name = chan.lv_list[0].name.split("K_")[1]
    last_close = _get_last_close(chan)
    ax.set_title(f"{code} / {lv_name}  {desc}  |  close={last_close:,.0f}",
                 fontsize=16, loc="left", color="red")
    return driver.figure


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 多级别联立图")
    ap.add_argument("--out", default=None,
                    help="输出 PNG 路径(默认 chan_view/chan_BTCUSDT_multi_<date>.png)")
    ap.add_argument("--layout", default="vertical", choices=["vertical", "horizontal"],
                    help="上下排/左右排(默认 vertical)")
    ap.add_argument("--top-days",    type=int, default=MULTI_VIEW["top"]["days"])
    ap.add_argument("--bottom-days", type=int, default=MULTI_VIEW["bottom"]["days"])
    args = ap.parse_args()

    print(f"\n[top] {MULTI_VIEW['top']['desc']}  freq={MULTI_VIEW['top']['freq']} days={args.top_days}")
    fig_top = render_one_view(MULTI_VIEW["top"]["freq"], args.top_days,
                              MULTI_VIEW["top"]["x_range"], MULTI_VIEW["top"]["desc"])

    print(f"\n[bottom] {MULTI_VIEW['bottom']['desc']}  freq={MULTI_VIEW['bottom']['freq']} days={args.bottom_days}")
    fig_bot = render_one_view(MULTI_VIEW["bottom"]["freq"], args.bottom_days,
                              MULTI_VIEW["bottom"]["x_range"], MULTI_VIEW["bottom"]["desc"])

    # 保存中间产物
    tmp_top = Path(_bootstrap._ROOT) / "cache" / "_multi_top.png"
    tmp_bot = Path(_bootstrap._ROOT) / "cache" / "_multi_bot.png"
    fig_top.savefig(tmp_top, dpi=120, bbox_inches="tight", facecolor="white")
    fig_bot.savefig(tmp_bot, dpi=120, bbox_inches="tight", facecolor="white")
    matplotlib.pyplot.close(fig_top)
    matplotlib.pyplot.close(fig_bot)

    # PIL 拼接
    from PIL import Image
    img_top = Image.open(tmp_top)
    img_bot = Image.open(tmp_bot)
    if args.layout == "vertical":
        # 上下排
        new_w = max(img_top.width, img_bot.width)
        new_h = img_top.height + img_bot.height + 30  # 中间留 30px 间隔
        combined = Image.new("RGB", (new_w, new_h), "white")
        combined.paste(img_top, (0, 0))
        combined.paste(img_bot, (0, img_top.height + 30))
    else:
        # 左右排
        new_w = img_top.width + img_bot.width + 30
        new_h = max(img_top.height, img_bot.height)
        combined = Image.new("RGB", (new_w, new_h), "white")
        combined.paste(img_top, (0, 0))
        combined.paste(img_bot, (img_top.width + 30, 0))

    if args.out:
        out_path = Path(args.out)
    else:
        date_str = pd.Timestamp.now().strftime("%Y%m%d")
        out_path = Path(_bootstrap._ROOT) / "chan_view" / f"chan_BTCUSDT_multi_{date_str}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(out_path, "PNG", optimize=True)
    # 清理中间产物
    tmp_top.unlink()
    tmp_bot.unlink()
    print(f"\n✓ 完成: {out_path}")


if __name__ == "__main__":
    main()
