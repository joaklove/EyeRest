# 组件库与 9 区块规格（设计契约 v1）

- 日期：2026-09-12
- 定位：**Figma 与代码的共同输入。** 不依赖 Figma 配额——无论最终走 Figma、HTML 设计板还是直接改代码，都以本文件为规格。
- 来源标注：
  - `[码]` = 从现有实现读出的事实（附 `文件:行号`），可直接信任
  - `[定]` = 本轮按 token 体系裁定的目标值（需要一次代码改动才能对齐）
  - `[待]` = 尚未核实，**不要当事实用**

---

## 0. 速览

| 项 | 数量 | 状态 |
|---|---|---|
| 组件 | 12 | 1 个已落 Figma（`Button/Primary`），其余规格已定 |
| 区块 | 9 | IA 已定，frame 待落 |
| 实际需要的状态帧 | **约 39**（不是 9×8=72） | 见 §3 的「不适用」判定 |
| 代码 ↔ 设计源不一致 | 5 条 | 1 条已修，4 条待裁定 |

**为什么会有这份文件**：Figma Starter 席位每月仅 20 次 MCP 调用（§6），不足以把 12 个组件 + 9 个区块**逐次交互式**建完。
所以执行口径改为「规格先定死，再脚本化批量落地」——本文件就是那份规格。

---

## 1. 组件库（12 个，按依赖顺序）

依赖顺序：**atoms（Button / Pill / Tile / Control）→ molecules（Card / Nav）**。先建 atoms，因为 molecules 的实例要用它们。

### 1.1 规格总表

| # | 组件 | 变体轴 | 几何 | 绑定 token | 文字样式 | 代码锚点 |
|---|---|---|---|---|---|---|
| 1 | `Button/Primary` | State=Default/Hover/Pressed/Disabled | r=6, pad 10×26, 高 41 | fill `color/brand/{primary,hover,pressed}`＋Disabled `color/bg/soft`；text `color/text/inverted`／`color/text/disabled` | Body(14) **Bold** | `position_editor.py:283` `[码]` |
| 2 | `Button/Secondary` | State=Default/Hover/Pressed | r=8, pad 8×26, fontSize 13 | fill `bg/soft`，border `border/default`，text `text/primary` | Secondary(13) | `settings.py:318` `[码]` |
| 3 | `Button/Ghost` | State=Default/Hover | r=6, pad 10×22 | fill `bg/soft`＋border `border/default`，hover `border/focus` | Body(14) | `position_editor.py:288` `[码]`（原为透明灰） |
| 4 | `Pill/Tag` | State=Default/Selected | r=999, pad 3×12 | Default `bg/soft`+`text/secondary`；Selected `brand/soft`+`brand/text` | Caption(12) | `dashboard.py:311,780` `[码]` |
| 5 | `Nav/SidebarItem` | State=Default/Hover/Active | r=10, pad 10×14, 外边距 2×12, 高 ≥40, icon 18 | Default 透明；Hover `bg/sidebar-hover`；Active `bg/sidebar-active`+`text/brand` | Secondary(13) **Medium** | `main_window.py:60-95` `[码]` |
| 6 | `Tile/Quick` | State=Default/Hover | **46×46 正圆**（r=23）, icon 居中 | fill `brand/soft`；hover `bg/raised`+阴影 | 无（图标） | `dashboard.py:141-144` `[码]` |
| 7 | `Card/Base` | State=Default/Raised | r=20, pad 20 | fill `bg/surface`；border `border/soft`；阴影 `Shadow/Card` | — | `dashboard.py:287,643` `[码]` |
| 8 | `Card/Rhythm` | Rhythm=Blink/Look/Move/Deep × State=On/Off | r=14, pad 16 | 左侧色条/图标 = `color/rhythm/{blink,look,move,deep}`；Off 态色值降为 `text/disabled` | H3(16) 标题 + Secondary(13) 副文 | `settings.py:231-256` `[码]`（2×2 布局） |
| 9 | `Card/Stat` | State=有值/空值 | 5 列等宽, gap 10 | 数值色 = 对应节奏色；空值 `--` 用 `text/muted` | 数值 Body(14) / 标签 Caption(12) | `dashboard.py:591-609` `[码]` |
| 10 | `Control/Switch` | State=On/Off/Disabled | 轨 44×24 r=999, 钮 18 | On 轨 `brand/primary`；Off 轨 `brand/soft`；Disabled 轨 `bg/soft` | — | `[待]` 组件在 `theme/components.py`，几何未核实 |
| 11 | `Control/Slider` | Part=填充段/Track/Knob | 轨高 6 r=3, 钮 14 | 填充 `brand/primary`；轨 `bg/soft` | — | `[待]` 同上 |
| 12 | `Input/Number` | State=Default/Focus/Error | r=6, pad 8×12, **无 +/- 按钮** | fill `bg/soft`；border `border/default`→`border/focus` | Body(14) 纯文本 | `settings.py:306` `[码]`（`NoButtons` 口径见 `V1_REFACTOR_SPEC`） |

> ⚠️ `Button/Primary`(r=6) 与 `Button/Secondary`(r=8) **圆角不一致**，是历史遗留：设置页的按钮与位置编辑弹窗的按钮是两套手写 QSS。
> 这是 §4 的第 4 条不一致项，需要一次性统一（推荐统一到 `radius/sm = 6`）。

### 1.2 已落地（Figma）

| 项 | 结果 |
|---|---|
| 页面 | `01 Foundations` / `02 Components` / `03 Blocks`（Starter 限 3 页，已用满） |
| 文字样式 | 7 个：`Type/Display`(ZCOOL KuaiLe 32) / `H1`22 / `H2`18 / `H3`16 / `Body`14 / `Secondary`13 / `Caption`12 |
| 组件 | `Button/Primary` 变体集，节点 `5:28`，4 个变体，属性轴 `State` |
| 品牌字体 | **ZCOOL KuaiLe（站酷快乐体）在 Figma 侧可用**，与应用同款，无需替代字体 |

**已验证**：主按钮四态底色像素采样 = `#26AE89`(primary) / `#1FA37E`(hover) / `#17906E`(pressed) / `#F5EFE7`(soft)，文字 `#FFFFFF` / `#CBD5E0`。

---

## 2. 九区块信息架构

**原则：每个数值都必须能追到一个真实数据源。** 追不到的就是装饰，应当删掉——这是"非 BI 面板"的判定标准。

| # | 区块 | 结构 | 真实数据源 | 依赖组件 |
|---|---|---|---|---|
| 1 | Sidebar | Logo 徽章(36×36 圆) + 标题 + 4 个导航项 + 底部版本号 | `APP_VERSION`；导航项 = 首页/设置/统计/关于 | `Nav/SidebarItem` |
| 2 | Hero | 问候语(品牌字体) + 副文案 + 植物背景 + 小动物插画 + **右上四行节奏总览** | 四层开关与周期：`BLINK_ENABLED`/`BLINK_CYCLE_SECONDS`、`LOOK_AWAY_ENABLED`/`short_work_duration`、`MOVE_ENABLED`/`MOVE_INTERVAL`、`DEEP_BREAK_ENABLED`/`DEEP_BREAK_INTERVAL` | `Pill/Tag` |
| 3 | 当前状态卡 | 标题 + 下一次提醒倒计时 + 陪伴文案 + 角色插画 | 各引擎剩余秒数（`_refresh_countdown`） | `Card/Base` |
| 4 | 快捷设置 | 4 个圆形图标入口 + 标题 + **当前值** | 当前 skin / intensity / 位置模式 / 音效方案 | `Tile/Quick` |
| 5 | 今日数据 | **5 项**：专注时长 / 眨眼 / 远眺 / 活动 / 长休 | `get_today_summary()` → `active_seconds` / `blink_cues` / `break_count`(=远眺+长休) / `move_count` / `session_count` | `Card/Stat` |
| 6 | 引言卡 | 文案 + 植物插画(44×44) | 静态文案（i18n） | `Card/Base` |
| 7 | Settings 2×2 | 四节奏卡片（周期/时长，**纯文本输入无 +/- 箭头**） | 同上四组配置键 | `Card/Rhythm` + `Control/*` + `Input/Number` |
| 8 | PositionEditor | 遮罩暗化 + 前景可拖预览 + 取消/保存 + 提示条 | `CUE_POSITION` / `CUE_POS_X,Y` / `CUE_POS_MONITOR`；6 种预设 | `Button/Primary` + `Button/Ghost` |
| 9 | VisualCue | 暖白底卡片 + 眼睛/植物元素 + 一行文字 | 触发层与强度：四类提示 × `CUE_SKIN`(3) × `CUE_INTENSITY`(3) | 独立（400×120） |

> **区块 5 的口径注意**：`break_count` 在代码里是 `look_away + deep`，而区块同时**分列**显示远眺与长休。
> 也就是说「休息次数」这个合计值没有独立数据源。**要么删掉合计，要么让 `get_today_summary` 分别返回 `look_away_count` / `deep_break_count`。**
> 建议后者（数据更诚实），已列入 §4。

---

## 3. 区块 × 状态矩阵

§10 的多状态矩阵是**页面级**的。落到区块级时，**不是每块都需要全部状态**——盲目铺 9×8=72 帧是纯浪费。

**通用判定**：`Loading` / `Error` 两个状态在本项目**整体不适用**——EyeRest 是纯本地应用，没有网络请求，也没有"加载中"的往返。
真正存在的"非正常态"是这六类：**暂停 / 空数据 / 长文本 / 英文 / 125% DPI / 层级关闭**。

| # | 区块 | 需要的状态帧 | 不适用（附理由） |
|---|---|---|---|
| 1 | Sidebar | 正常、激活项切换(×4)、暂停、英文、125% | 空数据/长文本（导航文案固定且短） |
| 2 | Hero | 正常、暂停、**某层关闭**（该行显示"已关闭"）、英文、125% | 空数据（问候语恒有值） |
| 3 | 当前状态卡 | 正常(倒计时)、暂停、空数据(`--`)、长文本、英文、125% | 英文长度是主要风险点（副文案 1.6× 膨胀） |
| 4 | 快捷设置 | 正常、未修改、已修改、英文 | 空数据（四值有默认） |
| 5 | 今日数据 | 正常、空数据(`--`)、首次启动(session=0)、英文、125% | — |
| 6 | 引言卡 | 正常、长文本、英文 | 其余全不适用（纯静态，状态最少的一块） |
| 7 | Settings 2×2 | 正常、某层关闭(卡片灰化)、已修改、保存成功、**越界输入(校验)**、英文、125% | 空数据（表单有默认值） |
| 8 | PositionEditor | 六种预设位置、custom、拖动中、英文 | 空数据/长文本 |
| 9 | VisualCue | 四类提示 × 三 Skin × 三强度（**抽代表帧，不铺满 36 帧**）、英文、长文本 | — |

**合计**:约 **39** 帧代表状态（对比盲铺 72 帧，省 **46%**）。

---

## 4. 代码 ↔ 设计源不一致项

按严重度排序。**这些是"上 Figma"真正的产出**——把散落的硬编码暴露成可裁决的清单。

| # | 项 | 现状 | 建议 | 状态 |
|---|---|---|---|---|
| 1 | PositionEditor 主按钮是**硬编码蓝** `#1976d2`/`#1565c0` | 与主色绿完全无关，是 V0.6 之前遗留 | 接 `PRIMARY`/`PRIMARY_HOVER`/`PRIMARY_PRESSED` | ✅ **本轮已修**（`position_editor.py:283-299`），像素验证 `#26AE89` + `#FFFFFF` |
| 2 | PositionEditor 次按钮是半透明灰 `rgba(120,124,134,220)`（反白字） | 同上 | 改为 `bg/soft` + `border/default` + `text/primary` | ✅ **本轮已修**，像素验证 `#F5EFE7` + `#EAE0D2` |
| 3 | 休息次数无独立数据源 | `break_count = look_away + deep`，但区块 5 又分列远眺与长休 | `get_today_summary` 增加 `look_away_count` / `deep_break_count` | ⏳ 待裁定 |
| 4 | 两套按钮圆角（r=6 vs r=8） | `/ui/position_editor.py` r=6、`/ui/settings.py` r=8 | 统一到 `radius/sm = 6`，两处共用 `Button/Primary` 样式 | ⏳ 待裁定 |
| 5 | 按钮内边距 `10×26` 不是 token 值 | 间距体系是 4/8/12/16/20/24/32/40，没有 26 | 对齐为 `space/3 × space/6`（12×24） | ⏳ 待裁定（改的是视觉，需你点头） |
| 6 | PositionEditor 遮罩层调色板未 token 化 | 提示条 `#eaf4ff` / `rgba(20,24,34,210)`、圆角 10 | 新增 `overlay/*` token，或**明确豁免**（暗色遮罩本就该有独立调色板） | ⏳ 待裁定 |

> 第 5、6 条**不建议默默改**——它们会改变实际观感，属于产品决定不是工程决定。

---

## 5. Figma 落地方式（配额受限后的执行口径）

Starter 席位每月 20 次调用，**不能再用"建一个看一眼再调"的细粒度节奏**。改为：

1. 本文件把规格定死（已完成）
2. 组件与区块写成**少数几个大脚本**，一次调用建一整批
3. 用 `tools/figma_mcp_call.py` 直连执行（不占用 WorkBuddy 客户端的调用路径）
4. 每批之后**只用 1 次** `get_screenshot` 做整体验收，不做逐节点确认

目标：把 12 组件 + 9 区块压到 **≤ 12 次调用**内完成。

```bash
# 直连执行（令牌来自 .build/figma_oauth/token.json）
./.venv/Scripts/python.exe tools/figma_mcp_call.py use_figma \
    iGNUkPX3P0HkYG5qX3QGHL .build/figma_scripts/build_cards.js "建 Card/* 组件"
```

---

## 6. 阻塞事实：Figma Starter 席位配额

**结论：路线 A 的"免费计划足够"这个判断是错的，已更正。**

来源：官方文档 `file://figma/docs/rate-limits-access.md`（MCP resource，可直接读）。

| 席位 | Starter | Professional | Org | Enterprise |
|---|---|---|---|---|
| View / Collab | **20 次/月** | 6 次/月 | 6 次/月 | 6 次/月 |
| Dev / Full | — | 200 次/天, 10/分 | 600 次/天, 20/分 | 600 次/天, 20/分 |

- 我们当前 = **Starter + View** → **20 次/月**。
- 限额只对"从 Figma 读数据"的工具生效；豁免名单只有 `add_code_connect_map` / `create_new_file` / `whoami`。
- **实测确认（2026-09-12）**：`use_figma` 与 `get_metadata` 均已返回
  `You've reached the Figma MCP tool call limit on the Starter plan`。
  直连 `whoami` 仍成功——但那是**豁免工具**，不代表配额恢复。
- 学籍（Education）计划等同 Professional 的 Dev/Full：**200 次/天**，若符合资格可免费拿到。

**影响**：20 次/月 连一次完整的设计系统建设都跑不完（本轮光是排查与建一个组件就用了 5 次）。
**因此**：本轮的落地改为"规格先行 + 脚本化批量"，把调用预算花在刀刃上。
