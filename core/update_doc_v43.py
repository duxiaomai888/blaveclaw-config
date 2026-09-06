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

# 更新版本历史。日期与描述都从报告本身取,不硬编码 —— 报告换了数据,
# 这行才跟着变;行已存在且内容不同就纠正(幂等且自修正)。
gen_date = (re.search(r'\*\*生成日期\*\*:\s*([\d-]+)', report) or [None, 'unknown'])[1]
stable_n = (re.search(r'由\s*(\d+)\s*条 Stab=100%', report) or [None, '?'])[1]
row_new = (f'| v4.3 | {gen_date} | 规则级实测校准:50 条规则 × 跨周期,'
           f'{stable_n} 条 Stab=100%(数据源 cache/v4.3_calibration.md) |')
new_doc = re.sub(r'^\| v4\.3 \|.*$', row_new, new_doc, count=1, flags=re.MULTILINE)
if row_new not in new_doc:
    new_doc = new_doc.replace(
        '| v4.2 | 2026-06-07 | 扩 50 币种实测',
        f'{row_new}\n| v4.2 | 2026-06-07 | 扩 50 币种实测'
    )


with open(DOC_PATH, 'w', encoding='utf-8') as f:
    f.write(new_doc)

print(f"✓ 文档模板.md 已更新 v4.3 章节")
print(f"  - 替换位置: char {match.start()} ~ {match.end()}")
print(f"  - 新报告长度: {len(report)} chars")
