"""从 app/ui/theme/tokens.py 导出 CSS 变量（HTML 设计板）与 WXSS 变量（微信小程序）。

用法::

    .venv/Scripts/python.exe tools/export_css_tokens.py

产出::

    docs/design/html/tokens.css        HTML 设计板用（:root 作用域）
    docs/design/html/tokens.wxss       微信小程序用（page 作用域）
    docs/design/html/tokens-report.md  三目标可实现性对照

设计要点
--------
tokens.py 是**唯一真源**。复用 ``export_figma_tokens.build()`` 的中间表示与命名，
让 Figma / CSS / WXSS 三处共用同一套语义路径：

    color.bg.canvas  ->  --color-bg-canvas   （CSS / WXSS）
                     ->  color/bg/canvas     （Figma）

改 tokens.py 后重跑三个导出脚本即可，任何一处都不要手改产物。

三目标的语法差异（本脚本要处理的）
----------------------------------
1. 作用域：浏览器用 ``:root``；**小程序不认 ``:root``**，必须写 ``page``。
2. 字号：Tokens Studio 侧是无单位数字（Figma 习惯），CSS/WXSS 必须带 ``px``。
3. 阴影：中间表示是 boxShadow 对象，CSS 侧要还原成 ``x y blur spread color``。
   注意 spread 为 0 时按 CSS 规范可省略。
4. 别名：CSS 用 ``var(--x)`` 而非 ``{color.x}``。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from export_figma_tokens import build, walk  # noqa: E402  （tools/ 已在 sys.path）

OUT_DIR = ROOT / "docs" / "design" / "html"


def var_name(path: str) -> str:
    """color.bg.canvas -> --color-bg-canvas"""
    return "--" + path.replace(".", "-")


def shadow_to_css(node: dict) -> str:
    """boxShadow 中间表示 -> CSS 值字符串。"""
    d = node["value"]

    def norm_len(v: str) -> str:
        return "0" if v in ("0", "0px") else v

    parts = [norm_len(d["x"]), norm_len(d["y"]), norm_len(d["blur"])]
    spread = d.get("spread", "0")
    if spread not in ("0", "0px"):
        parts.append(spread)
    parts.append(d["color"])
    return " ".join(parts)


def with_unit(raw: str) -> str:
    """纯数字补 px；已带单位（px/%/空）原样返回。"""
    s = str(raw)
    if s.replace(".", "", 1).isdigit():
        return f"{s}px"
    return s


def render(leaves: list[tuple[str, object]], scope: str) -> str:
    """生成变量块。scope 为 ':root'（HTML）或 'page'（小程序）。"""
    lines = [
        "/* 由 tools/export_css_tokens.py 从 app/ui/theme/tokens.py 生成，请勿手改。 */",
        f"{scope} {{",
    ]
    by_group: dict[str, list[str]] = {}
    for path, node in leaves:
        group = path.split(".")[0]
        name = var_name(path)
        raw = node["value"]

        if isinstance(raw, str) and raw.startswith("{") and raw.endswith("}"):
            val = f"var({var_name(raw.strip('{}'))})"
        elif isinstance(raw, str) and ("px" in raw or "%" in raw):
            val = raw
        elif isinstance(raw, (int, float)):
            val = with_unit(raw)
        elif isinstance(raw, str) and group == "shadow":
            val = raw
        elif isinstance(raw, dict):
            val = shadow_to_css(node)
        else:
            val = with_unit(raw)

        by_group.setdefault(group, []).append(f"  {name}: {val};")

    for group in sorted(by_group):
        lines.append(f"  /* {group} */")
        lines.extend(by_group[group])
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    tokens = build()
    leaves = walk(tokens)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    aliases = [
        (p, v["value"]) for p, v in leaves
        if isinstance(v.get("value"), str) and v["value"].startswith("{")
    ]
    groups: dict[str, int] = {}
    for path, _ in leaves:
        g = path.split(".")[0]
        groups[g] = groups.get(g, 0) + 1

    css = render(leaves, ":root")
    wxss = render(leaves, "page")

    (OUT_DIR / "tokens.css").write_text(css, encoding="utf-8")
    (OUT_DIR / "tokens.wxss").write_text(wxss, encoding="utf-8")

    shadows = [(p, v) for p, v in leaves if p.startswith("shadow.")]

    report = [
        "# 设计 token 的 CSS / WXSS 导出报告",
        "",
        "- 来源：`app/ui/theme/tokens.py`（唯一真源）",
        f"- 产出：`tokens.css`（{len(leaves)} 变量，`:root` 作用域）",
        f"        `tokens.wxss`（同 {len(leaves)} 变量，`page` 作用域）",
        f"- 变量总数：**{len(leaves)}**（别名 {len(aliases)} + 字面值 {len(leaves) - len(aliases)}）",
        "",
        "## 分组统计",
        "",
        "| 分组 | 变量数 |",
        "|---|---|",
    ]
    for g, n in sorted(groups.items(), key=lambda t: -t[1]):
        report.append(f"| `{g}` | {n} |")

    report += ["", "## 别名映射", "", "| 变量 | 指向 |", "|---|---|"]
    for p, v in aliases:
        report.append(f"| `{var_name(p)}` | `{var_name(v.strip('{}'))}` |")

    report += [
        "",
        "## 三目标可实现性对照",
        "",
        "同一份 token 投到三个目标，损耗完全不同：",
        "",
        "| 能力 | CSS（浏览器/设计板） | WXSS（微信小程序） | QSS（Qt Widgets） |",
        "|---|---|---|---|",
        "| CSS 变量 `var()` | ✅ 原生 | ✅ 需基础库 2.25.0+ | ❌ 无原生变量，须手工代入字面值 |",
        "| `box-shadow` | ✅ 原生 | ✅ 但不支持多层叠加 | ❌ **完全不支持** |",
        "| `backdrop-filter` | ✅ | ✅（有限制） | ❌ **完全没有** |",
        "| `transition` / `transform` | ✅ | ✅ | ❌ 不支持 |",
        "| `line-height` | ✅ | ✅ | ❌ 无此 QSS 属性 |",
        "| `letter-spacing` | ✅ | ✅ | ⚠️ 只能走 `QFont.setLetterSpacing` |",
        "| Flex / Grid 布局 | ✅ | ✅ Flex（无 Grid） | ⚠️ 仅 QHBox/VBox/Grid 布局，非 CSS 语法 |",
        "",
        "### 结论",
        "",
        f"- **CSS 侧：零损耗。** {len(leaves)} 个变量全部直出，含 "
        f"{len(shadows)} 个阴影——因为 `tokens.py` 里的阴影本来就是 CSS 语法。",
        "- **WXSS 侧：接近零损耗。** 只需把作用域从 `:root` 换成 `page`，并逐文件 `@import`。",
        "- **QSS 侧：有损。** 变量机制不存在，阴影与模糊能力缺失，"
        "这套 token 需要写一个 Qt 专用转换器把值代入 QSS 字面量。",
        "",
        f"> 具体而言：`tokens.py` 的 {len(leaves)} 个 token 里，"
        "`shadow.*` 3 个在 QSS 侧无处安放（必须改用 `QGraphicsDropShadowEffect`，"
        "而它一个 widget 只能挂一个）；`font-size.*` 的 7 个要写进 QSS `font-size`；"
        "其余颜色/间距/圆角可以代入字面值使用，但**失去「改一处全变」的能力**。",
        "",
    ]
    (OUT_DIR / "tokens-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(f"[css] 写出 {OUT_DIR / 'tokens.css'}（{len(leaves)} 变量）")
    print(f"[css] 写出 {OUT_DIR / 'tokens.wxss'}（{len(leaves)} 变量）")
    print(f"[css] 写出 {OUT_DIR / 'tokens-report.md'}")
    print()
    print("按分组：")
    for g, n in sorted(groups.items(), key=lambda t: -t[1]):
        print(f"  {g:<12} {n:>3}")
    print()
    print(f"阴影（CSS 侧可直出，QSS 侧会丢）：{len(shadows)} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
