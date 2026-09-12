# Figma 变量导出报告

- 来源：`app/ui/theme/tokens.py`
- 产出：`docs/design/figma/tokens.json`（Tokens Studio for Figma schema）
- 变量总数：**63**（别名 8 + 字面值 55）

## 分组统计

| 分组 | 变量数 |
|---|---|
| `color` | 35 |
| `space` | 8 |
| `font-size` | 7 |
| `radius` | 5 |
| `size` | 5 |
| `shadow` | 3 |

## 别名映射

| 变量 | 指向 |
|---|---|
| `color.bg.raised` | `color.bg.surface` |
| `color.bg.sidebar-active` | `color.bg.surface` |
| `color.border.focus` | `color.brand.primary` |
| `color.text.brand` | `color.brand.text` |
| `color.status.success` | `color.brand.primary` |
| `color.rhythm.blink` | `color.brand.primary` |
| `color.rhythm.look` | `color.info.base` |
| `color.rhythm.move` | `color.accent.deep` |

## 命名映射（Python 常量 → Figma 变量）

Python 侧的常量名不是好的变量名，导出时按语义重新分组。
改 `tokens.py` 后重跑本脚本即可重新导出，不要手改 `tokens.json`。

| tokens.py | Figma 变量 |
|---|---|
| `PRIMARY` | `color/brand/primary` |
| `PRIMARY_HOVER` | `color/brand/hover` |
| `PRIMARY_PRESSED` | `color/brand/pressed` |
| `PRIMARY_SOFT` | `color/brand/soft` |
| `PRIMARY_TEXT` | `color/brand/text` |
| `BG_CANVAS` | `color/bg/canvas` |
| `BG_SURFACE` | `color/bg/surface` |
| `TEXT_PRIMARY` | `color/text/primary` |
| `RHYTHM_BLINK/LOOK/MOVE/DEEP` | `color/rhythm/*` |
| `SPACE_n` | `space/n` |
| `RADIUS_*` | `radius/*` |
| `DISPLAY/H1/H2/H3/BODY/SECONDARY/CAPTION` | `font-size/*` |
| `SHADOW_*` | `shadow/*` |

