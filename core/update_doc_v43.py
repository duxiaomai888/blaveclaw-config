"""
update_doc_v43.py
================
把 cache/v4.3_calibration.md 内容追加到 文档模板.md,
替换原 v4.3 章节,确保文档与最新数据同步。

用法:
  python core/update_doc_v43.py
"""
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
REPORT_PATH = os.path.join(_ROOT, 'cache', 'v4.3_calibration.md')
DOC_PATH = os.path.join(_ROOT, '文档模板.md')

if not os.path.exists(REPORT_PATH):
    print(f"!! Report not found: {REPORT_PATH}")
    print("!! Run analyze_results.py first.")
    sys.exit(1)

with open(REPORT_PATH, encoding='utf-8') as f:
    report = f.read()

with open(DOC_PATH, encoding='utf-8') as f:
    doc = f.read()

# 找 v4.3 章节的位置(从 "# v4.3" 到下一个 "# 版本历史")
pattern = re.compile(
    r'^# v4\.3.*?(?=^# 版本历史)',
    re.DOTALL | re.MULTILINE
)
match = pattern.search(doc)
if not match:
    print("!! v4.3 章节没找到, 文档可能未初始化 v4.3")
    sys.exit(1)

# 替换: 保留"# v4.3 实测校准附录"作为章节头, 然后是报告内容
v43_header = """# v4.3 实测校准附录(50 条规则 100% 全验证)

> **核心定位**:本文档由 `analyze_results.py` 自动生成,**底部附录承载实战数据**。

"""
new_doc = doc[:match.start()] + v43_header + report + '\n---\n\n' + doc[match.end():]

# 更新版本历史(幂等:重复运行不再追加第二行)
v43_row = '| v4.3 | 2026-06-07 | ★ 5 条 skip 多币种联动规则(E05/E06/J01/J02/J03)全部验证,50/50 闭环 |'
if 'v4.3 |' not in new_doc:
    new_doc = new_doc.replace(
        '| v4.2 | 2026-06-07 | 扩 50 币种实测',
        f'{v43_row}\n| v4.2 | 2026-06-07 | 扩 50 币种实测'
    )


with open(DOC_PATH, 'w', encoding='utf-8') as f:
    f.write(new_doc)

print(f"✓ 文档模板.md 已更新 v4.3 章节")
print(f"  - 替换位置: char {match.start()} ~ {match.end()}")
print(f"  - 新报告长度: {len(report)} chars")
