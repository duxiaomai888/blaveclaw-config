"""
BBAC-D 精简版 CBS_Point — 保留 features 字段(给 stats.json 用)
==============================================================
原版从 ChanModel.Features 继承,CBS_Point.features.add_feat() 调用。
BBAC-D 删除 ChanModel/ 目录后,改为内联实现。
"""
from typing import Dict, Generic, List, Optional, TypeVar, Union

from Bi.Bi import CBi
from Common.CEnum import BSP_TYPE
from Seg.Seg import CSeg

LINE_TYPE = TypeVar('LINE_TYPE', CBi, CSeg)


class _SimpleFeatures:
    """最小 features 容器(替代被删除的 CFeatures)"""
    def __init__(self, init=None):
        self._features: Dict[str, float] = {} if init is None else dict(init)

    def items(self):
        return list(self._features.items())

    def __getitem__(self, k):
        return self._features[k]

    def add_feat(self, inp1, inp2=None):
        if inp2 is None:
            self._features.update(inp1)
        else:
            self._features.update({inp1: inp2})


class CBS_Point(Generic[LINE_TYPE]):
    def __init__(self, bi: LINE_TYPE, is_buy, bs_type: BSP_TYPE, relate_bsp1: Optional['CBS_Point'], feature_dict=None):
        self.bi: LINE_TYPE = bi
        self.klu = bi.get_end_klu()
        self.is_buy = is_buy
        self.type: List[BSP_TYPE] = [bs_type]
        self.relate_bsp1 = relate_bsp1

        self.bi.bsp = self  # type: ignore
        self.features = _SimpleFeatures(feature_dict)

        self.is_segbsp = False

        self.init_common_feature()

    def add_type(self, bs_type: BSP_TYPE):
        self.type.append(bs_type)

    def type2str(self):
        return ",".join([x.value for x in self.type])

    def add_another_bsp_prop(self, bs_type: BSP_TYPE, relate_bsp1):
        self.add_type(bs_type)
        if self.relate_bsp1 is None:
            self.relate_bsp1 = relate_bsp1
        elif relate_bsp1 is not None:
            assert self.relate_bsp1.klu.idx == relate_bsp1.klu.idx

    def add_feat(self, inp1: Union[str, Dict[str, float], Dict[str, Optional[float]], '_SimpleFeatures'], inp2: Optional[float] = None):
        self.features.add_feat(inp1, inp2)

    def init_common_feature(self):
        self.add_feat({'bsp_bi_amp': self.bi.amp()})
