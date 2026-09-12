"""CI check: README 提到的入口文件/目录都真实存在 —— 防文档漂移。

README.md 的「项目结构」段把目录、入口文件逐一列出。如果有人删了脚本/目录
却没更新 README,或写了不存在的路径,新读者照着跑会直接 ModuleNotFound /
FileNotFound。这个 check 分两道:

1. 正文里 `路径/文件.py` 形式的反引号条目(命令示例等),逐个 stat。
2. 树状段的条目 token(行首 ├──/└── 或带 │ 缩进的行,取首个空白分隔
   token)按类型校验:
     - 以 / 结尾 → 目录。顶层条目(行首 ├──/└──)或带 / 前缀的按字面校验;
       缩进的裸名(如 cache/ 下的 csv/)无法可靠解析父级,跳过。
     - 已知扩展名(.md/.yml/.in/.lock/.xlsx/.csv/.txt)或白名单裸文件
       (VERSION)→ 文件存在性。
     - .py → 按 README 惯例:裸文件名默认在 core/ 下,带前缀的按字面找。
   cache/、tmp/ 是 gitignore 的运行时目录,CI 干净检出里不存在,只校验
   拼写进白名单、不要求落盘。

任何一个缺失就 fail 并指明在 README 哪一行。

Run: cd <workspace> && .venv/Scripts/python tests/check_readme_entries.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, 'README.md')

# 正文里反引号包裹的 .py 路径(命令示例等)。
PATH_RE = re.compile(r'`([A-Za-z0-9_./-]+\.py)`')

# 树状条目里按字面校验的文件类型 / 无扩展名白名单。
KNOWN_FILE_EXT = ('.md', '.yml', '.yaml', '.in', '.lock', '.xlsx', '.csv', '.txt')
KNOWN_BARE_FILES = {'VERSION'}

# gitignore 的运行时目录:CI 干净检出里没有,不要求落盘,但拼写仍进树。
RUNTIME_DIRS = {'cache', 'tmp'}


def _iter_tree_lines(f):
    """产出 (lineno, stripped_line, is_top_level)。树状行 = 以 │├└─ 开头或
    带这些字符缩进的行;首个 token 即路径条目。"""
    for lineno, line in enumerate(f, 1):
        top = line.startswith(('├──', '└──'))
        stripped = line.lstrip('│├└─ ').strip()
        if stripped:
            yield lineno, stripped, top


def _py_candidates(rel):
    """README 惯例:裸 .py 文件名默认指 core/ 下的入口;带目录前缀的按字面。"""
    cands = [os.path.join(ROOT, rel)]
    if '/' not in rel and '\\' not in rel:
        cands.append(os.path.join(ROOT, 'core', rel))
    return cands


def main():
    if not os.path.exists(README):
        print(f"[ERR] README not found: {README}")
        return 1
    missing = []
    with open(README, encoding='utf-8') as f:
        for lineno, stripped, top in _iter_tree_lines(f):
            # 第一道:反引号 .py 条目(正文命令示例也可能出现在树状行注释里)。
            for m in PATH_RE.finditer(stripped):
                rel = m.group(1)
                if not any(os.path.exists(c) for c in _py_candidates(rel)):
                    missing.append((lineno, f'`{rel}`'))
            # 第二道:树状条目 token。条目本身不含空格,首 token 即路径。
            token = stripped.split()[0]
            if '*' in token:                    # glob(*.parquet)不是具体路径
                continue
            if token.endswith('/'):             # 目录条目
                rel = token.rstrip('/')
                if rel in RUNTIME_DIRS:
                    continue
                if ('/' in rel or top) and not os.path.isdir(os.path.join(ROOT, rel)):
                    missing.append((lineno, token))
                continue
            if token.endswith('.py'):           # 入口脚本条目
                if not any(os.path.exists(c) for c in _py_candidates(token)):
                    missing.append((lineno, token))
                continue
            ext = os.path.splitext(token)[1].lower()
            if token in KNOWN_BARE_FILES or ext in KNOWN_FILE_EXT:
                # 带 / 前缀的按字面;裸名只在顶层条目校验(缩进的无法解析父级)。
                if ('/' in token or top) and not os.path.isfile(os.path.join(ROOT, token)):
                    missing.append((lineno, token))
            elif top and '.' not in token:
                # 顶层无扩展名裸 token(如 VERSION):拼错会变成「未知名字」静默漏过,
                # 按「文件或目录存在其一」兜底校验。
                if not os.path.exists(os.path.join(ROOT, token)):
                    missing.append((lineno, token))
    if missing:
        print(f"[FAIL] README mentions {len(missing)} path(s) that don't exist:")
        for lineno, rel in missing:
            print(f"  line {lineno}: {rel}")
        return 1
    print("[OK] all README path entries exist (files + directories)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
