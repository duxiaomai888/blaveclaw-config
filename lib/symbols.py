"""Centralized symbol lists for batch / sector / BTC-corr backtests.

All other modules import from here so that adding a new coin means editing
one file, not three.
"""

# ── Batch default 50 币种 ───────────────────────────────────────────────────
DEFAULT_BATCH_50 = [
    # L1 主流
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT',
    'TRXUSDT', 'TONUSDT', 'ADAUSDT', 'AVAXUSDT', 'NEARUSDT', 'LINKUSDT', 'DOTUSDT', 'ATOMUSDT',
    # DeFi
    'UNIUSDT', 'AAVEUSDT', 'CRVUSDT',
    'MKRUSDT', 'COMPUSDT', 'SUSHIUSDT', 'CAKEUSDT', 'SNXUSDT',
    # L2
    'ARBUSDT', 'OPUSDT', 'DASHUSDT',
    'IMXUSDT', 'MNTUSDT', 'STRKUSDT',
    # AI
    'FETUSDT', 'AGIXUSDT', 'TAOUSDT', 'AKTUSDT', 'ICPUSDT',
    # Meme
    'DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT',
    'FLOKIUSDT', 'BONKUSDT', 'WIFUSDT', 'MEMEUSDT', 'TURBOUSDT',
    # 隐私/支付
    'XMRUSDT', 'LTCUSDT', 'BCHUSDT',
    # 存储
    'FILUSDT', 'STXUSDT', 'RNDRUSDT',
    # 新兴/SOL生态
    'JTOUSDT', 'JUPUSDT', 'PYTHUSDT',
    # 平台币
    'GTUSDT', 'KCSUSDT',
]

# ── BTC 联动测试用 14 币种 ──────────────────────────────────────────────────
BTC_CORR_14 = [
    'ETHUSDT', 'SOLUSDT', 'BNBUSDT',
    'DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT',
    'UNIUSDT', 'AAVEUSDT', 'CRVUSDT',
    'ARBUSDT', 'OPUSDT',
    'LINKUSDT', 'AVAXUSDT', 'NEARUSDT',
]

# ── 板块代理(因 Blave API 不返回板块成员,这里按类型粗分) ───────────────────
SECTORS = {
    'AI':      ['FETUSDT', 'AKTUSDT', 'ICPUSDT', 'AGIXUSDT'],
    'L2':      ['ARBUSDT', 'OPUSDT', 'IMXUSDT', 'MNTUSDT', 'STRKUSDT'],
    'Meme':    ['DOGEUSDT', 'SHIBUSDT', 'PEPEUSDT', 'FLOKIUSDT', 'BONKUSDT', 'WIFUSDT'],
    'DeFi':    ['UNIUSDT', 'AAVEUSDT', 'CRVUSDT', 'MKRUSDT', 'COMPUSDT', 'SUSHIUSDT', 'CAKEUSDT'],
    'Storage': ['FILUSDT', 'STXUSDT', 'RNDRUSDT'],
    'Privacy': ['DASHUSDT', 'LTCUSDT', 'BCHUSDT', 'XMRUSDT'],
    'L1':      ['SOLUSDT', 'AVAXUSDT', 'NEARUSDT', 'ATOMUSDT', 'DOTUSDT', 'ADAUSDT', 'TRXUSDT', 'TONUSDT', 'LINKUSDT'],
}
