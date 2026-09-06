"""
core/version.py
===============
项目版本号单一来源。VERSION 文件是给人读的("2026-09-06 v4.4"),本模块解析出
机读常量供生成脚本引用。

为什么要这个模块:版本号曾同时硬编码在 analyze_results.py / update_doc_v43.py /
README / 文档模板.md 四处,任何一处漏改就漂移(changelog 与正文互相矛盾)。
现在生成侧全部 import 这里;升版只改 VERSION 文件。

升版流程:改 VERSION 文件 → 跑 `python core/run_all.py`(或 analyze_results +
update_doc_v43)→ 人工改 文档模板.md 主标题/目录 与 README。
"""
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_VP = os.path.join(_ROOT, 'VERSION')

with open(_VP, encoding='utf-8') as _f:
    _raw = _f.read().strip()

# 取最后一个 token 里的版本号 —— 形如 "2026-09-06 v4.4"
_m = re.findall(r'v(\d+\.\d+)', _raw)
VERSION = f"v{_m[-1]}" if _m else _raw.split()[-1]

# 文件首段的日期,形如 "2026-09-06"
_d = re.search(r'\d{4}-\d{2}-\d{2}', _raw)
RELEASE_DATE = _d.group(0) if _d else _raw.split()[0]
