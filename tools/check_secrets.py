#!/usr/bin/env python3
"""提交前扫描：防止私密信息 / API 密钥被推上远端。

用法::

    python tools/check_secrets.py            # 扫暂存区（pre-commit 用）
    python tools/check_secrets.py --all      # 扫全部已跟踪文件
    python tools/check_secrets.py --install-hook   # 装成 .git/hooks/pre-commit

命中即返回 1，并打印 `文件:行号 类型 命中片段(已打码)`。
设计原则：**宁可偶尔误报**，也不要放过一个真密钥——误报改一行，
泄漏改不了（GitHub 上历史记录会永久保留）。

注意：本文件自身不参与扫描，且所有敏感模式都做了字面量拆分，
避免「扫描器把自己的模式串当成密钥」这种自指误报。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SELF = "tools/check_secrets.py"

#: 内容模式：(类型, 正则)。密钥形态都来自各平台官方前缀。
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(r"gh" + r"[pousr]_[A-Za-z0-9]{36,}")),
    ("GitHub PAT", re.compile(r"github_" + r"pat_[A-Za-z0-9_]{20,}")),
    ("AWS Access Key", re.compile(r"AK" + r"IA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AI" + r"za[0-9A-Za-z_\-]{35}")),
    ("OpenAI-style key", re.compile(r"sk" + r"-[A-Za-z0-9_\-]{20,}")),
    ("Slack token", re.compile(r"xox" + r"[baprs]-[A-Za-z0-9\-]{10,}")),
    ("私钥文件内容", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer 明文", re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{20,}")),
    (
        "疑似密钥赋值",
        re.compile(
            r"""(?i)\b(api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|"""
            r"""client[_-]?secret|password|passwd|pwd)\b\s*[:=]\s*["']([^"'\n]{12,})["']"""
        ),
    ),
]

#: 文件名模式：这些文件天生装密钥，出现即阻断（`.example` / `.sample` 除外）。
_BAD_NAMES = re.compile(
    r"(?i)(^|/)(\.env(\..*)?|id_rsa|id_ed25519|credentials\.json|secrets?\.(json|ya?ml|toml)|"
    r"[^/]*\.(pem|key|p12|pfx|jks))$"
)
_OK_SUFFIX = (".example", ".sample", ".template", ".dist")


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return out.stdout


def _targets(scan_all: bool) -> list[str]:
    if scan_all:
        return [p for p in _git("ls-files").splitlines() if p]
    return [
        p
        for p in _git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines()
        if p
    ]


def _mask(s: str) -> str:
    s = s.strip()
    return s[:4] + "…" + f"({len(s)} 字符)" if len(s) > 4 else "…"


def scan(paths: list[str]) -> list[str]:
    problems: list[str] = []
    for rel in paths:
        if rel == SELF:
            continue
        name = rel.replace("\\", "/")
        if _BAD_NAMES.search(name) and not name.endswith(_OK_SUFFIX):
            problems.append(f"{rel}:1  敏感文件名  {name}")
            continue
        f = REPO / rel
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text[:4096]:  # 二进制跳过
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for label, pat in _PATTERNS:
                m = pat.search(line)
                if m:
                    hit = m.group(2) if m.lastindex and m.group(2) else m.group(0)
                    problems.append(f"{rel}:{i}  {label}  {_mask(hit)}")
    return problems


def install_hook() -> int:
    hook = REPO / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "# 由 tools/check_secrets.py --install-hook 生成\n"
        'REPO="$(git rev-parse --show-toplevel)"\n'
        'for PY in "$REPO/.venv/Scripts/python.exe" "$REPO/.venv/bin/python" python3 python py; do\n'
        '  if command -v "$PY" >/dev/null 2>&1; then\n'
        '    exec "$PY" "$REPO/tools/check_secrets.py"\n'
        "  fi\n"
        "done\n"
        'echo "[check_secrets] 未找到可用的 python，跳过扫描" >&2\n'
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已安装 pre-commit 钩子：{hook}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="提交前私密信息扫描")
    ap.add_argument("--all", action="store_true", help="扫全部已跟踪文件（默认只扫暂存区）")
    ap.add_argument("--install-hook", action="store_true", help="安装 pre-commit 钩子")
    args = ap.parse_args()

    if args.install_hook:
        return install_hook()

    paths = _targets(args.all)
    if not paths:
        print("没有待扫描的文件。")
        return 0

    problems = scan(paths)
    scope = "全部已跟踪文件" if args.all else "暂存区"
    if problems:
        print(f"[!] {scope}中发现 {len(problems)} 处疑似私密信息，已阻断：\n")
        for p in problems:
            print("  " + p)
        print(
            "\n处理建议：\n"
            "  1) 把值挪进未跟踪的本地文件（如 .env），并在代码里读环境变量；\n"
            "  2) 确认该文件已被 .gitignore 忽略（git check-ignore -v <文件>）；\n"
            "  3) 若确实是误报（文档示例、占位符），调整写法或加入白名单；\n"
            "  4) 已经提交过的密钥必须视为已泄漏 —— 立即在平台侧吊销并重签。"
        )
        return 1

    print(f"[ok] {scope}扫描通过：{len(paths)} 个文件，未发现私密信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
