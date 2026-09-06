"""
update_doc_v43.py
================
把 cache/<VERSION>_calibration.md 内容追加到 文档模板.md,
替换原校准附录章节,确保文档与最新数据同步。

章节锚点稳定:目录链接指向附录容器头("# v4.4 实测校准附录",无覆盖度后缀),
覆盖度只在报告内部标题体现。这样数据重跑、覆盖数变化都不需要改目录 ——
之前把覆盖度写进章节头,导致目录锚点失效成死链。

用法:
  python core/update_doc_v43.py
"""
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _ROOT)
from core.version import VERSION  # noqa: E402

REPORT_PATH = os.path.join(_ROOT, 'cache', f'{VERSION}_calibration.md')
DOC_PATH = os.path.join(_ROOT, '文档模板.md')

if not os.path.exists(REPORT_PATH):
    print(f"!! Report not found: {REPORT_PATH}")
    print(f"!! Run core/analyze_results.py first (VERSION={VERSION}).")
    sys.exit(1)

with open(REPORT_PATH, encoding='utf-8') as f:
    report = f.read()

with open(DOC_PATH, encoding='utf-8') as f:
    doc = f.read()

# 找校准附录的替换范围:从 "# v<ver> 实测校准附录" 行首到 "# 版本历史" 行首。
#
# 刻意不用 DOTALL 正则:之前 `^# v\d+\.\d+.*实测校准附录.*?(?=^# 版本历史)` 在
# DOTALL 下边界模糊,一次中间态运行把附录之前的章节(精选规则统计 / 实战推荐采纳 /
# 通用指令模板 / 搭配的禁止项)一起吞掉了。线性扫描两行边界,范围可见可控。
def _find_appendix_span(doc_text, ver):
    """返回 (start, end);找不到任一边界返回 None。"""
    head_prefix = f'# {ver} 实测校准附录'
    start = None
    for i, line in enumerate(doc_text.splitlines()):
        if line.startswith(head_prefix):
            start = doc_text.find(line)
            break
    if start is None:
        return None
    end = None
    for line in doc_text[start:].splitlines()[1:]:
        if line.startswith('# 版本历史'):
            end = start + doc_text[start:].find(line)
            break
    return (start, end) if end is not None else None


span = _find_appendix_span(doc, VERSION)
if span is None:
    print(f"!! 校准附录范围未定位(需 '# {VERSION} 实测校准附录' 与 '# 版本历史' 两行),"
          f"文档可能未初始化 {VERSION}")
    sys.exit(1)
start, end = span

# 替换: 章节头固定为 "# v<ver> 实测校准附录"(不带覆盖度后缀),然后是报告内容。
# 覆盖度只在报告内部标题里体现 —— 写进章节头会让目录锚点变成死链。
# 覆盖度也不硬编码 "50 条 100% 全验证":catalog 里有 skip 项,也有 active 但
# 触发不足 min_trades 而没出数据的,写死就与报告自相矛盾。
def _groups(pattern, text, n=1, default='?'):
    """re.search 成功取 group(n),否则 default。"""
    m = re.search(pattern, text)
    return m.group(n) if m and n <= m.re.groups else default

n_cov = f'{_groups(r"(\d+)/", report)}' \
        f'/{_groups(r"/(\d+)\s*条规则有", report)}'
cal_header = f"""# {VERSION} 实测校准附录

> **核心定位**:本文档由 `analyze_results.py` 自动生成,**底部附录承载实战数据**。

"""
# 换行位置对齐:替换起点必须是附录头行首,否则说明范围找错了 —— 早先就是这样
# 把正文章节一起删掉的,所以在这里硬断言,宁可报错也不静默丢内容。
if doc[start:start + 1].isspace():
    raise AssertionError(
        f'替换起点 char {start} 不在行首(前缀 = {doc[start - 20:start]!r})。'
        '检查 _find_appendix_span,不要提交这种替换。')

new_doc = doc[:start] + cal_header + report + '\n---\n\n' + doc[end:]

# 更新版本历史。日期与描述都从报告本身取,不硬编码 —— 报告换了数据,
# 这行才跟着变;行已存在且内容不同就纠正(幂等且自修正)。
gen_date = (re.search(r'\*\*生成日期\*\*:\s*([\d-]+)', report) or [None, 'unknown'])[1]
stable_n = (re.search(r'其中\s*(\d+)\s*条 Stab=100%', report) or [None, '?'])[1]
cov = f'{n_cov} 条规则有数据' if n_cov != '?/?' else f'{stable_n} 条稳健'
row_new = (f'| {VERSION} | {gen_date} | 规则级实测校准:{cov} × 跨周期,'
           f'{stable_n} 条 Stab=100%(数据源 cache/{VERSION}_calibration.md) |')
# 只碰"校准数据行"(含 Stab=100%),不误伤同版本的口径/说明行;
# 没有匹配行才插入。之前 count=1 直接替换掉的是 v4.3 行 —— 升版后上一版
# 的数据记录就从版本历史里消失了,违反"changelog 不能丢数据演进"。
_pat = re.compile(r'^\| v\d+\.\d+ \|.*Stab=100%.*$', re.MULTILINE)
if _pat.search(new_doc):
    new_doc = _pat.sub(row_new, new_doc, count=1)
elif row_new not in new_doc:
    new_doc = new_doc.replace(
        '| v4.2 | 2026-06-07 | 扩 50 币种实测',
        f'{row_new}\n| v4.2 | 2026-06-07 | 扩 50 币种实测'
    )


with open(DOC_PATH, 'w', encoding='utf-8') as f:
    f.write(new_doc)

print(f"✓ 文档模板.md 已更新 {VERSION} 章节")
print(f"  - 替换范围: char {start} ~ {end}")
print(f"  - 新报告长度: {len(report)} chars")
