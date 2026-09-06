"""
BBAC-D 精简版 CCommonStockApi — stub
====================================
BBAC-D 不通过 DataAPI 取数据(直接 trigger_load 喂 KLU),
但 Chan.py 仍 import 这个基类,作为占位保留。
真正取数据时这个类不会被调用。
"""
from typing import Iterable, Optional

from KLine.KLine_Unit import CKLine_Unit


class CCommonStockApi:
    """基类 stub — 实际不被调用"""
    def __init__(self, code, k_type=None, begin_date=None, end_date=None, autype=None):
        self.code = code
        self.k_type = k_type
        self.begin_date = begin_date
        self.end_date = end_date
        self.autype = autype

    def get_kl_data(self) -> Iterable[CKLine_Unit]:
        # BBAC-D 不会调用此方法(数据从 trigger_load 直接喂入)
        raise NotImplementedError(
            "CCommonStockApi.get_kl_data 不支持。"
            "BBAC-D 应通过 CChan.trigger_load() 直接喂 KLU,不走 DataAPI。"
        )

    @classmethod
    def do_init(cls):
        pass

    @classmethod
    def do_close(cls):
        pass