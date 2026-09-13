"""从 app/ui/theme/tokens.py 导出 Figma 可导入的设计变量（Tokens Studio schema）。

用法::

    .venv/Scripts/python.exe tools/export_figma_tokens.py

产出::

    docs/design/figma/tokens.json         Tokens Studio for Figma 可直接加载
    docs/design/figma/tokens-report.md    本次导出的变量清单与改名映射

为什么走 Tokens Studio 而不是在 Figma 里手建变量
------------------------------------------------
Figma 没有「从一份 JSON 批量创建变量」的原生入口。官方可行路径是
Tokens Studio for Figma 插件：

    Storage Type → File → Load from File（选 tokens.json）
    → Styles & Variables → Export styles & variables to Figma

60+ 个变量手建既慢又容易抄错，所以这里做机器导出。

命名约定
--------
Python 常量名（PRIMARY_HOVER）不是好的变量名，导出时改成 Figma 惯用的
分组路径（color/brand/hover），与 tokens.py 的注释分组保持一致。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ui.theme import tokens as T  # noqa: E402

OUT_DIR = ROOT / "docs" / "design" / "figma"


def color(value: str) -> dict:
    return {"value": value, "type": "color"}


def number(value: int, unit: str = "px") -> dict:
    return {"value": f"{value}{unit}", "type": "number"}


def alias(path: str) -> dict:
    return {"value": "{" + path + "}"}


def parse_shadow(raw: str) -> dict:
    """'0 2px 8px rgba(45, 55, 72, 0.06)' -> Tokens Studio boxShadow 值对象。

    按 CSS box-shadow 语法解析：长度段 3 个 = x y blur（spread 缺省 0），
    4 个 = x y blur spread。注意不要假设每个数字都带 px——
    tokens.py 里 SHADOW_FOCUS 写作 '0 0 0 3px'，只有末位带单位。
    """
    m = re.match(r"^(.*?)\s*(rgba?\([^)]+\))\s*$", raw)
    if not m:
        raise ValueError(f"无法解析阴影: {raw!r}")
    lengths = m.group(1).split()
    col = m.group(2)

    if len(lengths) == 3:
        x, y, blur = lengths
        spread = "0"
    elif len(lengths) == 4:
        x, y, blur, spread = lengths
    else:
        raise ValueError(f"无法解析阴影长度段（需 3 或 4 个）: {raw!r}")

    def with_unit(v: str) -> str:
        return v if v.endswith("px") else f"{v}px"

    return {
        "value": {
            "x": with_unit(x),
            "y": with_unit(y),
            "blur": with_unit(blur),
            "spread": with_unit(spread),
            "color": col,
            "type": "dropShadow",
        },
        "type": "boxShadow",
    }


def build() -> dict:
    tokens: dict = {}

    tokens["color"] = {
        "bg": {
            "canvas": color(T.BG_CANVAS),
            "surface": color(T.BG_SURFACE),
            "soft": color(T.BG_SOFT),
            "raised": alias("color.bg.surface"),
            "sidebar": color(T.SIDEBAR_BG),
            "sidebar-hover": color(T.SIDEBAR_ITEM_HOVER),
            "sidebar-active": alias("color.bg.surface"),
        },
        "border": {
            "default": color(T.BORDER),
            "soft": color(T.BORDER_SOFT),
            "focus": alias("color.brand.primary"),
        },
        "text": {
            "primary": color(T.TEXT_PRIMARY),
            "secondary": color(T.TEXT_SECONDARY),
            "muted": color(T.TEXT_MUTED),
            "disabled": color(T.TEXT_DISABLED),
            "inverted": color(T.TEXT_INVERTED),
            "brand": alias("color.brand.text"),
        },
        "brand": {
            "primary": color(T.PRIMARY),
            "hover": color(T.PRIMARY_HOVER),
            "pressed": color(T.PRIMARY_PRESSED),
            "soft": color(T.PRIMARY_SOFT),
            "text": color(T.PRIMARY_TEXT),
        },
        "info": {
            "base": color(T.INFO),
            "soft": color(T.INFO_SOFT),
        },
        "accent": {
            "base": color(T.ACCENT),
            "deep": color(T.ACCENT_DEEP),
            "soft": color(T.ACCENT_SOFT),
        },
        "status": {
            "warning": color(T.WARNING),
            "warning-soft": color(T.WARNING_SOFT),
            "danger": color(T.DANGER),
            "danger-soft": color(T.DANGER_SOFT),
            "success": alias("color.brand.primary"),
        },
        "rhythm": {
            "blink": alias("color.brand.primary"),
            "look": alias("color.info.base"),
            "move": alias("color.accent.deep"),
            "deep": color(T.RHYTHM_DEEP),
        },
        "overlay": {
            "scrim": color(T.OVERLAY_SCRIM),
            "tip-bg": color(T.OVERLAY_TIP_BG),
            "tip-text": color(T.OVERLAY_TIP_TEXT),
            # 视觉提示卡（真卡 + 编辑预览共用，2026-09-13 收敛为一份）
            "cue-card-bg": color(T.CUE_CARD_BG),
            "cue-card-border": color(T.CUE_CARD_BORDER),
        },
    }

    tokens["space"] = {
        str(n): number(v) for n, v in [
            (1, T.SPACE_1), (2, T.SPACE_2), (3, T.SPACE_3), (4, T.SPACE_4),
            (5, T.SPACE_5), (6, T.SPACE_6), (8, T.SPACE_8), (10, T.SPACE_10),
        ]
    }
    # 组件级语义别名：按钮内边距（§4 裁定 = 水平 24 / 垂直 12）
    tokens["space"]["button-v"] = alias("space.3")
    tokens["space"]["button-h"] = alias("space.6")

    tokens["radius"] = {
        "sm": number(T.RADIUS_SM),
        "md": number(T.RADIUS_MD),
        "lg": number(T.RADIUS_LG),
        "xl": number(T.RADIUS_XL),
        "pill": number(T.RADIUS_PILL),
        "button": number(T.RADIUS_BUTTON),
        "cue-card": number(T.RADIUS_CUE_CARD),
    }

    tokens["size"] = {
        "sidebar-width": number(T.SIDEBAR_WIDTH),
        "window-default-w": number(T.WINDOW_DEFAULT_W),
        "window-default-h": number(T.WINDOW_DEFAULT_H),
        "window-min-w": number(T.WINDOW_MIN_W),
        "window-min-h": number(T.WINDOW_MIN_H),
    }

    tokens["font-size"] = {
        "display": number(T.DISPLAY, ""),
        "h1": number(T.H1, ""),
        "h2": number(T.H2, ""),
        "h3": number(T.H3, ""),
        "body": number(T.BODY, ""),
        "secondary": number(T.SECONDARY, ""),
        "caption": number(T.CAPTION, ""),
    }

    tokens["shadow"] = {
        "card": parse_shadow(T.SHADOW_CARD),
        "raised": parse_shadow(T.SHADOW_RAISED),
        "focus": parse_shadow(T.SHADOW_FOCUS),
    }

    return tokens


def walk(node: dict, prefix: str = "") -> list[tuple[str, object]]:
    """展平成 (路径, 值) 列表，只取叶子（带 type 的节点）。"""
    out = []
    for k, v in node.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and "type" in v:
            out.append((path, v))
        elif isinstance(v, dict) and "value" in v and "type" not in v:
            out.append((path, v))          # 别名节点
        elif isinstance(v, dict):
            out.extend(walk(v, path))
    return out


def main() -> int:
    tokens = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUT_DIR / "tokens.json"
    json_path.write_text(
        json.dumps(tokens, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    leaves = walk(tokens)
    groups: dict[str, int] = {}
    for path, _ in leaves:
        groups[path.split(".")[0]] = groups.get(path.split(".")[0], 0) + 1

    aliases = [(p, v["value"]) for p, v in leaves if isinstance(v.get("value"), str)
               and v["value"].startswith("{")]
    literals = [(p, v) for p, v in leaves if p not in {a[0] for a in aliases}]

    print(f"[tokens] 写出 {json_path}")
    print(f"[tokens] 共 {len(leaves)} 个变量，其中别名 {len(aliases)}、字面值 {len(literals)}")
    print()
    print("按分组：")
    for g, n in sorted(groups.items(), key=lambda t: -t[1]):
        print(f"  {g:<12} {n:>3} 个")
    print()
    print("别名（改一处自动跟随）：")
    for p, v in aliases:
        print(f"  {p:<28} -> {v}")
    print()

    # 重复字面值体检：值相同但不是别名的，提示可考虑合并
    by_val: dict[str, list[str]] = {}
    for p, v in literals:
        val = v["value"]
        key = json.dumps(val, sort_keys=True) if isinstance(val, dict) else str(val)
        by_val.setdefault(key, []).append(p)
    dupes = {k: v for k, v in by_val.items() if len(v) > 1}
    if dupes:
        print("值重复但未设别名的项（可考虑改成别名）：")
        for k, paths in dupes.items():
            print(f"  {k}  <-  {', '.join(paths)}")
    else:
        print("无重复字面值。")

    report = [
        "# Figma 变量导出报告",
        "",
        f"- 来源：`app/ui/theme/tokens.py`",
        f"- 产出：`docs/design/figma/tokens.json`（Tokens Studio for Figma schema）",
        f"- 变量总数：**{len(leaves)}**（别名 {len(aliases)} + 字面值 {len(literals)}）",
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
        report.append(f"| `{p}` | `{v.strip('{}')}` |")
    report += [
        "",
        "## 命名映射（Python 常量 → Figma 变量）",
        "",
        "Python 侧的常量名不是好的变量名，导出时按语义重新分组。",
        "改 `tokens.py` 后重跑本脚本即可重新导出，不要手改 `tokens.json`。",
        "",
        "| tokens.py | Figma 变量 |",
        "|---|---|",
        "| `PRIMARY` | `color/brand/primary` |",
        "| `PRIMARY_HOVER` | `color/brand/hover` |",
        "| `PRIMARY_PRESSED` | `color/brand/pressed` |",
        "| `PRIMARY_SOFT` | `color/brand/soft` |",
        "| `PRIMARY_TEXT` | `color/brand/text` |",
        "| `BG_CANVAS` | `color/bg/canvas` |",
        "| `BG_SURFACE` | `color/bg/surface` |",
        "| `TEXT_PRIMARY` | `color/text/primary` |",
        "| `RHYTHM_BLINK/LOOK/MOVE/DEEP` | `color/rhythm/*` |",
        "| `SPACE_n` | `space/n` |",
        "| `RADIUS_*` | `radius/*` |",
        "| `RADIUS_BUTTON` | `radius/button`（§4 裁定：按钮统一 r16） |",
        "| `BUTTON_PAD_V/H` | `space/button-v`(→`space/3`) / `space/button-h`(→`space/6`) |",
        "| `OVERLAY_SCRIM/TIP_BG/TIP_TEXT` | `color/overlay/*`（alpha 用 0-1 小数） |",
        "| `CUE_CARD_BG/BORDER` | `color/overlay/cue-card-*`（真卡 + 编辑预览共用） |",
        "| `RADIUS_CUE_CARD` | `radius/cue-card` |",
        "| `DISPLAY/H1/H2/H3/BODY/SECONDARY/CAPTION` | `font-size/*` |",
        "| `SHADOW_*` | `shadow/*` |",
        "",
    ]
    (OUT_DIR / "tokens-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"[tokens] 写出 {OUT_DIR / 'tokens-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
