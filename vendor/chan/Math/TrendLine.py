"""
BBAC-D 精简版 TrendLine — 用于画段内支撑/阻力线
=================================================
原版 TrendLine.py 是复杂的多项式拟合,BBAC-D 简化为线性回归(中线)。
ChanModel 删除后,这里内联实现。
"""
from typing import List, TYPE_CHECKING

from Common.CEnum import BI_DIR, TREND_LINE_SIDE

if TYPE_CHECKING:
    from Bi.Bi import CBi


class CTrendLine:
    """简单的支撑/阻力线 = 段内笔端点的最小二乘拟合"""

    def __init__(self, bi_list: List["CBi"], side: TREND_LINE_SIDE):
        self.side = side
        self.line = None

        if len(bi_list) < 2:
            return

        # 收集所有笔端点(按 side 区分)
        if side == TREND_LINE_SIDE.INSIDE:  # INSIDE=支撑,取每笔的低点
            pts = [(bi.idx, bi._low()) for bi in bi_list if bi.is_down() or bi.is_up()]
        else:  # OUTSIDE=阻力,取高点
            pts = [(bi.idx, bi._high()) for bi in bi_list if bi.is_down() or bi.is_up()]

        if len(pts) < 2:
            return

        # 简单线性回归 y = slope*x + intercept
        n = len(pts)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        denom = sum((x - x_mean) ** 2 for x in xs)
        if denom == 0:
            return
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
        intercept = y_mean - slope * x_mean

        # 包装成"line"对象,PlotMeta 用 line.p 和 line.slope
        class _Line:
            def __init__(self, p, slope):
                self.p = p        # (x, y)
                self.slope = slope

        self.line = _Line(_Point(xs[0], intercept + slope * xs[0]), slope)


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
