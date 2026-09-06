"""
chan_plot_animate.py — BTCUSDT 逐 K 线缠论演进图
==================================================
基于 chan.py 的 CAnimateDriver,逐根 K 线迭代画图,
展示缠论元素(笔/段/中枢/BSP)如何随新 K 线新增而变化。

适用场景:Jupyter Notebook 里观察缠论"当下推断"的过程
(参考 quick_guide.md 的"当前帧"概念)

依赖: IPython, jupyter

Usage:
    # Jupyter 里:
    %run core/chan_plot_animate.py

    # 或者作为脚本(在支持 IPython 的环境):
    python core/chan_plot_animate.py
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# --- 路径 ---
import _bootstrap  # noqa
from pathlib import Path

_VENDOR_CHAN = Path(_bootstrap._ROOT) / "vendor" / "chan"
if str(_VENDOR_CHAN) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CHAN))

import argparse
import pandas as pd
try:
    # Jupyter / IPython 环境(动态显示必需)
    from IPython.display import clear_output, display
    _HAS_IPYTHON = True
except ImportError:
    _HAS_IPYTHON = False
import matplotlib
matplotlib.use("Agg")  # 默认非交互;Jupyter 里改成 %matplotlib inline

# 中文字体
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei',
                                          'PingFang SC', 'Arial Unicode MS',
                                          'DejaVu Sans']
matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['axes.unicode_minus'] = False

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE

from chan_plot import (load_kline_parquet, df_to_klu_list, compute_chan,
                       PLOT_CONFIG_DEFAULT)


# ── 视图定义 ────────────────────────────────────
# 跟 chan_plot_multi.py 对齐
VIEWS = {
    "top":    dict(freq="1d", days=365,  x_range=200, desc="日线(大级别趋势)"),
    "bottom": dict(freq="4h", days=90,   x_range=300, desc="4小时(中级别进场)"),
}


def build_step_chan(symbol: str, freq: str, days: int):
    """构造一个 trigger_step=True 的 CChan,准备逐根迭代。

    BBAC-D:不依赖 DataAPI(已删除)。我们直接复用 trigger_load 喂入数据,
    但 trigger_load 是一次性算完。要做"逐根画图",改成下面 manual_step_chan:
    每次加几根 K 线重新算一次,模拟 step_load 行为。
    """
    df = load_kline_parquet(symbol, freq=freq)
    cutoff = df.index.max() - pd.Timedelta(days=days)
    df = df[df.index >= cutoff]
    if len(df) > 3000:
        df = df.iloc[-3000:]
    print(f"[{freq}] {len(df)} 根 K 线, {df.index.min()} → {df.index.max()}")

    lv = {"1m": KL_TYPE.K_1M, "5m": KL_TYPE.K_5M, "15m": KL_TYPE.K_15M,
          "30m": KL_TYPE.K_30M, "1h": KL_TYPE.K_60M, "4h": KL_TYPE.K_60M,
          "1d": KL_TYPE.K_DAY, "1w": KL_TYPE.K_WEEK}[freq]
    klu_list = df_to_klu_list(df)
    config = CChanConfig({
        "bi_strict":       True,
        "trigger_step":    True,
        "divergence_rate": 0.9,
        "print_warning":   False,
    })
    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CSV,
        lv_list=[lv],
        config=config,
        autype=AUTYPE.NONE,
    )
    chan.trigger_load({lv: klu_list})
    return chan, df, klu_list, lv


def build_step_snapshot(klu_list: list, lv: KL_TYPE, n: int, code: str = "BTCUSDT"):
    """从 n 根 K 线构建一个 snapshot。
    模拟"逐根 K 线触发"的效果:每张图都从第 1 根到第 n 根重新算。
    (注意:这是简化版,真正的 step_load 是增量计算;这里每帧重算所有数据。)

    关键:trigger_step=True 让 CChan.__init__ 不走 self.load() (DataAPI),
    后面再 trigger_load 喂数据。
    """
    config = CChanConfig({
        "bi_strict":       True,
        "trigger_step":    True,   # ← 跳过 init 时的 load
        "divergence_rate": 0.9,
        "print_warning":   False,
    })
    chan = CChan(
        code=code,
        data_src=DATA_SRC.CSV,
        lv_list=[lv],
        config=config,
        autype=AUTYPE.NONE,
    )
    # 只喂入前 n 根 K 线
    chan.trigger_load({lv: klu_list[:n]})
    return chan


def step_iterate(snap: CChan, x_range: int = 200, label: str = "", n: int = 0, total: int = 0,
                save_png: bool = False):
    """画一帧图。Jupyter 显示 / 非 Jupyter 保存 PNG(由 main 控制)。"""
    plot_para = {
        "figure": {"x_range": x_range, "w": 24, "h": 10, "x_tick_num": 8},
        "bi":    {"color": "black", "disp_end": True},
        "seg":   {"color": "g", "width": 5, "disp_end": False},
        "zs":    {"color": "orange", "linewidth": 2, "sub_linewidth": 0.5},
        "bsp":   {"buy_color": "r", "sell_color": "g", "fontsize": 13},
        "kl":    {"width": 0.4, "rugd": True},
    }
    from Plot.PlotDriver import CPlotDriver
    import matplotlib.pyplot as plt

    driver = CPlotDriver(snap, plot_config=PLOT_CONFIG_DEFAULT, plot_para=plot_para)
    ax = driver.figure.axes[0]
    lv = snap.lv_list[0].name.split("K_")[1]
    cur = snap[0].lst[-1].lst[-1] if snap[0].lst else None
    cur_str = f"{cur.close:.0f}" if cur else "?"
    n_bsp = len(snap[0].bs_point_lst)
    ax.set_title(f"{snap.code} / {lv} | frame={n}/{total} | close={cur_str} | bsp={n_bsp} {label}",
                 fontsize=14, loc="left", color="red")
    if _HAS_IPYTHON and not save_png:
        clear_output(wait=True)
        display(driver.figure)
    else:
        # 保存为 PNG(Jupyter 里 --save-png 调试 / 无 IPython 默认行为)
        out = Path(_bootstrap._ROOT) / "chan_view" / f"animate_frame_{n:04d}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        driver.figure.savefig(out, dpi=80, bbox_inches="tight")
        if n % 5 == 0:
            print(f"  frame={n}/{total} saved: {out.name}")
    plt.close(driver.figure)


def main():
    ap = argparse.ArgumentParser(description="BTCUSDT 逐 K 线缠论演进图")
    ap.add_argument("--freq", default="1d", choices=list(VIEWS.keys()) + ["1d", "4h", "1h"],
                    help="要演进的级别(默认 1d)")
    ap.add_argument("--days", type=int, default=180,
                    help="K 线范围(默认 180)")
    ap.add_argument("--x-range", type=int, default=200)
    ap.add_argument("--max-frames", type=int, default=20,
                    help="最多生成几帧(防止爆盘,默认 20)")
    ap.add_argument("--start-frac", type=float, default=0.3,
                    help="从数据前 30% 处开始(更早的帧 BSP 少,后面才丰富)")
    ap.add_argument("--save-png", action="store_true",
                    help="装了 IPython 也强制每帧保存为 PNG(Jupyter 里调试用)")
    args = ap.parse_args()

    if args.freq in VIEWS:
        freq = VIEWS[args.freq]["freq"]
        days = VIEWS[args.freq]["days"]
        x_range = VIEWS[args.freq]["x_range"]
    else:
        freq = args.freq
        days = args.days
        x_range = args.x_range

    if not _HAS_IPYTHON:
        print("⚠ 未检测到 IPython。每帧保存为 PNG 到 chan_view/animate_frame_*.png")
        print("  如需 Jupyter 动态显示:pip install ipython jupyter")
    elif args.save_png:
        print("→ IPython 已装 + --save-png:每帧保存为 PNG")

    chan, df, klu_list, lv = build_step_chan("BTCUSDT", freq=freq, days=days)
    n_total = len(klu_list)
    # 选帧:从前 start_frac 开始,均匀采样 max_frames 帧
    start = int(n_total * args.start_frac)
    end = n_total
    step = max(1, (end - start) // args.max_frames)
    frame_indices = list(range(start, end, step))[:args.max_frames]
    print(f"将生成 {len(frame_indices)} 帧(从 idx={start} 到 {end}, 步长 {step})")

    for i, idx in enumerate(frame_indices):
        snap = build_step_snapshot(klu_list, lv, idx, code="BTCUSDT")
        step_iterate(snap, x_range=x_range, label=f"| freq={freq}",
                     n=i, total=len(frame_indices), save_png=args.save_png)

    print(f"✓ 完成 {len(frame_indices)} 帧。")


if __name__ == "__main__":
    main()
