# 设计 token 的 CSS / WXSS 导出报告

- 来源：`app/ui/theme/tokens.py`（唯一真源）
- 产出：`tokens.css`（72 变量，`:root` 作用域）
        `tokens.wxss`（同 72 变量，`page` 作用域）
- 变量总数：**72**（别名 10 + 字面值 62）

## 分组统计

| 分组 | 变量数 |
|---|---|
| `color` | 40 |
| `space` | 10 |
| `radius` | 7 |
| `font-size` | 7 |
| `size` | 5 |
| `shadow` | 3 |

## 别名映射

| 变量 | 指向 |
|---|---|
| `--color-bg-raised` | `--color-bg-surface` |
| `--color-bg-sidebar-active` | `--color-bg-surface` |
| `--color-border-focus` | `--color-brand-primary` |
| `--color-text-brand` | `--color-brand-text` |
| `--color-status-success` | `--color-brand-primary` |
| `--color-rhythm-blink` | `--color-brand-primary` |
| `--color-rhythm-look` | `--color-info-base` |
| `--color-rhythm-move` | `--color-accent-deep` |
| `--space-button-v` | `--space-3` |
| `--space-button-h` | `--space-6` |

## 三目标可实现性对照

同一份 token 投到三个目标，损耗完全不同：

| 能力 | CSS（浏览器/设计板） | WXSS（微信小程序） | QSS（Qt Widgets） |
|---|---|---|---|
| CSS 变量 `var()` | ✅ 原生 | ✅ 需基础库 2.25.0+ | ❌ 无原生变量，须手工代入字面值 |
| `box-shadow` | ✅ 原生 | ✅ 但不支持多层叠加 | ❌ **完全不支持** |
| `backdrop-filter` | ✅ | ✅（有限制） | ❌ **完全没有** |
| `transition` / `transform` | ✅ | ✅ | ❌ 不支持 |
| `line-height` | ✅ | ✅ | ❌ 无此 QSS 属性 |
| `letter-spacing` | ✅ | ✅ | ⚠️ 只能走 `QFont.setLetterSpacing` |
| Flex / Grid 布局 | ✅ | ✅ Flex（无 Grid） | ⚠️ 仅 QHBox/VBox/Grid 布局，非 CSS 语法 |

### 结论

- **CSS 侧：零损耗。** 72 个变量全部直出，含 3 个阴影——因为 `tokens.py` 里的阴影本来就是 CSS 语法。
- **WXSS 侧：接近零损耗。** 只需把作用域从 `:root` 换成 `page`，并逐文件 `@import`。
- **QSS 侧：有损。** 变量机制不存在，阴影与模糊能力缺失，这套 token 需要写一个 Qt 专用转换器把值代入 QSS 字面量。

> 具体而言：`tokens.py` 的 72 个 token 里，`shadow.*` 3 个在 QSS 侧无处安放（必须改用 `QGraphicsDropShadowEffect`，而它一个 widget 只能挂一个）；`font-size.*` 的 7 个要写进 QSS `font-size`；其余颜色/间距/圆角可以代入字面值使用，但**失去「改一处全变」的能力**。

