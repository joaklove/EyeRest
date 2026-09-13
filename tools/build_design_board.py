"""生成自包含的 HTML 设计板（路线 C 的设计源）。

用法::

    .venv/Scripts/python.exe tools/build_design_board.py

输入 / 输出::

    docs/design/html/board.src.html          <- 手写的结构（唯一可编辑处）
    app/ui/theme/tokens.py                   <- token 唯一真源
    app/assets/fonts/ZCOOLKuaiLe-Regular.ttf <- 品牌圆体
            |
            v
    docs/design/html/design-board.html       <- 产物，单文件零外部依赖，禁止手改

为什么要生成而不是直接手写
--------------------------
设计板必须用**真的 token 值**，否则它就成了第二份真源，一定会与
`tokens.py` 漂移。所以产物里内联两样东西：

1. token CSS —— 调 ``export_css_tokens.render()``，与 ``tokens.css`` 同源同值；
2. 品牌字体 —— base64 data URI 内嵌 ``ZCOOLKuaiLe-Regular.ttf``。

第 2 点解决一个具体问题：品牌字体只随应用分发（``app/assets/fonts/``），
**没有装进系统字体库**，浏览器按 family 名找不到它。内嵌 data URI 后
设计板双击即可正确渲染品牌字，且不依赖任何外部文件或系统安装。

改 tokens.py 后重跑本脚本即可，**不要手改 design-board.html**。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from export_css_tokens import render  # noqa: E402
from export_figma_tokens import build, walk  # noqa: E402

HTML_DIR = ROOT / "docs" / "design" / "html"
SRC = HTML_DIR / "board.src.html"
OUT = HTML_DIR / "design-board.html"
FONT = ROOT / "app" / "assets" / "fonts" / "ZCOOLKuaiLe-Regular.ttf"

TOKENS_MARKER = "/* {{TOKENS}} */"
FONT_MARKER = "/* {{BRAND_FONT}} */"

# 字体内嵌的体积上限。超过就走相对路径回退，避免产物大到打不开。
MAX_EMBED_FONT_BYTES = 8 * 1024 * 1024


def font_block() -> str:
    """生成 @font-face 规则：优先 base64 内嵌，超限则回退相对路径。"""
    if not FONT.exists():
        return "/* 品牌字体缺失，回退系统字体 */"

    size = FONT.stat().st_size
    if size > MAX_EMBED_FONT_BYTES:
        rel = FONT.relative_to(HTML_DIR).as_posix()
        return (
            "/* 品牌字体超过内嵌上限，改用相对路径（设计板须与仓库同处） */\n"
            "@font-face {\n"
            '  font-family: "ZCOOL KuaiLe";\n'
            f"  src: url('{rel}') format('truetype');\n"
            "  font-display: swap;\n"
            "}"
        )

    b64 = base64.b64encode(FONT.read_bytes()).decode("ascii")
    return (
        "/* 品牌字体：站酷快乐体（OFL），与应用同款，base64 内嵌 */\n"
        "@font-face {\n"
        '  font-family: "ZCOOL KuaiLe";\n'
        f"  src: url(data:font/ttf;base64,{b64}) format('truetype');\n"
        "  font-weight: 400;\n"
        "  font-style: normal;\n"
        "  font-display: swap;\n"
        "}"
    )


def main() -> int:
    if not SRC.exists():
        raise SystemExit(f"缺少源文件：{SRC}")

    leaves = walk(build())
    token_css = render(leaves, ":root").rstrip()

    src = SRC.read_text(encoding="utf-8")
    for marker in (TOKENS_MARKER, FONT_MARKER):
        if marker not in src:
            raise SystemExit(f"源文件缺少标记 {marker}")

    banner = (
        "/* ============================================================\n"
        "   本文件由 tools/build_design_board.py 生成，请勿手改。\n"
        f"   结构源：docs/design/html/board.src.html\n"
        f"   token 真源：app/ui/theme/tokens.py（{len(leaves)} 个变量）\n"
        "   ============================================================ */"
    )

    out = src.replace(TOKENS_MARKER, f"{banner}\n{token_css}")
    out = out.replace(FONT_MARKER, font_block())
    OUT.write_text(out, encoding="utf-8")

    kb = OUT.stat().st_size / 1024
    print(f"[board] 写出 {OUT}")
    print(f"[board] 体积 {kb:,.0f} KB（含内嵌字体与 {len(leaves)} 个 token）")
    print(f"[board] 单一文件、零外部依赖，双击即可打开")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
