# 路线 C 评估：无 Figma（HTML 设计板 + Qt 截图对账）

- 日期：2026-09-12
- 背景：Figma 侧撞上 Starter 席位 20 次/月配额（`UI_UX_WORKFLOW_FIGMA.md` §3.3），
  且绕开配额的插件桥方案需要装 Figma 桌面版（§3.4），与本项目"严禁写 C 盘"硬约束冲突。
- 本文件回答三件事：① EyeRest 走这条路会有什么变化 ② 小程序等后续项目适不适合
  ③ 对比 Figma 差多少。

---

## 0. 三个问题的直接回答

| 问题 | 回答 |
|---|---|
| **走路线 C，EyeRest 开发会有什么变化？** | 设计源从"云端 Figma 文件"换成"本地 `tokens.py` + 单文件 HTML"。**改主色从「重跑导出 + 批量写 Figma」变成「重跑一个 3 秒的脚本」**。视觉产出不变。 |
| **小程序 / 其他应用适合吗？** | **越靠近 Web 技术栈越合适。** 小程序（WXSS 支持 CSS 变量）与 Web 是**最佳场景，甚至反超 Figma**；Qt Widgets 这类原生自绘控件是**最差场景**（设计板画得出、QSS 接不住）。 |
| **效果对比走 Figma 差多少？** | **最终视觉：没有差距。** 整屏观感的上限由**引擎能力**与**插画资产**决定，不由设计工具决定——Qt Widgets 走哪条路都是 ~80%。差距在**过程维度**（见 §4）。 |

---

## 1. 机制已验证：一份 token，三个目标

新建 `tools/export_css_tokens.py`，**复用** `export_figma_tokens.build()` 的中间表示，
让三个目标共用同一套语义路径：

```
app/ui/theme/tokens.py  （唯一真源）
        │
        ├─ export_figma_tokens.py ──▶ docs/design/figma/tokens.json   （Tokens Studio schema）
        └─ export_css_tokens.py   ──▶ docs/design/html/tokens.css     （:root 作用域 → 设计板）
                                  ──▶ docs/design/html/tokens.wxss    （page 作用域 → 小程序）
```

实测产出 **69 个变量 / 3 组目标**，命名一一对应（`color.bg.canvas` → `--color-bg-canvas`
→ Figma 的 `color/bg/canvas`）。

**关键实测发现**：`tokens.py` 里的阴影值写的就是 **CSS 语法**
（`"0 2px 8px rgba(45, 55, 72, 0.06)"`），所以能**零损耗**直出 CSS：

```css
--shadow-card: 0 2px 8px rgba(45, 55, 72, 0.06);
--shadow-focus: 0 0 0 3px rgba(38, 174, 137, 0.25);   /* 多层 + spread */
```

而这两行在 **QSS 里完全无处安放**——这正好是路线 C 的分水岭（见 §4）。

脚本要处理的三处语法差异：作用域（`:root` vs 小程序的 `page`）、字号单位
（Figma 侧无单位、CSS 侧要 `px`）、阴影对象还原成 `x y blur spread color`。

---

## 2. EyeRest 走路线 C 会发生什么变化

| 环节 | 现在（路线 A · Figma） | 路线 C |
|---|---|---|
| 设计源 | Figma Variables（云端） | `tokens.py` + HTML 设计板（本地） |
| 新增设计产物 | Figma 文件 `iGNUkPX…` | `docs/design/html/design-board.html` |
| 改主色 | 改 `tokens.py` → 重跑导出 → `use_figma` 批量写回 | 改 `tokens.py` → 重跑导出（**本地，3 秒**） |
| 看整体效果 | 开 Figma 网页 / 等截图 | 浏览器直接打开（**本地字体，真实渲染**） |
| 设计↔代码对账 | 无（Figma 只是"参考"） | HTML 截图 vs Qt 截图，像素 diff |
| 配额 | **20 次/月**（已耗尽） | **零** |
| C 盘风险 | 无 | 无 |
| 协作 / 评审 | 多人实时、评论、版本历史 | 靠 Git |
| 矢量资产编辑 | 内置 | 需外部工具（本项目资产本来就是 SVG 源文件） |

**两条最实质的变化**：

1. **配额消失，改设计变成"本地 3 秒"。** 现在改一次主色要：改 `tokens.py` → 跑导出 →
   用掉 Figma 调用额度 → 等云端生效。路线 C 里就是跑一个脚本，产出 CSS 变量。

2. **对账环节的工具链你已经有了。** 项目里已有真机渲染截图
   （`.build/render_ui.py`，用默认 windows 平台 + `WA_DontShowOnScreen`）和 PIL 像素采样
   （`y=530` 行扫描定位色卡那套方法）。路线 C 的"对账"= 把两份 PNG 做 diff，
   **不需要新造轮子**。

> 也因此，路线 C 的短板很清楚：**没有组件变体的形式化、没有多人协作、没有版本历史**。
> 它是"给独立开发者的一条零成本路径"，不是"团队设计系统"。

---

## 3. 跨项目适配矩阵

| 目标平台 | 适配度 | 关键理由 |
|---|---|---|
| **微信 / 支付宝小程序** | ★★★★★ | WXSS **原生支持 CSS 变量**（基础库 2.25.0+ 完整支持 `var()`；Skyline 亦支持）。只需把 `:root` 换成 `page`，再逐文件 `@import`。设计板 ≈ 实现 |
| **Web（React / Vue / 静态站）** | ★★★★★ | CSS 变量直接复用，设计板可**演进成实现**，不存在翻译环节 |
| **Electron / Tauri 桌面** | ★★★★★ | 同样是 Web 技术栈 |
| **Qt Quick (QML)** | ★★★★ | QML 属性体系比 QSS 更接近 CSS（有 shadow、动画、状态机），仍需一次翻译但损耗小 |
| **Qt Widgets（EyeRest 现状）** | ★★★ | 设计板能画出效果，**QSS 接不住**：无变量、无 `box-shadow`、无 `transition`、无 `line-height` |
| **移动原生（iOS / Android）** | ★★ | 平台设计语言与 Web 差异大（SF / Material），需专门的 token 分发管线 |
| **WinForms / WPF 等** | ★★ | 与 Qt Widgets 同类问题：声明式样式能力弱于 CSS |

**读法**：这张表的横轴其实是「**目标平台在多大程度上说 CSS 这门语言**」。
- 说的是（Web / 小程序 / Electron）→ **HTML 设计板就是实现语法的草稿，零损耗**。
- 不说（Qt Widgets / 原生控件）→ 设计板会**放大**"设计能画、实现做不到"的落差。

小程序还有一个额外好处：**它的 CSS 能力显著强于 Qt Widgets**——
`box-shadow`（不支持多层叠加）、`backdrop-filter`、`transition`、`transform`、
`line-height` 全都支持。也就是说，同一份 HTML 设计板投到小程序，
**能落地的比例远高于投到 Qt Widgets**。

---

## 4. 对比 Figma：差距到底在哪

### 4.1 最终视觉产出：没有差距

这是最反直觉的一条结论。项目已有量化评估（`UI_ROUTE_EVALUATION.md` §0）：

> 差距里约 **25% 是引擎不够用**、约 **60% 是资产质量**、约 **15% 是活数据 vs 静帧**。

配合三条路线的天花板（同文件 §3）：Qt Widgets **~80%** / Qt Quick **~92%** / Web **~95%**。

**换设计工具，一分钱都吃不到。** 因为 60% 的差距来自插画资产的质量代差
（扁平色块 vs 有柔光体积的角色插画），40% 来自"那张参考图本身是 AI 生成的 moodboard、
不是单屏规格书"。这两项**与设计工具无关**。

所以：**在 EyeRest 这个项目上，Figma 和 HTML 设计板做出来的东西，最终看起来是一样的。**

### 4.2 过程维度：各有胜负

| 维度 | Figma | HTML 设计板 | 胜 |
|---|---|---|---|
| 变量真源 | Variables + **多模式** + 别名 + 发布到库 | CSS 变量（无模式、无发布概念） | Figma |
| 组件结构化 | **Variants 属性轴** + 实例覆盖 | 手写 CSS class + `:hover` / `:active` | Figma |
| 布局能力 | Auto Layout | Flexbox / Grid（**更强更自由**） | HTML |
| 视觉表现上限 | 像素画布，无限 | 浏览器，≈无限 | 平 |
| **设计↔实现距离** | 永远隔一层翻译 | Web/小程序**零**；Qt 一层 | **HTML** |
| **活数据** | 静态（除非写插件） | 可直接注入真实数据渲染 | **HTML** |
| **可执行性** | 静态图 | 能量 DPI、测长文本、测空数据 | **HTML** |
| 协作 / 评审 | 多人实时、评论、版本历史 | 无（Git 兜底） | Figma |
| 矢量资产编辑 | 内置 | 需外部工具 | Figma |
| 成本 | 20 次/月（已耗尽）+ 学 Auto Layout | **零** | **HTML** |

### 4.3 一个容易被忽略的点：谁更"诚实"

Figma 的像素画布**不告诉你任何东西做不到**——你可以画出 QSS 永远实现不了的柔光，
而那个落差要到几个月后做 UI 时才暴露。

HTML 设计板同样能画出那些效果（浏览器也支持 `box-shadow` / `backdrop-filter`），
**但因为它和"能否落地"共用同一套语言，落差会被就地看见**。
`design-board.html` 里那张「可实现性对照表」就是把这件事显式化：
每一项都在页面上真实生效，同时标明它在 QSS 侧的下场。

---

## 5. 结论与建议

1. **对 EyeRest**：路线 C 与 Figma 在**最终视觉产出上没有差距**（上限由引擎与资产决定）。
   Figma 多给的是"变量/组件的结构化治理"，代价是 20 次/月配额 + 生态烂账 + 桌面版门槛。
2. **对后续项目**：如果要做**小程序 / Web / Electron**，路线 C 是**首选**——
   CSS 变量直通，设计板可演进为实现的草稿，零成本零配额。
   如果要走**原生控件**（Qt Widgets / WPF / 原生 App），路线 C 仍可用，
   但必须像本文件一样先把"目标平台接不住的能力"列清楚。
3. **真正的决策依据**不是"哪个效果更好"，而是：
   **你更需要「结构化设计治理 + 协作」（选 Figma），还是「设计即实现 + 零成本 + 零约束」（选路线 C）。**
   对单人开发的 EyeRest，后者更划算。

---

## 6. 已落地 / 待办

**2026-09-13：用户拍板走路线 C，设计板已从"样板"展开为"完整板"。**

- ✅ `tools/export_css_tokens.py` —— 69 变量 → CSS（`:root`）+ WXSS（`page`）
- ✅ `docs/design/html/tokens.css` / `tokens.wxss` / `tokens-report.md`
- ✅ `tools/build_design_board.py` —— 从 `board.src.html` 生成**自包含**设计板
      （注入 token + base64 内嵌品牌字体；产物 2.0 MB，零外部依赖，双击即开）
- ✅ `docs/design/html/design-board.html` —— **完整板**：9 区块全覆盖
      （首页 6 块 / 设置页 3 组 / PositionEditor / VisualCue 三种）
      + 状态画廊 6 类非正常态 + 12 组件全状态 + 可实现性对照 + 不一致清单
- ✅ `docs/design/html/README.md` —— 生成管线、看板方式、对账方法、5 条踩坑
- ✅ 验收图归档 `docs/design/acceptance/board/`（headless Chrome 出图，5 张）
- ⬜ **对账脚本**：HTML 截图 vs Qt 截图自动像素 diff（目前仍是手工两步）
- ⬜ 把 §4 的 4 条「待裁定 / 产品决定」项收口（需用户拍板）
