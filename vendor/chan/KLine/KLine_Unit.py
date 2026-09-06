"""
BBAC-D 精简版 CKLine_Unit — 只保留 OHLC + 几何属性
======================================================
删除了原版所有 Math.* 指标计算(MACD/BOLL/Demark/RSI/KDJ/Trend)
和 TradeInfo(交易信息)。
力度计算(BSP 判定)直接用 high/low 算 slope/amp,不依赖任何指标。

保留:time/open/high/low/close + sub_kl_list + sup_kl + klc + pre/next + limit_flag
"""
import copy
from typing import Dict, Optional

from Common.CEnum import DATA_FIELD, TREND_TYPE
from Common.ChanException import CChanException, ErrCode
from Common.CTime import CTime


class CKLine_Unit:
    def __init__(self, kl_dict, autofix=False):
        # _time, _close, _open, _high, _low
        self.kl_type = None
        self.time: CTime = kl_dict[DATA_FIELD.FIELD_TIME]
        self.close = kl_dict[DATA_FIELD.FIELD_CLOSE]
        self.open = kl_dict[DATA_FIELD.FIELD_OPEN]
        self.high = kl_dict[DATA_FIELD.FIELD_HIGH]
        self.low = kl_dict[DATA_FIELD.FIELD_LOW]

        self.check(autofix)

        self.sub_kl_list = []   # 次级别 KLU 列表
        self.sup_kl: Optional[CKLine_Unit] = None  # 父级 KLU

        from KLine.KLine import CKLine
        self.__klc: Optional[CKLine] = None  # 所属合并 K 线

        self.trend: Dict[TREND_TYPE, Dict[int, float]] = {}  # 留给未来扩展

        # BBAC-D:Blave 7 维 alpha(MS/CS/HC/TI/WH/SM/BT)
        # 外部脚本通过 inject_alpha() 注入,key 是指标缩写
        self.alpha: Dict[str, float] = {}

        self.limit_flag = 0  # 0:普通 -1:跌停 1:涨停
        self.pre: Optional[CKLine_Unit] = None
        self.next: Optional[CKLine_Unit] = None

        self.set_idx(-1)

    def inject_alpha(self, indicator: str, value: float):
        """注入 Blave 7 维 alpha 数值。BBAC-D 用于 BSP 力度计算。"""
        self.alpha[indicator] = value

    def __deepcopy__(self, memo):
        _dict = {
            DATA_FIELD.FIELD_TIME: self.time,
            DATA_FIELD.FIELD_CLOSE: self.close,
            DATA_FIELD.FIELD_OPEN: self.open,
            DATA_FIELD.FIELD_HIGH: self.high,
            DATA_FIELD.FIELD_LOW: self.low,
        }
        obj = CKLine_Unit(_dict)
        obj.trend = copy.deepcopy(self.trend, memo)
        obj.limit_flag = self.limit_flag
        obj.set_idx(self.idx)
        memo[id(self)] = obj
        return obj

    @property
    def klc(self):
        assert self.__klc is not None
        return self.__klc

    def set_klc(self, klc):
        self.__klc = klc

    @property
    def idx(self):
        return self.__idx

    def set_idx(self, idx):
        self.__idx: int = idx

    def __str__(self):
        return f"{self.idx}:{self.time}/{self.kl_type} open={self.open} close={self.close} high={self.high} low={self.low}"

    def check(self, autofix=False):
        if self.low > min([self.low, self.open, self.high, self.close]):
            if autofix:
                self.low = min([self.low, self.open, self.high, self.close])
            else:
                raise CChanException(f"{self.time} low price={self.low} is not min of [low={self.low}, open={self.open}, high={self.high}, close={self.close}]", ErrCode.KL_DATA_INVALID)
        if self.high < max([self.low, self.open, self.high, self.close]):
            if autofix:
                self.high = max([self.low, self.open, self.high, self.close])
            else:
                raise CChanException(f"{self.time} high price={self.high} is not max of [low={self.low}, open={self.open}, high={self.high}, close={self.close}]", ErrCode.KL_DATA_INVALID)

    def add_children(self, child):
        self.sub_kl_list.append(child)

    def set_parent(self, parent: 'CKLine_Unit'):
        self.sup_kl = parent

    def get_children(self):
        yield from self.sub_kl_list

    def _low(self):
        return self.low

    def _high(self):
        return self.high

    def set_metric(self, metric_model_lst: list) -> None:
        # BBAC-D:删除所有指标计算。BSP 力度直接由 Bi.cal_macd_metric
        # 用 high/low 算(slope/amp),不需要预先存指标。
        # 此方法保留为空,接口不变。
        pass

    def get_parent_klc(self):
        assert self.sup_kl is not None
        return self.sup_kl.klc

    def include_sub_v_time(self, sub_lv_t: str) -> bool:
        if self.time.to_str() == sub_lv_t:
            return True
        for sub_klu in self.sub_kl_list:
            if sub_klu.time.to_str() == sub_lv_t:
                return True
            if sub_klu.include_sub_v_time(sub_lv_t):
                return True
        return False

    def set_pre_klu(self, pre_klu: Optional['CKLine_Unit']):
        if pre_klu is None:
            return
        pre_klu.next = self
        self.pre = pre_klu
