"""
chart.py — K 线 + 5 指标子图 + 信号标记 (plotly 交互式 HTML)
"""
from datetime import datetime
from typing import Dict
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def render_chart(kline: pd.DataFrame,
                 indicators: Dict[str, pd.DataFrame],
                 signals: pd.DataFrame,
                 output_path: str = None,
                 last_n_bars: int = 200) -> str:
    """
    画 6 子图 (1 K 线 + 5 指标) + 信号标记
    kline:      index=time, cols=[Open, High, Low, Close, Volume]
    indicators: {"HC": df, "MS": df, ...}  index=time, col=alpha
    signals:    index=time, cols=[signal, conf, raw]
    output_path: HTML 文件路径

    返回: HTML 文件路径
    """
    kline = kline.iloc[-last_n_bars:].copy()
    signals = signals.reindex(kline.index, method="ffill").tail(last_n_bars)

    fig = make_subplots(
        rows=6, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.40, 0.12, 0.12, 0.12, 0.12, 0.12],
    )

    # ── 子图 1: K 线 (candlestick) + Close 线 ──
    fig.add_trace(go.Candlestick(
        x=kline.index,
        open=kline["Open"], high=kline["High"],
        low=kline["Low"], close=kline["Close"],
        name="K-line",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=kline.index, y=kline["Close"],
        mode="lines", line=dict(color="#888", width=1),
        name="Close",
    ), row=1, col=1)

    # 信号点
    long_idx = signals.index[signals["signal"] == "long"]
    short_idx = signals.index[signals["signal"] == "short"]
    if len(long_idx) > 0:
        long_prices = kline.loc[long_idx, "Close"]
        fig.add_trace(go.Scatter(
            x=long_idx, y=long_prices,
            mode="markers",
            marker=dict(symbol="triangle-up", color="green", size=12),
            name="LONG", text=[f"conf={signals.loc[i, 'conf']:.2f}" for i in long_idx],
            hovertemplate="LONG %{x}<br>price=%{y}<br>%{text}<extra></extra>",
        ), row=1, col=1)
    if len(short_idx) > 0:
        short_prices = kline.loc[short_idx, "Close"]
        fig.add_trace(go.Scatter(
            x=short_idx, y=short_prices,
            mode="markers",
            marker=dict(symbol="triangle-down", color="red", size=12),
            name="SHORT", text=[f"conf={signals.loc[i, 'conf']:.2f}" for i in short_idx],
            hovertemplate="SHORT %{x}<br>price=%{y}<br>%{text}<extra></extra>",
        ), row=1, col=1)

    # ── 子图 2~6: 5 个指标 ──
    indicator_names = ["HC", "MS", "WH", "TI", "LM"]
    colors = {"HC": "#1f77b4", "MS": "#ff7f0e", "WH": "#2ca02c",
              "TI": "#d62728", "LM": "#9467bd"}
    for i, name in enumerate(indicator_names):
        if name not in indicators or indicators[name] is None or len(indicators[name]) == 0:
            fig.add_annotation(
                xref="paper", yref=f"y{i+2} domain",
                x=0.5, y=0.5, text=f"{name}: no data",
                showarrow=False,
            )
            continue
        ind = indicators[name].reindex(kline.index, method="ffill")
        fig.add_trace(go.Scatter(
            x=ind.index, y=ind.iloc[:, 0],
            mode="lines", line=dict(color=colors[name], width=1.2),
            name=name,
        ), row=i + 2, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.3,
                      row=i + 2, col=1)

    # 布局
    fig.update_layout(
        title=dict(text="<b>VoteStrategy — BTCUSDT 1h</b>  K-line + 5 Indicators + Signals",
                   x=0.5, font=dict(size=16)),
        xaxis_rangeslider_visible=False,    # 关掉 K 线滑块
        height=1000,
        template="plotly_dark",
        showlegend=True,
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center"),
        hovermode="x unified",
    )
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    for i in range(2, 7):
        fig.update_yaxes(title_text=indicator_names[i - 2] if i - 2 < 5 else "",
                         row=i, col=1)

    if output_path:
        fig.write_html(output_path, include_plotlyjs="cdn")
        return output_path
    else:
        return fig.to_html(include_plotlyjs="cdn", full_html=True)
