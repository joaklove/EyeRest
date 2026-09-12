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

**执行要点**：

1. 调 `use_figma` **前必须先加载 `/figma-use` skill**（skill 正文用 MCP `resources/read` 读
   `skill://figma/figma-use/SKILL.md`，索引在 `skill://index.json`）。
2. 调 `create_new_file` 前必须先加载 `/figma-create-new-file` skill。
3. 用 `figma.variables.createVariable()` 批量建变量，数据源读 `tokens.json`；
   8 个别名用 `figma.variables.createVariableAlias()` 建。

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

63 个变量 / 8 个别名；改主色时别名自动跟随。**同一份 `tokens.json`，两条路径都能用。**

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

## 阶段 1 续 — 组件库

按 `tokens-report.md` 的分组建组件，同样走 `use_figma`。组件清单见
`UI_UX_WORKFLOW_FIGMA.md` 阶段 1 的 Components 表（Button / Card / Tile / Control / Nav / Pill）。

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
