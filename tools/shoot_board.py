"""设计板验收出图（headless Chrome → 按背景色裁底 → 归档）。

为什么要有这个脚本：以前是手敲 Chrome 命令行 + 手动裁底，两处易错
（`--user-data-dir` 忘了指 G 盘会写 C 盘 profile；`--window-size` 是视口
上限不是自适应，底部会留一截背景空白）。固化下来，验收图可复跑、可比对。

做法：把**生成好的** `design-board.html` 复制一份临时副本，注入一小段
CSS 把不需要的顶层节点 `display:none`，只留目标 section 再截图。
注意是"运行时隐藏"，**不是改产物** —— `design-board.html` 依旧由
`tools/build_design_board.py` 生成，不手改。

用法::

    .venv/Scripts/python.exe tools/shoot_board.py            # 全部
    .venv/Scripts/python.exe tools/shoot_board.py overlay    # 只出某一张

产物：`docs/design/acceptance/board/board-<name>.png`
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "docs" / "design" / "html" / "design-board.html"
OUT_DIR = ROOT / "docs" / "design" / "acceptance" / "board"
TMP_DIR = ROOT / ".build" / "tmp"
PROFILE = ROOT / ".build" / "chrome-profile"

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

#: 页面背景色（底部留白与节间空隙都是它）—— 与 README §3 记录一致
BG = (0xED, 0xEA, 0xE4)
#: 逐像素比对容差（抗 PNG 抗锯齿边缘的轻微偏差）
TOL = 8
#: 注入隔离 CSS 时钉的页面底色（与 BG 同值，写成 CSS 十六进制）
BG_CSS = "#EDEAE4"

VIEW_W = 1120
VIEW_H = 7200

#: (输出名, 保留规则)。``None`` = 整板不隐藏任何节点。
#: 顶层 ``section.sec`` 共 7 个，顺序见 design-board.html。
#: 注意选择器**不能加 ``body > ``** —— section 的父节点是 ``div.wrap``，不是 body。
TARGETS: list[tuple[str, str | None]] = [
    ("full", None),
    ("home", "section.sec:nth-of-type(1)"),
    ("settings", "section.sec:nth-of-type(2)"),
    ("overlay", "section.sec:nth-of-type(3)"),
    ("gallery-components", "section.sec:nth-of-type(4), section.sec:nth-of-type(5)"),
    ("gaps", "section.sec:nth-of-type(6), section.sec:nth-of-type(7)"),
]

#: 只留目标节时，把本板自身的排版（页头/页脚）与其余节一起隐藏。
#: 用 ``{keep}`` / ``{bg}`` 占位、手工 replace —— CSS 的花括号会与 str.format 打架。
#: 必须同时把页面底色钉在 ``html, body`` 上：底色原本挂在 ``.wrap`` 上，
#: 少了这一句，隔离截图会退化成"整页 + 别的节也还在"。
ISOLATE_CSS = """
html, body { background: {bg} !important; }
.wrap > header, .wrap > .foot { display: none !important; }
.sec { display: none !important; }
{keep} { display: block !important; }
"""


def _is_bg(px: tuple[int, int, int], bg: tuple[int, int, int]) -> bool:
    return all(abs(a - b) <= TOL for a, b in zip(px[:3], bg[:3]))


def _content_bottom(im: Image.Image) -> int:
    """自底向上找最后一行还有内容的行（返回下一行索引=可裁掉的位置）。

    背景色取**右下角像素**而不是硬编码常量：隔离出单节时页面底色可能变
    （底色原本挂在被隐藏的容器上），硬编码会让裁底整体失效。
    """
    rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    bg = px[w - 2, h - 2]  # type: ignore[index]
    for y in range(h - 1, -1, -1):
        for x in range(0, w, 4):  # 隔列抽样足够，背景是纯色整行
            if not _is_bg(px[x, y], bg):  # type: ignore[index]
                return y + 1
    return h


def _shoot(html: Path, out_png: Path) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    env = {"TEMP": str(TMP_DIR), "TMP": str(TMP_DIR)}
    cmd = [
        str(CHROME),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={PROFILE}",
        f"--window-size={VIEW_W},{VIEW_H}",
        f"--screenshot={out_png}",
        html.as_uri(),
    ]
    import os

    subprocess.run(cmd, check=True, env={**os.environ, **env}, capture_output=True)


def _write_isolated_copy(name: str, keep: str) -> Path:
    src = BOARD.read_text(encoding="utf-8")
    css = ISOLATE_CSS.replace("{keep}", keep).replace("{bg}", BG_CSS)
    patched = src.replace("</body>", f"<style>{css}</style>\n</body>")
    if patched == src:
        raise RuntimeError("未找到 </body>，产物结构变了？")
    tmp = TMP_DIR / f"board-{name}.html"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(patched, encoding="utf-8")
    return tmp


def shoot(name: str, keep: str | None) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = TMP_DIR / f"_raw-{name}.png"
    html = BOARD if keep is None else _write_isolated_copy(name, keep)
    _shoot(html, raw)

    im = Image.open(raw)
    bottom = _content_bottom(im)
    im.crop((0, 0, im.width, bottom)).save(OUT_DIR / f"board-{name}.png")

    out = OUT_DIR / f"board-{name}.png"
    print(f"[board-shot] {name:<18} {im.width}x{bottom} -> {out.name}")
    return out


def main() -> None:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not CHROME.exists():
        print(f"[board-shot] 找不到 Chrome：{CHROME}")
        raise SystemExit(2)
    if not BOARD.exists():
        print(f"[board-shot] 找不到设计板：{BOARD}（先跑 tools/build_design_board.py）")
        raise SystemExit(2)

    for name, keep in TARGETS:
        if wanted and name not in wanted:
            continue
        shoot(name, keep)


if __name__ == "__main__":
    main()
