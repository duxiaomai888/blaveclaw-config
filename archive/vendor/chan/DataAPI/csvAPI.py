"""
BBAC-D 精简版 csvAPI — stub
============================
原版从 CSV 文件读取 OHLCV 数据。BBAC-D 不通过此路径取数据,
但 CChan.__init__ 仍会 import 它(检查 data_src)。

这里只放最小 stub,让 CChan 能正常实例化。
真正取数据请用 lib/data.py + core/chan_plot.py 的 trigger_load 流程。
"""
from .CommonStockAPI import CCommonStockApi


class CSV_API(CCommonStockApi):
    """最小 stub,不实际读文件"""
    def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None):
        super().__init__(code, k_type, begin_date, end_date, autype)
