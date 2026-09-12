"""CI check: README 提到的入口文件都真实存在 —— 防文档漂移。

README.md 的「项目结构」段把 core/ 的主入口逐一列出。如果有人删了脚本却没
更新 README,或写了不存在的路径,新读者照着跑会直接 ModuleNotFound。这个
check 用正则把 README 里 `路径/文件.py` 形式的条目抽出来,逐个 stat 确认存在;
任何一个缺失就 fail 并指明在 README 哪一行。

Run: cd <workspace> && .venv/Scripts/python tests/check_readme_entries.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, 'README.md')

# README 结构图里出现的、带 .py 后缀的路径条目(含子目录前缀的)。
# 只校验明确写出的文件名,不校验注释性描述。
PATH_RE = re.compile(r'`([A-Za-z0-9_./-]+\.py)`')


def main():
    if not os.path.exists(README):
        print(f"[ERR] README not found: {README}")
        return 1
    missing = []
    with open(README, encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            # 只看「项目结构」代码块里的行(以 ├── / │ / └ 开头的树状行),
            # 避免抓到正文里举例的命令名。
            stripped = line.lstrip('│├└─ ')
            if not stripped:
                continue
            for m in PATH_RE.finditer(line):
                rel = m.group(1)
                # README 结构条目里的 .py 多半是裸文件名(如 run_batch.py),在 core/ 下;
                # 带目录前缀的按字面找。两路径都试一下。
                cands = [os.path.join(ROOT, rel)]
                if '/' not in rel and '\\' not in rel:
                    cands.append(os.path.join(ROOT, 'core', rel))
                if not any(os.path.exists(c) for c in cands):
                    missing.append((lineno, rel))
    if missing:
        print(f"[FAIL] README mentions {len(missing)} .py path(s) that don't exist:")
        for lineno, rel in missing:
            print(f"  line {lineno}: `{rel}`")
        return 1
    print(f"[OK] all README .py entries exist")
    return 0


if __name__ == '__main__':
    sys.exit(main())
