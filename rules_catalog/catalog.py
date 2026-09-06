"""
Rules Catalog v4.0
==================
50 条规则的结构化定义 - 文档、回测、扫描、反向工程的"唯一真理之源"

字段说明:
  id           : 规则编号 (A01-J03)
  category     : 类别 (A-J)
  name_cn      : 中文业务名
  name_short   : 短名(报告用)
  business_logic: 业务逻辑描述
  direction_doc: 文档标注方向 (long/short/neutral)
  param_space  : 扫描参数空间 {param_name: [候选值]}
  cond_builder : 条件构建函数 (df, p) -> pd.Series[bool]
                  df: 含有 HC/WH/MS/TI/LM/ret_1h 等指标的 DataFrame
                  p : dict, 参数名 -> 具体阈值(由 resolve_param_space 解析后传入)
  notes        : 备注
  skip         : 标记为 True 表示该规则跳过(如 BTC 联动)
"""
import numpy as np

# ── A: 主力动作 (8) ──────────────────────────────────
A_RULES = [
    {
        'id': 'A01', 'category': 'A', 'name_cn': '大户开多仓',
        'name_short': 'long_ignition + hc_delta=1',
        'business_logic': 'WH 启动区 + HC 持续增,主力在多头侧明确加仓',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['hc_delta'] > 0),
    },
    {
        'id': 'A02', 'category': 'A', 'name_cn': '大户平空仓',
        'name_short': 'long_wait + hc_delta=1',
        'business_logic': 'WH 观望区 + HC 持续增,主力在回补空单',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_75'},
        'cond_builder': lambda df, p: (df['HC'].between(-0.3, p['hc_th'])) & (df['hc_delta'] > 0),
    },
    {
        'id': 'A03', 'category': 'A', 'name_cn': '大户开空仓',
        'name_short': 'short_ignition + hc_delta=-1',
        'business_logic': 'WH 启动区 + HC 持续减,主力在空头侧明确加仓',
        'direction_doc': 'short',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] < -p['hc_th']) & (df['hc_delta'] < 0),
    },
    {
        'id': 'A04', 'category': 'A', 'name_cn': '大户平多仓',
        'name_short': 'short_wait + hc_delta=-1',
        'business_logic': 'WH 观望区 + HC 持续减,主力在回补多单',
        'direction_doc': 'short',
        'param_space': {'hc_th': 'quantile_50_75'},
        'cond_builder': lambda df, p: (df['HC'].between(-p['hc_th'], 0.3)) & (df['hc_delta'] < 0),
    },
    {
        'id': 'A05', 'category': 'A', 'name_cn': '大户顺势推空',
        'name_short': 'short_markup + hc_delta=1',
        'business_logic': '空头推升 + HC 持续增,空头力量在加码',
        'direction_doc': 'short',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] < -p['hc_th']) & (df['hc_delta'] > 0),
    },
    {
        'id': 'A06', 'category': 'A', 'name_cn': '大户低位布局',
        'name_short': 'wh+long_accumulation',
        'business_logic': 'WH 强 + 多头建仓区,巨鲸在低位静悄悄吸筹',
        'direction_doc': 'long',
        'param_space': {'wh_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['WH'] > p['wh_th']) & (df['HC'].between(0, 0.5)),
    },
    {
        'id': 'A07', 'category': 'A', 'name_cn': '派发预警',
        'name_short': 'long_markup + hc=3 + delta=-1',
        'business_logic': '多头推升 + HC 极强 + 持续减,主力高位派发',
        'direction_doc': 'short',
        'param_space': {'abs_hc_th': [1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: (df['HC'] > 0.5) & (df['hc_delta'] < 0) & (df['abs_HC'] > p['abs_hc_th']),
    },
    {
        'id': 'A08', 'category': 'A', 'name_cn': 'HC 高位下降',
        'name_short': '|hc|+hc_delta=-1',
        'business_logic': 'HC 在极端区持续减,趋势力量衰竭',
        'direction_doc': 'short',
        'param_space': {'abs_hc_th': [1.0, 1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: (df['abs_HC'] > p['abs_hc_th']) & (df['hc_delta'] < 0),
    },
]

# ── B: 动能确认 (6) ──────────────────────────────────
B_RULES = [
    {
        'id': 'B01', 'category': 'B', 'name_cn': '顺势做多动能',
        'name_short': 'long+ti+dir=1',
        'business_logic': '多头状态 + TI 强正,主动买盘强劲',
        'direction_doc': 'long',
        'param_space': {'ti_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (df['ti_sign'] > 0) & (df['HC'] > 0),
    },
    {
        'id': 'B02', 'category': 'B', 'name_cn': '顺势做空动能',
        'name_short': 'short+ti+dir=-1',
        'business_logic': '空头状态 + TI 强负,主动卖盘强劲',
        'direction_doc': 'short',
        'param_space': {'ti_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (df['ti_sign'] < 0) & (df['HC'] < 0),
    },
    {
        'id': 'B03', 'category': 'B', 'name_cn': '多空博弈',
        'name_short': 'wh wash',
        'business_logic': 'WH 洗盘区,多空博弈剧烈,趋势性差',
        'direction_doc': 'short',
        'param_space': {'wh_band': [0.2, 0.3, 0.5, 0.8]},
        'cond_builder': lambda df, p: df['WH'].between(-p['wh_band'], p['wh_band']),
    },
    {
        'id': 'B04', 'category': 'B', 'name_cn': 'TI 动能加速',
        'name_short': 'ti+rising',
        'business_logic': 'TI 强 + TI 持续增,动能加速中',
        'direction_doc': 'long',
        'param_space': {'ti_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (df['TI'] > df['TI'].shift(1)),
    },
    {
        'id': 'B05', 'category': 'B', 'name_cn': 'TI 极强 + 价格横盘',
        'name_short': 'ti>=3+price flat',
        'business_logic': 'TI 极强 + 价格横盘,动能积聚待释放',
        'direction_doc': 'long',
        'param_space': {'ti_th': [2.0, 3.0, 3.82]},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (df['ret_1h'].abs() < 0.003),
    },
    {
        'id': 'B06', 'category': 'B', 'name_cn': 'TI 强 + 价格反向',
        'name_short': 'ti+price reverse',
        'business_logic': 'TI 强 + 价格反向,动能背离预警',
        'direction_doc': 'short',
        'param_space': {'ti_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (np.sign(df['ret_1h']) != df['ti_sign']),
    },
]

# ── C: 动量突破 (5) ──────────────────────────────────
C_RULES = [
    {
        'id': 'C01', 'category': 'C', 'name_cn': 'SM+UM 突破',
        'name_short': 'sm_deor+lm_extreme',
        'business_logic': 'SM 脱橘 + UM 极端,单边行情 + 异常波动',
        'direction_doc': 'long',
        'param_space': {'sm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['SM'].abs() > p['sm_th']) & (df['LM'].abs() > 2),
    },
    {
        'id': 'C02', 'category': 'C', 'name_cn': 'SM+UM 启动',
        'name_short': 'sm_deor+lm normal',
        'business_logic': 'SM 脱橘 + UM 中性,板块温和启动,早期机会',
        'direction_doc': 'long',
        'param_space': {'sm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['SM'].abs() > p['sm_th']) & (df['LM'].abs() < 2) & (df['LM'].notna()),
    },
    {
        'id': 'C03', 'category': 'C', 'name_cn': 'SM 单边动能',
        'name_short': '|sm|>=5',
        'business_logic': 'SM 极强,单边动能充分,顺势机会',
        'direction_doc': 'long',
        'param_space': {'sm_th': [3.0, 4.0, 5.0]},
        'cond_builder': lambda df, p: df['SM'].abs() > p['sm_th'],
    },
    {
        'id': 'C04', 'category': 'C', 'name_cn': 'SM 极强 + 价格横盘',
        'name_short': '|sm|>=5+flat',
        'business_logic': 'SM 极强 + 价格横盘,脱橘前蓄力',
        'direction_doc': 'long',
        'param_space': {'sm_th': [3.0, 4.0, 5.0]},
        'cond_builder': lambda df, p: (df['SM'].abs() > p['sm_th']) & (df['ret_1h'].abs() < 0.003),
    },
    {
        'id': 'C05', 'category': 'C', 'name_cn': 'SM 绿柱持续增',
        'name_short': 'sm+sm rising',
        'business_logic': 'SM 绿柱 + 持续增,动能持续向上',
        'direction_doc': 'long',
        'param_space': {'sm_th': [1.0, 2.0, 3.0]},
        'cond_builder': lambda df, p: (df['SM'] > p['sm_th']) & (df['SM'] > df['SM'].shift(1)),
    },
]

# ── D: 主力 vs 散户 (4) ──────────────────────────────────
D_RULES = [
    {
        'id': 'D01', 'category': 'D', 'name_cn': '主力吸筹',
        'name_short': 'hc+ms<=-th',
        'business_logic': 'HC 做多但 MS 极度悲观,主力在散户恐慌时反向吸筹',
        'direction_doc': 'long',
        'param_space': {'hc_th': [0.5, 1.0, 1.5, 2.0], 'ms_th': [0.5, 1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['MS'] < -p['ms_th']),
    },
    {
        'id': 'D02', 'category': 'D', 'name_cn': '主力派发',
        'name_short': 'hc<=-th+ms>=th',
        'business_logic': 'HC 做空但 MS 极度乐观,主力在散户狂热时反向派发',
        'direction_doc': 'short',
        'param_space': {'hc_th': [0.5, 1.0, 1.5, 2.0], 'ms_th': [0.5, 1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['HC'] < -p['hc_th']) & (df['MS'] > p['ms_th']),
    },
    {
        'id': 'D03', 'category': 'D', 'name_cn': '主力吸筹(加强)',
        'name_short': 'D01+price flat 24h',
        'business_logic': 'HC 多 + MS 悲观 + 价格横盘,主力暗中吸筹',
        'direction_doc': 'long',
        'param_space': {'hc_th': [0.5, 1.0, 1.5, 2.0], 'ms_th': [0.5, 1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['MS'] < -p['ms_th']) & (df['ret_24h'].abs() < 0.01),
    },
    {
        'id': 'D04', 'category': 'D', 'name_cn': '主力派发(加强)',
        'name_short': 'D02+new high 24h',
        'business_logic': 'HC 空 + MS 乐观 + 价格新高,主力在顶部派发',
        'direction_doc': 'short',
        'param_space': {'hc_th': [0.5, 1.0, 1.5, 2.0], 'ms_th': [0.5, 1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['HC'] < -p['hc_th']) & (df['MS'] > p['ms_th']) & df['new_high_24h'],
    },
]

# ── E: 板块联动 (4 + 2 BTC skip) ──────────────────────
E_RULES = [
    {
        'id': 'E01', 'category': 'E', 'name_cn': '主力+板块共振',
        'name_short': 'hc+ms>=1',
        'business_logic': 'HC 强势 + 板块 1h 强势,主力与板块共振',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['MS'] > 1),
    },
    {
        'id': 'E02', 'category': 'E', 'name_cn': '主力+板块背离',
        'name_short': 'hc+ms<=-1',
        'business_logic': 'HC 强势 + 板块 1h 弱势,主力意图与板块背离',
        'direction_doc': 'short',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['MS'] < -1),
    },
    {
        'id': 'E03', 'category': 'E', 'name_cn': 'WH 进区板块联动',
        'name_short': 'wh+ms>=1',
        'business_logic': 'WH 进区 + 板块 1h 强势,大资金发动热点',
        'direction_doc': 'long',
        'param_space': {'wh_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['WH'] > p['wh_th']) & (df['MS'] > 1),
    },
    {
        'id': 'E04', 'category': 'E', 'name_cn': 'TI+板块共振',
        'name_short': 'ti+dir=1+ms>=1',
        'business_logic': 'TI 强正 + 板块强势,动能 + 板块情绪同步',
        'direction_doc': 'long',
        'param_space': {'ti_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['TI'] > p['ti_th']) & (df['ti_sign'] > 0) & (df['MS'] > 1),
    },
    # BTC 联动类 - 单币种回测无法验证,默认跳过
    {
        'id': 'E05', 'category': 'E', 'name_cn': 'BTC + 板块冷',
        'name_short': 'btc+sector_cold',
        'business_logic': 'BTC 涨 + 板块 1h 弱势,板块补涨机会',
        'direction_doc': 'long',
        'skip': True,
        'skip_reason': '需 BTC 数据,单币种框架不扫描',
    },
    {
        'id': 'E06', 'category': 'E', 'name_cn': 'BTC + 板块热',
        'name_short': 'btc+sector_hot',
        'business_logic': 'BTC 跌 + 板块 1h 强势,板块抗跌',
        'direction_doc': 'short',
        'skip': True,
        'skip_reason': '需 BTC 数据,单币种框架不扫描',
    },
]

# ── F: 价格确认 (6) ──────────────────────────────────
F_RULES = [
    {
        'id': 'F01', 'category': 'F', 'name_cn': 'HC 强 + 价格回撤',
        'name_short': 'hc+1h drop>1%',
        'business_logic': 'HC 强 + 价格回撤,拉回给买入机会',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['ret_1h'] < -0.01),
    },
    {
        'id': 'F02', 'category': 'F', 'name_cn': 'HC 强 + 价格新高',
        'name_short': 'hc+new high 24h',
        'business_logic': 'HC 强 + 价格新高,突破 + 主力确认',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & df['new_high_24h'],
    },
    {
        'id': 'F03', 'category': 'F', 'name_cn': 'HC 强 + 价格横盘',
        'name_short': 'hc+price flat 24h',
        'business_logic': 'HC 强 + 价格横盘,主力暗中吸筹',
        'direction_doc': 'long',
        'param_space': {'hc_th': 'quantile_50_95'},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['ret_24h'].abs() < 0.01),
    },
    {
        'id': 'F04', 'category': 'F', 'name_cn': 'MS 极度乐观 + 价格新高',
        'name_short': 'ms+new high 24h',
        'business_logic': 'MS 极度乐观 + 价格新高,情绪顶部',
        'direction_doc': 'short',
        'param_space': {'ms_th': [1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['MS'] > p['ms_th']) & df['new_high_24h'],
    },
    {
        'id': 'F05', 'category': 'F', 'name_cn': 'MS 极度悲观 + 价格新低',
        'name_short': 'ms+new low 24h',
        'business_logic': 'MS 极度悲观 + 价格新低,情绪底部',
        'direction_doc': 'long',
        'param_space': {'ms_th': [1.0, 1.5, 2.0]},
        'cond_builder': lambda df, p: (df['MS'] < -p['ms_th']) & df['new_low_24h'],
    },
    {
        'id': 'F06', 'category': 'F', 'name_cn': '长上影线 + 高位',
        'name_short': 'long upper shadow',
        'business_logic': 'K 线上影 + 高位,见顶信号',
        'direction_doc': 'short',
        'param_space': {'shadow_ratio': [1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: (
            (df['High'] - df[['Open', 'Close']].max(axis=1)) >
            p['shadow_ratio'] * (df[['Open', 'Close']].max(axis=1) - df[['Open', 'Close']].min(axis=1))
        ) & (df['ret_24h'] > 0.05),
    },
]

# ── G: 极端反转 (5) ──────────────────────────────────
G_RULES = [
    {
        'id': 'G01', 'category': 'G', 'name_cn': '极端共识',
        'name_short': '|hc|+|ti| same sign',
        'business_logic': 'HC 极强 + TI 极强同向,极端共识',
        'direction_doc': 'long',
        'param_space': {'abs_hc_th': [1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: (df['abs_HC'] > p['abs_hc_th']) & (df['abs_TI'] > 2) & (df['hc_sign'] * df['ti_sign'] > 0),
    },
    {
        'id': 'G02', 'category': 'G', 'name_cn': '极端波动+筹码集中',
        'name_short': '|hc|+lm extreme',
        'business_logic': 'HC 极强 + UM 极强,异常波动 + 主力筹码',
        'direction_doc': 'long',
        'param_space': {'abs_hc_th': [1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: (df['abs_HC'] > p['abs_hc_th']) & (df['LM'].abs() > 2),
    },
    {
        'id': 'G03', 'category': 'G', 'name_cn': 'HC 强势集中',
        'name_short': '|hc|>=th alone',
        'business_logic': '|HC| ≥ 2.5 进入过度集中区,实战应避免追高',
        'direction_doc': 'short',
        'param_space': {'abs_hc_th': [1.0, 1.5, 2.0, 2.5]},
        'cond_builder': lambda df, p: df['abs_HC'] > p['abs_hc_th'],
    },
    {
        'id': 'G04', 'category': 'G', 'name_cn': 'HC 零轴突破',
        'name_short': 'hc zero cross up',
        'business_logic': 'HC 由 ≤0 翻 >0,趋势可能由空转多',
        'direction_doc': 'long',
        'param_space': {'zero': [0]},
        'cond_builder': lambda df, p: (df['HC'] > 0) & (df['HC'].shift(1) <= 0),
    },
    {
        'id': 'G05', 'category': 'G', 'name_cn': 'HC 零轴跌破',
        'name_short': 'hc zero cross down',
        'business_logic': 'HC 由 ≥0 翻 <0,趋势可能由多转空',
        'direction_doc': 'short',
        'param_space': {'zero': [0]},
        'cond_builder': lambda df, p: (df['HC'] < 0) & (df['HC'].shift(1) >= 0),
    },
]

# ── H: 爆仓 (4) ──────────────────────────────────
H_RULES = [
    {
        'id': 'H01', 'category': 'H', 'name_cn': '空头爆仓接刀',
        'name_short': 'lm+ti+dir=1',
        'business_logic': '空头极端爆仓 + TI 强正,大户接刀,1h 反弹',
        'direction_doc': 'long',
        'param_space': {'lm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['LM'] > p['lm_th']) & (df['TI'] > 2) & (df['ti_sign'] > 0),
    },
    {
        'id': 'H02', 'category': 'H', 'name_cn': '多头爆仓出清',
        'name_short': 'lm+ti+dir=-1',
        'business_logic': '多头极端爆仓 + TI 强负,市场出清',
        'direction_doc': 'short',
        'param_space': {'lm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['LM'] < -p['lm_th']) & (df['TI'] > 2) & (df['ti_sign'] < 0),
    },
    {
        'id': 'H03', 'category': 'H', 'name_cn': 'LM 爆仓 + 板块共振',
        'name_short': '|lm|+ms>=1',
        'business_logic': 'LM 爆仓极端 + 板块联动,爆仓触发的顺势',
        'direction_doc': 'long',
        'param_space': {'lm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['LM'].abs() > p['lm_th']) & (df['MS'] > 1),
    },
    {
        'id': 'H04', 'category': 'H', 'name_cn': 'LM 极端 + 价格反向',
        'name_short': '|lm|+price reverse',
        'business_logic': 'LM 极端 + 价格反向,爆仓触底/触顶',
        'direction_doc': 'long',
        'param_space': {'lm_th': 'quantile_50_70'},
        'cond_builder': lambda df, p: (df['LM'].abs() > p['lm_th']) & (np.sign(df['ret_1h']) != df['lm_sign']),
    },
]

# ── I: 连续模式 (3) ──────────────────────────────────
I_RULES = [
    {
        'id': 'I01', 'category': 'I', 'name_cn': '连续 3 根阳线',
        'name_short': '3 bullish bars',
        'business_logic': '连续 3 根 K 线阳,趋势初成',
        'direction_doc': 'long',
        'param_space': {'n': [3]},
        'cond_builder': lambda df, p: (df['Close'] > df['Open']).rolling(p['n']).sum() >= p['n'],
    },
    {
        'id': 'I02', 'category': 'I', 'name_cn': '连续 3 根阴线',
        'name_short': '3 bearish bars',
        'business_logic': '连续 3 根 K 线阴,下跌趋势',
        'direction_doc': 'short',
        'param_space': {'n': [3]},
        'cond_builder': lambda df, p: (df['Close'] < df['Open']).rolling(p['n']).sum() >= p['n'],
    },
    {
        'id': 'I03', 'category': 'I', 'name_cn': '吸筹转启动',
        'name_short': 'long_accum->long_ign',
        'business_logic': 'long_accumulation → long_ignition 状态切换',
        'direction_doc': 'long',
        'param_space': {'hc_th': [0.5]},
        'cond_builder': lambda df, p: (df['HC'] > p['hc_th']) & (df['HC'].shift(1) < p['hc_th']) & (df['HC'].shift(1) > 0),
    },
]

# ── J: 跨币种联动 (3 BTC skip) ──────────────────────
J_RULES = [
    {
        'id': 'J01', 'category': 'J', 'name_cn': 'BTC 联动',
        'name_short': 'btc_linkage',
        'business_logic': 'BTC 涨 + 该币跟涨但涨幅落后,补涨机会',
        'direction_doc': 'long',
        'skip': True,
        'skip_reason': '需 BTC 数据,单币种框架不扫描',
    },
    {
        'id': 'J02', 'category': 'J', 'name_cn': 'BTC 背离',
        'name_short': 'btc_divergence',
        'business_logic': 'BTC 涨 + 该币跌,跨币种背离',
        'direction_doc': 'long',
        'skip': True,
        'skip_reason': '需 BTC 数据,单币种框架不扫描',
    },
    {
        'id': 'J03', 'category': 'J', 'name_cn': '板块内联动',
        'name_short': 'sector_linkage',
        'business_logic': '同板块 ≥ 3 币 1h 涨 > 1% + 该币未涨,板块补涨',
        'direction_doc': 'long',
        'skip': True,
        'skip_reason': '需板块多币数据,单币种框架不扫描',
    },
]

# ── 合并所有规则 ──────────────────────────────────
ALL_RULES = A_RULES + B_RULES + C_RULES + D_RULES + E_RULES + F_RULES + G_RULES + H_RULES + I_RULES + J_RULES


# ── 启动时校验 ──────────────────────────────────
# 防止"忘记 cond_builder / 字段缺失"这种隐患 — 在 import 时就抛错,
# 比在 core/single_symbol_backtest.py 跑一半才 TypeError 安全得多。
_REQUIRED_FIELDS = ('id', 'category', 'name_cn', 'direction_doc')
_ACTIVE_REQUIRED = _REQUIRED_FIELDS + ('cond_builder', 'param_space')
_VALID_DIRECTIONS = {'long', 'short', 'neutral'}


def _validate_catalog():
    """验证 ALL_RULES 的 schema。失败抛 ValueError。"""
    seen_ids = set()
    for r in ALL_RULES:
        rid = r.get('id')
        if rid is None:
            raise ValueError(f'catalog: rule missing "id" field: {r}')
        if rid in seen_ids:
            raise ValueError(f'catalog: duplicate rule id {rid!r}')
        seen_ids.add(rid)
        for f in _REQUIRED_FIELDS:
            if f not in r:
                raise ValueError(f'catalog: rule {rid} missing required field {f!r}')

        is_active = not r.get('skip', False)
        if is_active:
            for f in _ACTIVE_REQUIRED:
                if f not in r:
                    raise ValueError(
                        f'catalog: active rule {rid} missing required field {f!r}. '
                        f'Either add the field or set skip=True.'
                    )
            if not callable(r['cond_builder']):
                raise ValueError(f'catalog: rule {rid} cond_builder is not callable')
            if not isinstance(r['param_space'], dict):
                raise ValueError(f'catalog: rule {rid} param_space is not a dict')

        direction = r.get('direction_doc')
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f'catalog: rule {rid} direction_doc={direction!r} '
                f'not in {sorted(_VALID_DIRECTIONS)}'
            )
    return len(ALL_RULES)


_TOTAL_RULES = _validate_catalog()


# 给 lambda 起个有意义的 __name__,方便 debug 时堆栈追踪和日志
# (例如 traceback 会显示 "File ..., in cond_a01" 而不是 "in <lambda>")
def _name_lambdas():
    for r in ALL_RULES:
        cb = r.get('cond_builder')
        if cb is not None and cb.__name__ == '<lambda>':
            try:
                cb.__name__ = f'cond_{r["id"].lower()}'
                cb.__qualname__ = f'cond_{r["id"].lower()}'
            except (AttributeError, TypeError):
                # 某些类型的 callable 不可设置 __name__ — 跳过即可
                pass
_name_lambdas()

# ── 工具函数 ──────────────────────────────────
def get_rules_by_category(category):
    """按类别获取规则"""
    return [r for r in ALL_RULES if r['category'] == category]

def get_active_rules():
    """获取非 skip 的规则"""
    return [r for r in ALL_RULES if not r.get('skip', False)]

def get_rule_by_id(rule_id):
    """按 ID 查找规则"""
    for r in ALL_RULES:
        if r['id'] == rule_id:
            return r
    return None

def resolve_param_space(rule, df, mode='default'):
    """把 'quantile_50_95' 解析成实际的数值列表

    mode:
      - 'default'  : 用规则固定阈值 (6 个等距分位点, 50%-95%)
      - 'adaptive' : 把 quantile_* 替换为该币 alpha 分布的实际分位数 (per-coin p90 公平)
                     注意: 此模式下 50%-95% 仍映射到该币分布的 50%-95% 分位,
                     而非固定到全局常量;对小币种更公平
    """
    resolved = {}
    for k, v in rule['param_space'].items():
        if isinstance(v, str) and v.startswith('quantile_'):
            parts = v.replace('quantile_', '').split('_')
            lo, hi = int(parts[0]), int(parts[1])
            if k.startswith('hc_') or k.startswith('abs_hc'):
                col = 'HC'
            elif k.startswith('ti_') or k.startswith('abs_ti'):
                col = 'TI'
            elif k.startswith('ms_') or k.startswith('abs_ms'):
                col = 'MS'
            elif k.startswith('wh_') or k.startswith('abs_wh'):
                col = 'WH'
            elif k.startswith('lm_') or k.startswith('abs_lm'):
                col = 'LM'
            else:
                col = k.split('_')[0].upper()
            use_abs = k.startswith('abs_')
            series = (df[col].abs() if use_abs else df[col]).dropna()
            if len(series) == 0:
                resolved[k] = v
                continue
            # default: 全局 50%-95% 分位 6 等距点
            # adaptive: 该币分布 50%-95% 分位 6 等距点 (per-coin fair)
            if mode == 'adaptive':
                # 拉到 [0, 1] 区间后再 scale 到 [lo/100, hi/100]
                qvals = np.linspace(lo / 100, hi / 100, 6)
            else:
                qvals = np.linspace(lo, hi, 6) / 100
            resolved[k] = [round(float(np.quantile(series, q)), 3) for q in qvals]
        else:
            resolved[k] = v
    return resolved

def get_total_count():
    return len(ALL_RULES)

def get_active_count():
    return len(get_active_rules())

if __name__ == '__main__':
    print(f"Total rules: {get_total_count()}")
    print(f"Active (non-skip): {get_active_count()}")
    print(f"Skipped: {get_total_count() - get_active_count()}")
    print()
    for cat in 'ABCDEFGHIJ':
        rules = get_rules_by_category(cat)
        print(f"  {cat}: {len(rules)} rules - {[r['id'] for r in rules]}")
