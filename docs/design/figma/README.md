# Figma 设计源（路线 A · Figma 轻路线）

本节对应 `G:\Open Code\docs\UI_UX_WORKFLOW_FIGMA.md` 的方案。

## 目录内容

| 文件 | 说明 |
|---|---|
| `tokens.json` | 由 `tools/export_figma_tokens.py` 导出，Tokens Studio schema（也是 `use_figma` 的数据源） |
| `tokens-report.md` | 本次导出的变量清单、分组统计、别名映射、命名映射 |
| `README.md` | 本文件，操作步骤 |

**`tokens.json` 是产物，不要手改。** 改 `app/ui/theme/tokens.py` 后重跑：

```bash
cd "G:/Open Code/apps/EyeRest"
./.venv/Scripts/python.exe tools/export_figma_tokens.py
```

**Figma 设计文件**：`file_key = iGNUkPX3P0HkYG5qX3QGHL`
→ <https://www.figma.com/design/iGNUkPX3P0HkYG5qX3QGHL>

---

## 阶段 0 — 接通 remote MCP ✅ 已完成（2026-09-12）

常规 OAuth 走不通：**Figma 的 DCR 端点是 `client_name` 白名单**，非白名单名称返回 403，
而 403 会让 MCP 客户端**静默挂起**（不报错、不打开浏览器）——就是「点信任后一直转圈」。
完整证据见 `docs/UI_UX_WORKFLOW_FIGMA.md` §3.1。

**采用的方案**：外部授权 + 静态头注入。

```bash
# 首次授权（需在已登录 Figma 的浏览器点一次「允许」）
./.venv/Scripts/python.exe tools/figma_mcp_auth.py login

# 查看令牌剩余有效期
./.venv/Scripts/python.exe tools/figma_mcp_auth.py status

# 90 天到期前续期
./.venv/Scripts/python.exe tools/figma_mcp_auth.py refresh
```

`login` / `refresh` 会自动把令牌注入 `~/.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp",
      "headers": { "Authorization": "Bearer <access_token>" },
      "timeout": 60000
    }
  }
}
```

令牌落在 `.build/figma_oauth/token.json`（`.build/` 已被 gitignore，**不入库**）。
**有效期 90 天**（`expires_in = 7776000`）。

> ⚠️ 改完 `mcp.json` **不会热生效，需重启 WorkBuddy**。
>
> ⚠️ 若重启后客户端仍坚持走 OAuth 而不用静态头，退路是**直接 curl 调 MCP 端点**——本轮全程已验证可行：
> `POST https://mcp.figma.com/mcp`，头 `Content-Type: application/json` + `Accept: application/json, text/event-stream` + `Authorization: Bearer <token>`，
> 体 `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}`。
> Figma 服务端**无状态、不返回 `mcp-session-id`**，每次调用不必带 session。
>
> ⚠️ 这是非官方 workaround（借用了白名单里的客户端名），Figma 随时可能收紧。

**实测能力**：41 个工具；`whoami` = 席位 `View` / 层级 `starter`（**免费计划足够**）；
`create_new_file` 与写设计源**均验证可用**。

---

## 阶段 1 — 把 tokens 写进 Figma 变量

> ✅ **2026-09-12 更新：改用 `use_figma` 编程写入，放弃 Tokens Studio 手工 8 步。**
>
> 原因：实测发现 remote MCP **有写能力**——`use_figma` 直接执行 Figma Plugin API 代码，
> 可以批量创建 Variables / 组件 / frame。原先"远程版不能写、只能走 Tokens Studio 手工导入"
> 的判断是错的（见 `UI_UX_WORKFLOW_FIGMA.md` §3.2）。

**执行要点**（已按此执行）：

1. 调 `use_figma` **前必须先加载 `/figma-use` skill**（skill 正文用 MCP `resources/read` 读
   `skill://figma/figma-use/SKILL.md`，索引在 `skill://index.json`）。
2. 调 `create_new_file` 前必须先加载 `/figma-create-new-file` skill。
3. 变量用 `figma.variables.createVariable(name, collection, 'COLOR'|'FLOAT')` 建。
   **别名没有单独方法**——是 `variable.setValueForMode(modeId, { type: 'VARIABLE_ALIAS', id: 目标变量.id })`。
4. **每个变量都要显式设 `scopes`**：默认 `ALL_SCOPES` 会污染所有属性选择器。
   本项目的约定是底色 → `FRAME_FILL`+`SHAPE_FILL`、文字 → `TEXT_FILL`、
   描边 → `STROKE_COLOR`、间距/内边距 → `GAP`、圆角 → `CORNER_RADIUS`、
   窗口尺寸 → `WIDTH_HEIGHT`、字号 → `FONT_SIZE`。
5. **阴影做不成变量**，只能用 effect style —— 所以 63 个 token 落到 Figma 是
   **60 个变量 + 3 个 effect style**。
6. 每次 `use_figma` 最多切一次页；纯写变量时不需要切页（默认在第一页）。

### ✅ 阶段 1a 已落地（2026-09-12）

| 项 | 结果 |
|---|---|
| 集合 | `EyeRest Tokens`，模式 `Light`（免费计划仅 1 个模式，够用） |
| 变量 | **60 个** — color 35 / space 8 / radius 5 / size 5 / font-size 7 |
| 别名 | **8 个**，回读校验全部指向正确 |
| 效果样式 | `Shadow/Card`（0,2 blur8）、`Shadow/Raised`（0,4 blur16）、`Shadow/Focus`（0,0 blur0 spread3） |

**页面 `01 Foundations`** 上的三块展示板（色块/间距条全部**绑定变量**，不是硬编码值）：

| Frame | 节点 ID | 内容 |
|---|---|---|
| `Foundations / Color Core` | `3:2` | Background / Brand / Text / Border，21 个色卡 |
| `Foundations / Color Semantic` | `3:105` | Info / Accent / Status / Rhythm，14 个色卡 |
| `Foundations / Scale` | `3:176` | spacing / radius / font-size / window size |

色卡副标题会自动显示 `= brand/primary` 这类别名指向——**这是别名生效的可视证据**，
也是"改主色只需改一处"的验证点。

> ⚠️ `use_figma` 的两个高频坑（本轮都踩到了）：
> 1. **`layoutSizingHorizontal = 'FILL'` 必须在 `parent.appendChild(child)` 之后设**，
>    否则报 `FILL can only be set on children of auto-layout frames`。
> 2. **WRAP 要生效，容器必须固定主轴尺寸**：先 `resize(W, h)` → 设 `counterAxisSizingMode='FIXED'`
>    → 最后设 `primaryAxisSizingMode='AUTO'`（`resize()` 会重置 sizing mode，顺序不能反）。

<details>
<summary>备选路径（Tokens Studio 手工导入，已不推荐）</summary>

| 步 | 操作 |
|---|---|
| 1 | Figma 里新建 Design 文件 |
| 2 | 右键 → Plugins → **Tokens Studio for Figma** |
| 3 | 插件里选 **New empty file** |
| 4 | 左侧 **Settings → Storage Type → File** |
| 5 | 点 **Load from File**，选本目录的 `tokens.json` |
| 6 | 左侧应出现六组、共 **63 个 token** |
| 7 | **Styles & Variables** → **Export styles & variables to Figma** |
| 8 | 若提示已存在同名变量，勾 **Update existing** |

</details>

**60 个变量 + 3 个效果样式 / 8 个别名**；改主色时别名自动跟随。**同一份 `tokens.json`，两条路径都能用。**

### 为什么用别名

8 个别名对应 `tokens.py` 里的语义等价关系，例如：

```
color/status/success  →  color/brand/primary
color/rhythm/blink    →  color/brand/primary
color/border/focus    →  color/brand/primary
color/rhythm/look     →  color/info/base
color/rhythm/move     →  color/accent/deep
```

这样"主色只有一个真源"这件事在设计侧也成立——不会出现"改了主色但成功态还是旧绿"。

> 导出报告里另有两处**值相同但未设别名**（`#FFFFFF` 的 `bg/surface` vs `text/inverted`；`20px` 的 `space/5` vs `radius/xl`）。这是**刻意**的：
> - `text/inverted` 与 `bg/surface` 现在都是白，但语义不同（反白文字 vs 卡片面），将来完全可能分道扬镳
> - `space/5` 与 `radius/xl` 都是 20px 纯属数值巧合，毫无语义关系
>
> 强行合并会制造假耦合。

---

## 阶段 1 续 — 组件库 🔶 进行中（2026-09-12）

### 已落地

| 项 | 结果 |
|---|---|
| 页面 | `01 Foundations` / `02 Components`(5:11) / `03 Blocks`(5:12) |
| 文字样式 | 7 个 `Type/*`：Display(ZCOOL KuaiLe 32) / H1 22 / H2 18 / H3 16 / Body 14 / Secondary 13 / Caption 12 |
| 组件 | `Button/Primary` 变体集 `5:28`（4 变体，属性轴 `State`） |
| 品牌字体 | **ZCOOL KuaiLe 在 Figma 侧可用**，与应用同款，不需要替代字体 |

### ⛔ 阻塞：Starter 席位每月仅 20 次 MCP 调用

**这推翻了上一轮"免费计划足够"的结论，必须更正。**

官方口径（`file://figma/docs/rate-limits-access.md`）：**Starter + View 席位 = 20 次/月**；
豁免工具只有 `add_code_connect_map` / `create_new_file` / `whoami`。

实测（2026-09-12）：`use_figma`、`get_metadata` 均已返回
`You've reached the Figma MCP tool call limit on the Starter plan`。
直连 `whoami` 仍成功，但它是**豁免工具**，不能据此认为配额还有余量。

> 20 次/月不足以逐次交互式建完 12 组件 + 9 区块（本轮仅"排查 + 建 1 个组件"就用掉 5 次）。
> 出路：升级 Professional + Dev/Full 席位（200 次/天）／学籍计划（同 200 次/天）／等下月重置／改走路线 C。

### 执行口径调整：规格先行 + 脚本化批量

组件与区块规格已定死在 **`docs/design/SPEC_COMPONENTS_AND_BLOCKS.md`**（12 组件 + 9 区块 + 状态矩阵 + 不一致清单）。
落地时把整批写成一个脚本、一次调用建完，避免细粒度往返。

### 直连工具（绕过客户端，也便于脚本化）

```bash
./.venv/Scripts/python.exe tools/figma_mcp_call.py list                  # 列出全部工具
./.venv/Scripts/python.exe tools/figma_mcp_call.py whoami                # 豁免，不计数
./.venv/Scripts/python.exe tools/figma_mcp_call.py call get_metadata '{"fileKey":"...","nodeId":"5:28"}'
./.venv/Scripts/python.exe tools/figma_mcp_call.py use_figma <fileKey> <脚本.js> "描述"
```

---

## 阶段 1 续 — 落地时必踩的坑（本轮新增 3 条）

> ⚠️ 上面的「`layoutSizingHorizontal` 顺序」与「WRAP 需固定主轴尺寸」两条仍然有效，以下是新增：

7. **Starter 计划只给 3 个页面。** 第 4 个 `figma.createPage()` 直接抛
   `The Starter plan only comes with 3 pages`。所以完整界面 frame 只能并入 `03 Blocks`，不要另开 `04 Screens`。

8. **`use_figma` 是整脚本事务性的。** 一次调用里前面已成功建好的东西，只要脚本后段抛错，
   **全部回滚**（实测：建 7 个文字样式成功 → 建页面抛错 → 回查文字样式为空）。所以宁可拆小，不要押注"应该能跑完"。

9. **`setBoundVariableForPaint` 会保留传入 paint 的字面色，且渲染优先用字面色。**
   传 `{type:'SOLID', color:{r:0.5,g:0.5,b:0.5}}` 再绑到白色变量 → 节点上**绑定是对的**，
   但**渲染出来是 `#808080` 灰**。而自动生成的绑定 paint（无 `color` 字段、只有 `boundVariables`）渲染正常。
   **正确写法：绑定时不要给字面色。**

   ```js
   // ✗ 渲染成灰
   node.fills = [figma.variables.setBoundVariableForPaint({ type: 'SOLID', color: { r: .5, g: .5, b: .5 } }, 'color', V)];
   // ✓ 只留绑定
   node.fills = [{ type: 'SOLID', boundVariables: { color: { type: 'VARIABLE_ALIAS', id: V.id } } }];
   ```

---

## 与代码侧的对应关系

```
app/ui/theme/tokens.py  ──导出──▶  docs/design/figma/tokens.json
                                          │
                                          │ use_figma（Plugin API，批量建变量）
                                          ▼
                                    Figma Variables
                                          │
                                          │ get_variable_defs（remote MCP）
                                          ▼
                                    AI 读取精确值 → 写回 tokens.py / QSS
```

**同步纪律：只同步 Variables，不同步布局。** Auto Layout 与 Qt 布局模型不是一一对应，
强行同步布局会制造无意义的维护量。

---

## MCP 能力速查（2026-09-12 实测，共 41 个工具）

| 用途 | 工具 |
|---|---|
| 读设计 → 代码 | `get_design_context` / `get_screenshot` / `get_metadata` / `get_variable_defs` |
| 写设计源 | `use_figma` / `create_new_file` / `upload_assets` |
| 设计系统 | `search_design_system` / `get_libraries` |
| Code Connect | `get_code_connect_map` / `add_code_connect_map` / `get_code_connect_suggestions` / `send_code_connect_mappings` / `get_context_for_code_connect` / `list_file_components_for_code_connect` |
| 其他 | `whoami` / `generate_diagram` / `get_figjam` / `get_motion_context` / `download_assets` / `export_video` |

⚠️ **`generate_figma_design`（把运行中的界面捕获回画布）不适用于本项目**——
它按 **URL** 抓取浏览器渲染结果，原生 PySide6 窗口不满足这个前提。
注意区分：**「写入设计源」通，「捕获原生窗口」断。**
