# EyeRest

低打扰用眼健康助手。

## 简介

EyeRest 是一款基于 20-20-20 规则的用眼健康助手，帮助用户在长时间使用电脑时
定时休息眼睛。它采用低打扰设计，支持短休息与长休息、自动空闲检测、全屏避让、
电源/会话感知，让休息提醒不打断当前工作节奏。

## 技术栈

- **Python 3.12+**
- **PySide6** — Qt6 GUI 框架，提供主窗口、系统托盘、休息窗口、视觉提醒
- **pywin32** — Windows 平台 API（空闲检测、电源事件、会话事件、开机启动）
- **JSON 持久化**（V0.5 起）——`config.json` + `stats.json` 原子写存储，
  无数据库、无 ORM；统计记录为「提醒触发次数」而非传感器数据

## 快速启动

### 1. 安装依赖

```bash
cd apps/EyeRest
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 运行

```bash
# 方式一：直接运行脚本（已自动处理 sys.path）
python app/main.py

# 方式二：以模块方式运行
python -m app.main
```

启动后会显示主窗口，标题为 `EyeRest`。日志会输出到数据目录下的
`logs/eyerest.log`（数据目录解析顺序见下文「数据目录」）。

## 数据目录（V1.0）

解析优先级（由高到低）：

1. 环境变量 `EYEREST_DATA_DIR` —— 显式指定
2. 打包模式（frozen）：exe 同级 `data/`（便携，不写系统盘）
3. 开发模式：`%APPDATA%/EyeRest/`（⚠️ C 盘，仅开发调试用；日常请用打包版，
   或显式设置 `EYEREST_DATA_DIR` 指向 G 盘）

## 打包（One-dir）

```bash
# 必须用项目 venv 构建（保证依赖齐全）
.venv\Scripts\python.exe -m PyInstaller EyeRest.spec --noconfirm
```

- 产物：`dist/EyeRest/`（`EyeRest.exe` + `_internal/` 依赖目录）
- One-dir 模式**无 _MEI 解压过程**：启动即运行，不再向临时目录倾倒
  ~115MB 文件，也不会残留 `_MEIxxxx` 垃圾目录
- 日常使用：双击 `dist/EyeRest/EyeRest-Portable.bat`（会把 TEMP/TMP
  也重定向到程序目录，实现零 C 盘写入）

## V1.0 正式版：四层护眼节奏（核心概念）

- 计时基准从「键鼠活跃时间」改为**屏幕暴露时间（Screen Exposure）**：
  屏幕亮着 + 未锁屏即算用眼，看 PDF / 视频 / 代码时键鼠空闲不再中断计时
- **四层节奏**（统一视觉提醒组件 `visual_cue.py`，默认不抢焦点）：
  1. 👁 **Blink 眨眼**——60s 一个 Blink Cycle，周期内每 10s 一次低打扰
     Cue（共 5 次），文字与纯动画交替出现，避免视觉习惯化
  2. 🌿 **Look Away 远眺**——每 20min 提示「看远处 20 秒」（视觉 Cue，
     不再全屏打断）
  3. 🚶 **Move 活动**——每 45min 提醒起身活动约 3 分钟（推荐默认值，
     可自由调整）
  4. 🧘 **Deep Break 深度休息**——默认 90min 周期 / 5min 全屏休息，
     独立于短休息记账，周期与时长均可自定义
- **视觉设置**：三种皮肤（极简眼睛 / 卡通双眼 / 小眼睛角色）、三档
  强度（安静 / 标准 / 明显）、位置（默认底部中央 + 四角预设 + 自由
  拖动，带边缘保护与位置锁定）
- **托盘控制权**：暂停 30 分钟 / 2 小时 / 今天、游戏/会议模式、立即休息
- 键鼠空闲仅用于判断「是否离开电脑」：idle 60~180s 进 AWAY（暂停累计、
  不重置），超过 300s 判定自然休息（重置会话）
- 数据诚实原则：无摄像头，统计记录的是「眨眼提示次数」而非「眨眼次数」

## 目录结构

```
apps/EyeRest/
├── app/
│   ├── main.py              # PySide6 应用入口（事件装配与接线）
│   ├── core/                # 事件总线、状态机、屏幕暴露/眨眼/活动/休息引擎
│   ├── windows/             # Windows 平台检测（空闲/电源/会话/全屏/启动）
│   ├── ui/                  # 主窗口、Dashboard、统计、设置、休息窗口、托盘、
│   │                        #   统一视觉提醒 visual_cue.py、位置编辑器
│   ├── services/            # 业务服务层 + JSON 存储（settings/stats store）
│   ├── config/              # 默认配置 + config.json 存储
│   ├── i18n/                # 中英文案
│   └── utils/               # 日志、时间、系统工具、音效合成
├── tests/                   # 单元测试（530+，offscreen UI 测试）
├── assets/                  # 图标
├── docs/                    # 版本重构规格文档
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 状态

**V0.5.1 — UI & Interaction Fix**：

- 四层护眼节奏（Blink Cycle / Look Away / Move / Deep Break）全部落地，
  计时基准为屏幕暴露时间
- 持久化为 `config.json` + `stats.json`（无数据库）
- 设置页纵向分区重排：时间控件直接键入（无 +/- 按钮）、滚轮防误触、
  保存/恢复默认有明确反馈、声音提供试听与触发规则说明
- 自定义提示位置：编辑模式下暂停全部节奏，遮罩之上显示真实角色供拖动
- 五套合成音效（木质/玻璃/呼吸/自然/铃音）与提醒强度联动
- 测试基线：530 passed + 3 subtests（offscreen）

## 许可

待补充。
