from typing import List, Optional

from Common.cache import make_cache
from Common.CEnum import BI_DIR, BI_TYPE, DATA_FIELD, FX_TYPE, MACD_ALGO
from Common.ChanException import CChanException, ErrCode
from KLine.KLine import CKLine
from KLine.KLine_Unit import CKLine_Unit


class CBi:
    def __init__(self, begin_klc: CKLine, end_klc: CKLine, idx: int, is_sure: bool):
        # self.__begin_klc = begin_klc
        # self.__end_klc = end_klc
        self.__dir = None
        self.__idx = idx
        self.__type = BI_TYPE.STRICT

        self.set(begin_klc, end_klc)

        self.__is_sure = is_sure
        self.__used_to_be_sure = is_sure
        self.__sure_end: List[CKLine] = []

        self.__seg_idx: Optional[int] = None

        from Seg.Seg import CSeg
        self.parent_seg: Optional[CSeg[CBi]] = None  # 在哪个线段里面

        from BuySellPoint.BS_Point import CBS_Point
        self.bsp: Optional[CBS_Point] = None  # 尾部是不是买卖点

        self.next: Optional[CBi] = None
        self.pre: Optional[CBi] = None

    def clean_cache(self):
        self._memoize_cache = {}

    @property
    def begin_klc(self): return self.__begin_klc

    @property
    def end_klc(self): return self.__end_klc

    @property
    def dir(self): return self.__dir

    @property
    def idx(self): return self.__idx

    @property
    def type(self): return self.__type

    @property
    def is_sure(self): return self.__is_sure

    @property
    def used_to_be_sure(self): return self.__used_to_be_sure

    @property
    def is_used_to_be_sure(self): return self.is_sure or self.used_to_be_sure

    @property
    def sure_end(self): return self.__sure_end

    @property
    def klc_lst(self):
        klc = self.begin_klc
        while True:
            yield klc
            klc = klc.next
            if not klc or klc.idx > self.end_klc.idx:
                break

    @property
    def klc_lst_re(self):
        klc = self.end_klc
        while True:
            yield klc
            klc = klc.pre
            if not klc or klc.idx < self.begin_klc.idx:
                break

    @property
    def seg_idx(self): return self.__seg_idx

    def set_seg_idx(self, idx):
        self.__seg_idx = idx

    def __str__(self):
        return f"{self.dir}|{self.begin_klc} ~ {self.end_klc}"

    def check(self):
        try:
            if self.is_down():
                assert self.begin_klc.high > self.end_klc.low
            else:
                assert self.begin_klc.low < self.end_klc.high
        except Exception as e:
            raise CChanException(f"{self.idx}:{self.begin_klc[0].time}~{self.end_klc[-1].time}笔的方向和收尾位置不一致!", ErrCode.BI_ERR) from e

    def set(self, begin_klc: CKLine, end_klc: CKLine):
        self.__begin_klc: CKLine = begin_klc
        self.__end_klc: CKLine = end_klc
        if begin_klc.fx == FX_TYPE.BOTTOM:
            self.__dir = BI_DIR.UP
        elif begin_klc.fx == FX_TYPE.TOP:
            self.__dir = BI_DIR.DOWN
        else:
            raise CChanException("ERROR DIRECTION when creating bi", ErrCode.BI_ERR)
        self.check()
        self.clean_cache()

    @make_cache
    def get_begin_val(self):
        return self.begin_klc.low if self.is_up() else self.begin_klc.high

    @make_cache
    def get_end_val(self):
        return self.end_klc.high if self.is_up() else self.end_klc.low

    @make_cache
    def get_begin_klu(self) -> CKLine_Unit:
        if self.is_up():
            return self.begin_klc.get_peak_klu(is_high=False)
        else:
            return self.begin_klc.get_peak_klu(is_high=True)

    @make_cache
    def get_end_klu(self) -> CKLine_Unit:
        if self.is_up():
            return self.end_klc.get_peak_klu(is_high=True)
        else:
            return self.end_klc.get_peak_klu(is_high=False)

    @make_cache
    def amp(self):
        return abs(self.get_end_val() - self.get_begin_val())

    @make_cache
    def get_klu_cnt(self):
        return self.get_end_klu().idx - self.get_begin_klu().idx + 1

    @make_cache
    def get_klc_cnt(self):
        assert self.end_klc.idx == self.get_end_klu().klc.idx
        assert self.begin_klc.idx == self.get_begin_klu().klc.idx
        return self.end_klc.idx - self.begin_klc.idx + 1

    @make_cache
    def _high(self):
        return self.end_klc.high if self.is_up() else self.begin_klc.high

    @make_cache
    def _low(self):
        return self.begin_klc.low if self.is_up() else self.end_klc.low

    @make_cache
    def _mid(self):
        return (self._high() + self._low()) / 2  # 笔的中位价

    @make_cache
    def is_down(self):
        return self.dir == BI_DIR.DOWN

    @make_cache
    def is_up(self):
        return self.dir == BI_DIR.UP

    def update_virtual_end(self, new_klc: CKLine):
        self.append_sure_end(self.end_klc)
        self.update_new_end(new_klc)
        self.__used_to_be_sure = self.__is_sure
        self.__is_sure = False

    def restore_from_virtual_end(self, sure_end: CKLine):
        self.__is_sure = True
        self.__used_to_be_sure = True
        self.update_new_end(new_klc=sure_end)
        self.__sure_end = []

    def append_sure_end(self, klc: CKLine):
        self.__sure_end.append(klc)

    def update_new_end(self, new_klc: CKLine):
        self.__end_klc = new_klc
        self.check()
        self.clean_cache()

    # BBAC-D:macd_algo 接受 8 种算法
    #   - 2 种价格几何:slope, amp
    #   - 6 种 Blave 7 维 alpha(ms, cs, hc, ti, wh, bt)
    # SM 固定 1d 频率,不列入(笔可能是 1h/4h 等)
    # ChanConfig.set_bsp_config() 已在配置时拦截其他值,这里 throw 兜底。
    # 同时接受字符串("slope")和枚举值(MACD_ALGO.SLOPE)
    _ALGO_STRINGS = {"slope", "amp",
                      "ms", "cs", "hc", "ti", "wh", "bt"}
    _ALGO_ENUMS = {MACD_ALGO.SLOPE, MACD_ALGO.AMP}

    def cal_macd_metric(self, macd_algo, is_reverse):
        # 同时接受字符串和枚举
        is_valid = (macd_algo in self._ALGO_STRINGS or
                    macd_algo in self._ALGO_ENUMS)
        if not is_valid:
            raise CChanException(
                f"BBAC-D: macd_algo='{macd_algo}' 不支持。"
                f"可选: slope/amp(价格几何) 或 ms/cs/hc/ti/wh/bt(Blave 7 维 alpha)。",
                ErrCode.PARA_ERROR)
        # 把枚举值标准化成字符串
        algo = macd_algo
        if hasattr(algo, 'value'):
            algo = algo.value  # Enum → "slope" / "amp"
        # 处理别名
        if algo in (MACD_ALGO.SLOPE.value, MACD_ALGO.SLOPE):
            algo = "slope"
        elif algo in (MACD_ALGO.AMP.value, MACD_ALGO.AMP):
            algo = "amp"

        if algo == "slope":
            return self.Cal_MACD_slope()
        if algo == "amp":
            return self.Cal_MACD_amp()
        # Blave alpha 算法
        if algo == "ms":
            return self.Cal_MACD_alpha_ms(is_reverse)
        if algo == "cs":
            return self.Cal_MACD_alpha_cs(is_reverse)
        if algo == "hc":
            return self.Cal_MACD_alpha_hc(is_reverse)
        if algo == "ti":
            return self.Cal_MACD_alpha_ti(is_reverse)
        if algo == "wh":
            return self.Cal_MACD_alpha_wh(is_reverse)
        if algo == "bt":
            return self.Cal_MACD_alpha_bt(is_reverse)
        raise CChanException(f"unsupport macd_algo={macd_algo}", ErrCode.PARA_ERROR)

    @make_cache
    def Cal_MACD_slope(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        if self.is_up():
            return (end_klu.high - begin_klu.low)/end_klu.high/(end_klu.idx - begin_klu.idx + 1)
        else:
            return (begin_klu.high - end_klu.low)/begin_klu.high/(end_klu.idx - begin_klu.idx + 1)

    @make_cache
    def Cal_MACD_amp(self):
        begin_klu = self.get_begin_klu()
        end_klu = self.get_end_klu()
        if self.is_down():
            return (begin_klu.high-end_klu.low)/begin_klu.high
        else:
            return (end_klu.high-begin_klu.low)/begin_klu.low

    # ── BBAC-D:Blave 7 维 alpha 力度算法 ────────────
    # 6 个算法(去掉 SM,因为它固定 1d 频率,不适合其他频率)
    # 计算方式:对笔内每根 KLU 的 alpha 值求平均(下降笔取绝对值最小,上升笔取最大)
    def _alpha_aggregate(self, indicator: str, is_reverse: bool) -> float:
        """聚合笔内 KLU 的 alpha 值。"""
        klu_iter = self.klc_lst_re if is_reverse else self.klc_lst
        vals = []
        for klc in klu_iter:
            for klu in klc.lst:
                v = klu.alpha.get(indicator)
                if v is not None:
                    vals.append(v)
        if not vals:
            return 1e-7  # 无数据 → 极小值(避免除零)
        if self.is_down():
            # 下降笔:力度 = -mean(vals) — 负值越深力度越强
            return -sum(vals) / len(vals) if sum(vals) > 0 else 1e-7
        else:
            return sum(vals) / len(vals)

    def Cal_MACD_alpha_ms(self, is_reverse=False):
        return self._alpha_aggregate("ms", is_reverse)

    def Cal_MACD_alpha_cs(self, is_reverse=False):
        return self._alpha_aggregate("cs", is_reverse)

    def Cal_MACD_alpha_hc(self, is_reverse=False):
        return self._alpha_aggregate("hc", is_reverse)

    def Cal_MACD_alpha_ti(self, is_reverse=False):
        return self._alpha_aggregate("ti", is_reverse)

    def Cal_MACD_alpha_wh(self, is_reverse=False):
        return self._alpha_aggregate("wh", is_reverse)

    def Cal_MACD_alpha_bt(self, is_reverse=False):
        return self._alpha_aggregate("bt", is_reverse)

    # def set_klc_lst(self, lst):
    #     self.__klc_lst = lst
