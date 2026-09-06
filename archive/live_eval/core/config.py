"""
live_eval core config — 共享常量与时区工具

不依赖系统 lib/ 任何模块,只 import 标准库 + 第三方包。
"""
from pathlib import Path
from datetime import timezone, timedelta
import pandas as pd

# ── Blave API ──
BLAVE_BASE_URL = 'https://api.blave.org'

# ── Binance 公共 API (免 key) ──
BINANCE_BASE_URL = 'https://api.binance.com'
BINANCE_FAPI_BASE_URL = 'https://fapi.binance.com'  # 合约
BINANCE_DATA_BASE_URL = 'https://data.binance.com'  # 大户持仓

# ── 项目路径 ──
SCRIPT_DIR = Path(__file__).parent        # live_eval/core/
PROJECT_ROOT = SCRIPT_DIR.parent          # live_eval/
DATA_DIR = PROJECT_ROOT / 'data'
REPORTS_DIR = PROJECT_ROOT / 'reports'
TEMPLATES_DIR = PROJECT_ROOT / 'templates'
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# ── 时区 ──
BJ_TZ = timezone(timedelta(hours=8))


def to_bj(utc_dt):
    """Convert UTC datetime to Beijing-time aware datetime."""
    if isinstance(utc_dt, pd.Timestamp):
        utc_dt = utc_dt.to_pydatetime()
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(BJ_TZ)


def load_blave_keys():
    """Search .env up to 5 levels, then env vars. Skip numbered keys."""
    env_file = Path('.env').resolve()
    for _ in range(5):
        if env_file.exists():
            break
        env_file = env_file.parent / '.env'
    api_key, secret_key = None, None
    if env_file.exists():
        skip_prefixes = set()
        for n in range(1, 11):
            skip_prefixes.add(f'blave_api_key{n}=')
            skip_prefixes.add(f'blave_secret_key{n}=')
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if any(line.startswith(p) for p in skip_prefixes):
                continue
            if line.startswith('blave_api_key='):
                api_key = line.split('=', 1)[1].strip()
            elif line.startswith('blave_secret_key='):
                secret_key = line.split('=', 1)[1].strip()
    if not api_key:
        api_key = os.environ.get('blave_api_key')
        secret_key = os.environ.get('blave_secret_key')
    if not api_key:
        raise RuntimeError(f'Blave API key not found. Searched: {env_file}')
    return {'api-key': api_key, 'secret-key': secret_key}


import os  # placed here to avoid unused at top
