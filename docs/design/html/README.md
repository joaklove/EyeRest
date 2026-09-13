# HTML 设计板（路线 C 的设计源）

- 日期：2026-09-13
- 定位：**EyeRest 现行的设计源。** 取代 Figma 侧（后者受 Starter 席位 20 次/月 MCP 配额限制，
  见工作区 `docs/UI_UX_WORKFLOW_FIGMA.md` §3.3–3.4）。
- 上游契约：`docs/design/SPEC_COMPONENTS_AND_BLOCKS.md`（12 组件 + 9 区块 + 状态矩阵）

---

## 1. 文件清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `board.src.html` | **手写源** | 设计板的结构与样式。**要改设计板就改这里。** |
| `design-board.html` | **产物** | 自包含单文件（约 2 MB，内含 69 token + base64 品牌字体）。双击即可打开。 |
| `tokens.css` | **产物** | 69 个 CSS 变量，`:root` 作用域 |
| `tokens.wxss` | **产物** | 同 69 个变量，`page` 作用域（微信小程序） |
| `tokens-report.md` | **产物** | 分组统计、别名映射、三目标可实现性对照 |

产物一律 **禁止手改**——改了会在下次生成时被覆盖。

---

## 2. 生成管线（唯一真源 = `app/ui/theme/tokens.py`）

```bash
cd "G:/Open Code/apps/EyeRest"

# 1) 导出 CSS / WXSS 变量
.venv/Scripts/python.exe tools/export_css_tokens.py

# 2) 生成自包含设计板（注入 token + 内嵌品牌字体）
.venv/Scripts/python.exe tools/build_design_board.py
```

```
app/ui/theme/tokens.py （唯一真源）
   ├─▶ export_figma_tokens.py ─▶ docs/design/figma/tokens.json   （Tokens Studio）
   ├─▶ export_css_tokens.py   ─▶ docs/design/html/tokens.css     （设计板）
   │                          ─▶ docs/design/html/tokens.wxss    （小程序）
   └─▶ build_design_board.py  ─▶ docs/design/html/design-board.html
```

三处命名同源：`color.bg.canvas` → `--color-bg-canvas`（CSS/WXSS）→ `color/bg/canvas`（Figma）。

**为什么要生成而不是手写 JS 拼**：设计板必须用**真的 token 值**，否则它就成了第二份真源，
迟早与 `tokens.py` 漂移。第 2 步同时做两件事——注入 token、把
`app/assets/fonts/ZCOOLKuaiLe-Regular.ttf` 以 base64 内嵌。
后者解决一个具体问题：**品牌字体只随应用分发、没有装进系统字体库**，
浏览器按 family 名找不到它；内嵌后双击即可正确渲染，不依赖任何外部文件。

---

## 3. 看一眼设计板

```bash
start "" "G:/Open Code/apps/EyeRest/docs/design/html/design-board.html"
```

或用 headless Chrome 出验收图（**注意 `--user-data-dir` 必须指向 G 盘**，
否则 Chrome 会往 `%LOCALAPPDATA%` 写 profile，撞"严禁写 C 盘"硬约束）：

```bash
export TEMP="G:/Open Code/apps/EyeRest/.build/tmp"; export TMP="$TEMP"
"/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
  --user-data-dir="G:/Open Code/apps/EyeRest/.build/chrome-profile" \
  --window-size=1120,7200 \
  --screenshot="G:/Open Code/apps/EyeRest/docs/design/acceptance/board/_raw.png" \
  "file:///G:/Open%20Code/apps/EyeRest/docs/design/html/design-board.html"
```

图中背景色是 `#EDEAE4`，底部会有一截空白（`--window-size` 取的是上限）——
按"逐行找最后一个与背景不同的行"裁掉即可。归档目录 `docs/design/acceptance/board/`。

---

## 4. 与 Qt 实现对账

设计板是**规格**，真机截图是**实况**，两者做像素 diff：

```bash
# 真机渲染（必须默认 windows 平台 + WA_DontShowOnScreen；offscreen 下 CJK 缺字形）
.venv/Scripts/python.exe .build/render_ui.py

# 再用 PIL 采样比对（项目惯用方法：定位 + 中值，不靠肉眼）
```

对账重点：**主色、块间距、圆角、字号层级、插画位置**。不追求整屏 1:1
（见 `docs/UI_ROUTE_EVALUATION.md`：整屏上限 Qt Widgets ~80%，
差距 60% 来自插画资产、25% 来自引擎，与设计工具无关）。

---

## 5. 设计板能做什么、不能做什么

**能做（且 Figma 做不到的）**

- **可执行**：能量 125% DPI、能测长文本换行、能测空数据、能直接注入真实数据渲染
- **零配额、零成本**：改主色 = 跑 3 秒脚本，不用等云端
- **设计↔实现零距离**（Web / 小程序目标）：同一套 CSS 语言，设计板可演进成实现草稿
- **诚实**：板上每项能力都注明它在 QSS 侧的下场，落差就地可见

**做不到**

- 组件变体的形式化（靠 CSS class + `:hover` / `:active` 手写）
- 多人实时协作与评论（靠 Git）
- 版本历史的图形化对比（靠 Git）
- 矢量资产的可视化编辑（本项目资产本来就是 SVG 源文件，用编辑器改即可）

一句话定位：**它是"给独立开发者的零成本路径"，不是"团队设计系统"。**

---

## 6. 踩坑记录

1. **品牌字体须 base64 内嵌**，不能只写 `font-family` —— 字体没进系统字体库，浏览器找不到。
2. **`--user-data-dir` 必须指到 G 盘**，否则 headless Chrome 写 C 盘 profile。
3. **`--window-size` 是视口上限**，不是自适应；截图底部会留白，需按背景色裁掉。
4. **数字输入框用 `type="text"`**，不要用 `type="number"` —— 后者会渲染出 spinner 箭头，
   与代码里的 `QAbstractSpinBox.NoButtons` 口径不符，会误导判读。
5. 设计板里的**产品 UI 只用 `var(--...)`**；设计板自身的排版（页头、图例、标注）才允许写字面值。
   两者混用会让"token 覆盖度"失真。
6. **半透明色必须写 0-1 小数 alpha，不能沿用 QSS 的 0-255 整数**。
   `rgba(10, 12, 18, 120)` 在 Qt 里合法，在浏览器里会被 clamp 成 1 → 遮罩变**纯黑**，
   设计板与实现静默不一致。实测 Qt 对 `120` / `0.47` / `47%` 分别解析出 0.469 / 0.465 / 0.469，
   故小数与百分比是两侧唯一的公共写法。对应 token：`--color-overlay-scrim` / `--color-overlay-tip-bg`。
7. **板的几何要与控件代码对账，别照"看起来合理"画**。此前本板把 PositionEditor 画成
   「浮层面板 + 标题 + 副文案」，真实实现只有一个居中提示条 —— 已删。改板前先读
   `position_editor.py` 的 `_build_ui()`。
