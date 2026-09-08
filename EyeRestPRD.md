# EyeRest — 技术产品规格（PRD）

> 版本：v1.0 ｜ 状态：规划稿 ｜ 技术栈：Python 3.12+ / PySide6 ｜ 平台：Windows 10/11

---

## 一、项目概述

| 项目 | 内容 |
|---|---|
| 产品名称 | **EyeRest** |
| 一句话定位 | 面向长时间电脑用户的低打扰用眼健康助手，通过识别实际电脑使用状态，在合适时间提醒用户远眺、眨眼和长休息 |
| 核心价值 | ① 统计真实 Active Time（非软件运行时间）；② 低打扰提醒（不锁屏、不强制）；③ 自然休息识别（离开电脑自动重置计时） |
| 目标用户 | 程序员、设计师、学生、长时间办公人群 |
| 技术栈 | Python 3.12+ / PySide6 / SQLite / SQLAlchemy / pydantic / pywin32 |
| 运行环境 | Windows 10 / 11（64-bit） |
| 分发方式 | PyInstaller 打包 exe + 桌面快捷方式 + 开机启动 |
| 参考产品 | Workrave（Active Time 模型）、CareUEyes（20-20-20 + 智能暂停） |

### 产品核心逻辑（MVP 浓缩）

> **检测"我真的用了多久电脑" → 到20分钟温和提醒我看远处20秒 → 到90分钟提醒我离开电脑5分钟 → 我离开电脑时自动暂停计时 → 回来继续 → 每天告诉我今天到底有没有好好休息。**

### 设计红线（不可违背）

- **不锁屏、不强制、不打断全屏工作**——提醒 > 打断
- **Active Time > App Running Time**——只统计真实键鼠活动时间
- **空闲 ≠ 休息完成**——只有真正进入休息状态并持续达标才记为完成
- **空闲不能无限积累成"休息"**——≥120秒空闲判定为自然休息并重置计时
- V1 只做 Windows，不做 macOS / Linux / 移动端

---

## 二、产品原则

整个产品遵循 5 条原则：

### 1. 真实使用时间 > 软件运行时间

用户打开电脑 8 小时，不代表用了 8 小时。系统统计 **Active Time** 而非 App Running Time。通过 Windows `GetLastInputInfo` API 获取最后一次输入事件时间，按每秒轮询判断 ACTIVE / IDLE。

### 2. 提醒 > 打断

默认不锁屏、不强制、不打断全屏工作。CareUEyes 提供强制休息和锁屏是成熟产品的高级能力，但 V1 不采用。

### 3. 低存在感

软件 90% 以上时间应该安静地待在系统托盘。Dashboard 和 Statistics 只是查看页面，真正高频使用的是 **Tray + Reminder**。

### 4. 数据应该服务行为

不做"健康指数 87 分"这类模糊评分，而是输出具体行为数据：
- 今天有效使用 6h32m
- 最长连续使用 1h47m
- 18 次提醒，完成 14 次

### 5. V1 只做 Windows

先不做 macOS、Linux、Android、iOS。

---

## 三、产品信息架构

```text
EyeRest
│
├── Dashboard        ← 唯一需要认真做 UI 的页面
│
├── Statistics       ← 数据查看
│
├── Settings         ← 配置中心
│   ├── Break        ← 休息参数
│   ├── Reminder     ← 提醒方式
│   ├── Behavior     ← 智能检测行为
│   └── General      ← 通用设置
│
└── System Tray      ← 高频使用入口
```

真正高频使用的是 **Tray + Reminder**，Dashboard 和 Statistics 只是查看。

---

## 四、页面设计

### ① Dashboard 首页

这是唯一需要认真做 UI 的页面。

```text
┌────────────────────────────────────────┐
│ 👁 EyeRest                         ⚙  │
├────────────────────────────────────────┤
│                                        │
│              今日用眼                   │
│                                        │
│             06:32:18                   │
│           有效使用时间                  │
│                                        │
│     ● 正在使用电脑                     │
│                                        │
├────────────────────────────────────────┤
│                                        │
│  下一次远眺                             │
│                                        │
│             08:32                      │
│                                        │
│        还有 12 分 18 秒                 │
│                                        │
├────────────────────────────────────────┤
│                                        │
│  连续使用       32 min                 │
│  今日休息       14 次                  │
│  休息完成率     82%                    │
│                                        │
├────────────────────────────────────────┤
│                                        │
│          [ 立即休息 20 秒 ]             │
│                                        │
└────────────────────────────────────────┘
```

**状态显示：**

```text
🟢 正在使用    (ACTIVE)
🟡 即将休息    (BREAK_WARNING)
🔵 正在休息    (SHORT_BREAK / LONG_BREAK)
⚪ 空闲        (IDLE)
⏸ 已暂停      (PAUSED)
```

### ② Statistics 统计页

第一版不做复杂图表，先做文本 + 柱状图。

```text
今日

有效使用       06:32
最长连续使用   01:47
平均连续使用   00:38

远眺提醒       18
完成           14
跳过            3
延迟            1

长休息          4
完成            4
```

下面：

```text
过去7天

Mon   ███████
Tue   ██████
Wed   ████████
Thu   █████
Fri   ███████
Sat   ████
Sun   █████
```

**第二阶段再做：** 日/周/月切换、平均连续使用时间趋势、完成率趋势。

### ③ Settings 设置页

#### Break 设置

```text
短休息
☑ 启用 20-20-20
工作时间  [ 20 ] 分钟
休息时间  [ 20 ] 秒

长休息
☑ 启用长休息
连续使用  [ 90 ] 分钟
休息时间  [ 5 ] 分钟（可配置 1~30 分钟）
```

#### Reminder 设置

```text
提醒方式
☑ 桌面通知
☑ 视觉提醒
☐ 声音

提前提醒
[ 30 ] 秒

休息操作
☑ 允许延迟
延迟时间  [ 5 ] 分钟
最多延迟  [ 2 ] 次
```

#### Behavior 设置

```text
智能检测
☑ 根据键盘/鼠标活动计算有效使用时间
☑ 空闲时暂停计时
空闲阈值  [ 60 ] 秒
☑ Windows 锁屏时暂停
☑ Windows 睡眠时暂停
☑ 全屏程序时延迟提醒（V1.1）
```

> 注意：**空闲 ≠ 休息完成。** 用户停止操作 30 秒不应该认为完成了 20 秒远眺。只有真正进入休息状态并持续达到要求，才记为一次完成。

#### General 设置

```text
☑ Windows 启动时运行
☑ 启动后最小化到托盘
☑ 开机自动进入保护状态

语言  [ 简体中文 ]
主题  [ 跟随系统 ]
数据保存  本地
```

### ④ System Tray 系统托盘

这是 V1 非常重要的部分。

**右键托盘菜单：**

```text
👁 EyeRest
● 正在保护
有效使用：02:32:18
下一次休息：12:32
──────────────
立即休息
暂停 30 分钟
暂停今天
──────────────
打开 EyeRest
设置
退出
```

**左键：** 打开 Dashboard。

---

## 五、状态机设计（核心）

整个软件的核心不是 UI，而是**状态机**。

### 状态定义

```python
class AppState(Enum):
    INACTIVE       # 空闲/未活动
    ACTIVE         # 正在使用电脑
    IDLE           # 空闲中（>60秒无输入）
    BREAK_WARNING  # 即将休息（提前30秒）
    SHORT_BREAK    # 短休息中
    LONG_BREAK     # 长休息中
    PAUSED         # 用户手动暂停
    LOCKED         # 系统锁屏
    SLEEP          # 系统睡眠
```

### 状态转换图

```
                 ┌──────────────┐
                 │   INACTIVE   │
                 └──────┬───────┘
                        │ 用户开始操作
                        ↓
                 ┌──────────────┐
                 │    ACTIVE    │
                 └──────┬───────┘
                        │
               Active Time达到20min
                        ↓
                 ┌──────────────┐
                 │  BREAK_DUE   │
                 └──────┬───────┘
                        │
                 ┌──────┴───────┐
                 ↓              ↓
             COMPLETE        POSTPONE
                 │              │
                 ↓              ↓
              ACTIVE        ACTIVE
```

### 关键转换规则

#### ACTIVE → IDLE
- **条件**：超过 60 秒没有键鼠输入
- **动作**：active_time 停止增长，idle_time 开始增长

#### IDLE → INACTIVE（自然休息）
- **条件**：Idle ≥ 120 秒
- **动作**：判定为自然休息，active_time 重置为 0，重新开始
- **原理**：用户已经离开电脑，不应无限积累成"休息"

#### IDLE → ACTIVE
- **条件**：检测到键鼠输入
- **动作**：恢复 active_time 累计

#### ACTIVE → BREAK_WARNING
- **条件**：active_time ≥ work_duration - warning_duration（默认 1200 - 30 = 1170 秒）
- **动作**：显示 Soft 通知"还有30秒，准备看远处休息一下"

#### BREAK_WARNING → SHORT_BREAK
- **条件**：active_time ≥ work_duration（默认 1200 秒 = 20 分钟）
- **动作**：进入短休息，显示休息窗口

#### SHORT_BREAK 中断处理（关键）
- **条件**：休息期间检测到键鼠活动
- **动作**：不立即判定"休息失败"，而是暂停休息倒计时
- **UI**：提示"你似乎还在使用电脑"，提供 [继续休息] [跳过] 按钮
- **V1 简化**：直接暂停倒计时，用户再次停止操作后继续

#### ACTIVE → LONG_BREAK
- **条件**：active_time ≥ 90 分钟
- **动作**：进入长休息，显示"该离开电脑一会儿了"，默认 5 分钟（可配置 1~30 分钟）

#### ANY → LOCKED / SLEEP
- **条件**：系统锁屏 / 系统睡眠
- **动作**：计时器暂停
- **恢复后**：检查空闲时长，≥120 秒判定自然休息重置，<120 秒恢复 ACTIVE

---

## 六、计时逻辑与算法

### 核心原则：不用 wall-clock 简单计时

不要设计 `timer += 1` 这种简单逻辑，而应该：

```text
System Clock
     ↓
Input Detector (GetLastInputInfo)
     ↓
Activity State (ACTIVE / IDLE)
     ↓
Active Time Engine
     ↓
Break Engine
     ↓
Reminder Engine
```

### Active Time 算法示例

```text
当前时间 = 10:00

10:00 → 键盘
10:01 → 鼠标
10:02 → 键盘
10:03 → 无操作
10:04 → 无操作
10:05 → 鼠标

idle_threshold = 60秒

10:00 ACTIVE
10:01 ACTIVE
10:02 ACTIVE
10:03 IDLE
10:04 IDLE
10:05 ACTIVE

只有 ACTIVE 期间增加 active_time
```

### V1 核心规则（定死）

| 规则 | 阈值 |
|---|---|
| Active | 键鼠活动距离上次输入 ≤ 60 秒 |
| Idle | > 60 秒没有键鼠输入 |
| Natural Rest | Idle ≥ 120 秒 |
| Short Break | Active Time ≥ 20 分钟 |
| Long Break | Active Time ≥ 90 分钟 |
| Warning | 休息前 30 秒 |

### 20-20-20 实现

```python
remaining = active_threshold - active_time

if remaining <= warning_duration:    # ≤30秒
    state = BREAK_WARNING
elif remaining <= 0:                  # ≤0秒
    state = SHORT_BREAK
```

### 重要规则：空闲不能无限积累成"休息"

```text
检测到长时间 Idle（≥120秒）
        ↓
判定为自然休息
        ↓
ACTIVE_TIME = 0
        ↓
重新开始
```

例如用户去吃饭 2 小时，回来后不能说"恭喜，你已经休息 2 小时"，然后立即重新开始 20 分钟。应该重置 active_time，从头开始计时。

---

## 七、提醒策略（三级）

### Level 1：Soft Warning（提前 30 秒）

```text
👀
还有30秒
准备看远处休息一下
```
- 桌面通知，无声音

### Level 2：Short Break（20 分钟）

```text
👀 看远处20秒
18
```
- 视觉提醒窗口，倒计时

### Level 3：Long Break（90 分钟）

```text
🧘
已经连续使用电脑90分钟
建议休息5分钟
[现在休息] [延迟5分钟]
```
- 长休息窗口

---

## 八、延迟机制

用户点击"延迟5分钟"：

```text
next_break = now + 5min
postpone_count += 1
```

最多延迟 2 次，第 3 次不再提供延迟按钮。**但仍然不强制锁屏。**

---

## 九、数据库设计（SQLite）

V1 直接使用 SQLite，不使用 MySQL。数据库文件存放在 `%AppData%/EyeRest/eyerest.db`。

### 9.1 settings 表

```sql
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

默认配置项：

| key | value | 说明 |
|---|---|---|
| short_break_enabled | true | 启用 20-20-20 |
| short_work_duration | 1200 | 工作时间（秒，20分钟） |
| short_break_duration | 20 | 休息时间（秒） |
| long_break_enabled | true | 启用长休息 |
| long_work_duration | 5400 | 连续使用（秒，90分钟） |
| long_break_duration | 300 | 休息时间（秒，5分钟） |
| idle_threshold | 60 | 空闲阈值（秒） |
| warning_duration | 30 | 提前提醒（秒） |
| postpone_duration | 300 | 延迟时间（秒，5分钟） |
| max_postpone | 2 | 最多延迟次数 |

### 9.2 usage_sessions 表

```sql
CREATE TABLE usage_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time DATETIME NOT NULL,
    end_time DATETIME,
    active_seconds INTEGER DEFAULT 0,
    idle_seconds INTEGER DEFAULT 0
);
```

记录每次使用会话。

### 9.3 break_events 表（核心数据表）

```sql
CREATE TABLE break_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,              -- SHORT / LONG
    scheduled_at DATETIME NOT NULL,
    started_at DATETIME,
    ended_at DATETIME,
    duration_seconds INTEGER,
    status TEXT NOT NULL,            -- COMPLETED / SKIPPED / POSTPONED / INTERRUPTED
    postpone_count INTEGER DEFAULT 0
);
```

### 9.4 daily_stats 表（每日汇总）

```sql
CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,
    active_seconds INTEGER DEFAULT 0,
    idle_seconds INTEGER DEFAULT 0,
    short_breaks INTEGER DEFAULT 0,
    short_breaks_completed INTEGER DEFAULT 0,
    short_breaks_skipped INTEGER DEFAULT 0,
    long_breaks INTEGER DEFAULT 0,
    long_breaks_completed INTEGER DEFAULT 0,
    longest_session_seconds INTEGER DEFAULT 0
);
```

**为什么单独设计 daily_stats？** 因为以后做"今天/本周/本月"统计时不需要每次扫描所有事件。

```text
Raw Events (break_events)
    ↓
Daily Aggregator
    ↓
daily_stats
    ↓
Dashboard / Statistics
```

---

## 十、Windows 技术方案

### 技术栈

```text
Python 3.12+
PySide6          # GUI 框架
SQLite           # 本地数据库
SQLAlchemy       # ORM
pydantic         # 数据验证
pywin32          # Windows API
pynput           # 键鼠监听（辅助，不作为 Idle 检测核心）
PyInstaller      # 打包
```

### 10.1 Idle 检测（核心：GetLastInputInfo）

Windows 官方 API `GetLastInputInfo` 用于获取最后一次输入事件，可直接用于 input idle detection。

```python
import ctypes
from ctypes import wintypes

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]

def get_idle_seconds():
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    tick = ctypes.windll.kernel32.GetTickCount()
    return (tick - info.dwTime) / 1000
```

**不要高频轮询**——V1 每 1 秒检测一次足够，CPU 占用非常低。

### 10.2 系统事件监听

需要监听以下 Windows 消息：

| 事件 | Windows 消息 | 处理 |
|---|---|---|
| 锁屏/解锁 | WM_WTSSESSION_CHANGE | LOCKED / 恢复 |
| 睡眠/唤醒 | WM_POWERBROADCAST | SLEEP / 恢复 |
| 关机 | WM_QUERYENDSESSION | 保存数据 |

### 10.3 系统托盘

使用 PySide6 的 `QSystemTrayIcon`，负责：托盘图标、右键菜单、桌面通知。

### 10.4 自动启动

使用 Windows 注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，无需管理员权限。

### 10.5 通知

V1 先使用 Windows 原生通知（通过 QSystemTrayIcon.showMessage），目标是系统通知中心能够看到。

### 10.6 全屏检测（V1.1）

V1 预留接口，V1.1 实现。思路：

```text
GetForegroundWindow → GetWindowRect → 判断窗口是否覆盖当前显示器
如果 fullscreen == True → BREAK_WARNING → 延迟提醒
```

---

## 十一、项目目录结构

```text
eyerest/
│
├── app/
│   ├── main.py                    # 应用入口
│   │
│   ├── core/                      # 核心引擎
│   │   ├── state_machine.py       # 状态机
│   │   ├── timer_engine.py        # 计时引擎
│   │   ├── activity_monitor.py    # 活动监控
│   │   ├── break_engine.py        # 休息引擎
│   │   └── event_bus.py           # 事件总线
│   │
│   ├── windows/                   # Windows 系统集成
│   │   ├── idle_detector.py       # GetLastInputInfo
│   │   ├── power_monitor.py       # 电源事件
│   │   ├── session_monitor.py     # 会话/锁屏事件
│   │   ├── fullscreen_detector.py # 全屏检测（V1.1）
│   │   └── startup.py             # 开机启动
│   │
│   ├── database/                  # 数据持久化
│   │   ├── database.py            # 连接管理
│   │   ├── models.py              # SQLAlchemy 模型
│   │   ├── repository.py          # CRUD 操作
│   │   └── migrations.py          # 建表迁移
│   │
│   ├── ui/                        # 界面
│   │   ├── main_window.py         # 主窗口
│   │   ├── dashboard.py           # Dashboard
│   │   ├── statistics.py          # 统计页
│   │   ├── settings.py            # 设置页
│   │   ├── break_window.py        # 休息窗口
│   │   └── tray.py                # 系统托盘
│   │
│   ├── services/                  # 业务服务
│   │   ├── usage_service.py       # 使用记录
│   │   ├── break_service.py       # 休息记录
│   │   ├── statistics_service.py  # 统计聚合
│   │   └── notification_service.py # 通知服务
│   │
│   ├── config/
│   │   └── defaults.py            # 默认配置
│   │
│   └── utils/
│       ├── logger.py              # 日志
│       ├── time.py                # 时间工具
│       └── system.py              # 系统工具
│
├── tests/                         # 测试
│   ├── test_timer.py
│   ├── test_state_machine.py
│   ├── test_activity.py
│   └── test_break.py
│
├── assets/
│   ├── icons/
│   └── sounds/
│
├── installer/
│
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 十二、核心模块架构

**架构分层（不可写成一坨）：**

```text
                  Windows
                     │
          ┌──────────┼──────────┐
          ↓          ↓          ↓
      Idle API    Power API   Window API
          │          │          │
          └──────────┼──────────┘
                     ↓
              ActivityMonitor
                     ↓
               TimerEngine
                     ↓
              StateMachine
                     ↓
               BreakEngine
                     ↓
             NotificationService
                     ↓
                    UI
```

**数据层：**

```text
ActivityMonitor → UsageService → SQLite
BreakEngine → BreakService → SQLite
Raw Events → Daily Aggregator → daily_stats → Dashboard
```

**核心类接口：**

```python
class ActivityMonitor:
    def get_idle_seconds(self) -> float: ...

class TimerEngine:
    active_seconds: int
    idle_seconds: int
    def tick(self): ...
    def reset(self): ...

class BreakEngine:
    def should_warn(self) -> bool: ...
    def should_break(self) -> bool: ...

class StateMachine:
    state: AppState
    def transition(self, event): ...

class UsageService:
    def record_session(self): ...

class StatisticsService:
    def get_today_stats(self): ...
```

---

## 十三、开发计划

开发顺序：**底层逻辑 → 状态机 → UI**，不按页面开发。分三个迭代：

### V0.1：核心计时与基础提醒（约 4-5 天）

| Sprint | 内容 |
|---|---|
| Day 1 | Python 项目脚手架、PySide6、日志系统、配置系统、能启动 |
| Day 2 | Windows 活动检测（GetLastInputInfo）、ACTIVE/IDLE 判断 |
| Day 3 | Timer Engine、Active Time / Idle Time 分别累计 |
| Day 4 | 状态机（8 个状态 + 转换规则 + 单元测试） |
| Day 5 | Break Engine、20-20-20 规则、系统托盘、休息窗口 |

### V0.2：长休息、延迟与数据持久化（约 3-4 天）

| Sprint | 内容 |
|---|---|
| Day 6 | 长休息逻辑、延迟机制（最多 2 次）、暂停功能 |
| Day 7 | SQLite 数据库（4 张表 + Repository + Service） |
| Day 8 | Daily Statistics 聚合、统计页 |
| Day 9 | Dashboard 首页、Settings 设置页 |

### V0.3：系统集成与打包（约 3-4 天）

| Sprint | 内容 |
|---|---|
| Day 10 | Windows 系统事件监听（锁屏/睡眠/唤醒）、通知服务 |
| Day 11 | 开机自启动、预留扩展接口（Display/Camera/Fullscreen/Health/Cloud） |
| Day 12 | 异常场景测试（锁屏、睡眠、跨午夜、长时间 Idle、多显示器、程序崩溃） |
| Day 13 | PyInstaller 打包、安装包、最终验收 |

---

## 十四、验收标准

不用"功能差不多都做了"来判断完成，直接用下表：

| 验收项 | 标准 |
|---|---|
| 程序启动 | ≤2 秒 |
| 常驻内存 | 低水平 |
| CPU | 空闲时接近 0 |
| Active 检测 | 正常 |
| Idle 检测 | 正常 |
| 20 分钟提醒 | 正常 |
| 20 秒休息 | 正常 |
| 90 分钟长休息 | 正常 |
| 延迟 | 正常 |
| 跳过 | 正常 |
| 锁屏 | 正常 |
| 睡眠 | 正常 |
| 唤醒 | 正常 |
| 开机启动 | 正常 |
| Tray | 正常 |
| 数据统计 | 正常 |
| 程序重启 | 数据不丢失 |

---

## 十五、V1 明确不做

以下功能全部推迟到 V2/V3：

- ❌ AI 眼睛识别
- ❌ 摄像头 / 人脸识别 / 坐姿检测
- ❌ 蓝光过滤 / 屏幕色温 / 屏幕亮度
- ❌ 多显示器高级管理
- ❌ AI 健康分析
- ❌ 云同步 / 用户账号
- ❌ 社交 / 排行榜 / 成就系统
- ❌ 会员 / 手机 App / Mac 版本

---

## 十六、预留扩展接口

虽然 V1 不开发，但架构上预留以下接口，以后加功能不会把核心代码搞乱：

```python
class DisplayService:
    def set_brightness(self, value):
        raise NotImplementedError

class CameraService:
    def start_detection(self):
        raise NotImplementedError

class FullscreenService:
    def is_fullscreen(self) -> bool:
        raise NotImplementedError

class HealthAnalysisService:
    def analyze(self, stats):
        raise NotImplementedError

class CloudService:
    def sync(self, data):
        raise NotImplementedError
```

---

## 十七、最终产品架构

```text
                       EyeRest
                          │
          ┌───────────────┼───────────────┐
          │               │               │
       Activity          Break           Data
          │               │               │
      键鼠检测          20-20-20        SQLite
      Idle检测          长休息          Statistics
      锁屏检测          延迟
      睡眠检测          跳过
          │               │
          └───────┬───────┘
                  ↓
             State Machine
                  ↓
            Reminder Engine
                  ↓
         ┌────────┴────────┐
         ↓                 ↓
       Tray            Break UI
         │                 │
         └────────┬────────┘
                  ↓
               Dashboard
```

---

## 参考资料

- [Workrave](https://workrave.org/) — Active Time + Microbreak + Restbreak 产品模型验证
- [CareUEyes](https://care-eyes.com/) — 20-20-20 + 智能暂停 + 全屏检测组合
- [Microsoft GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo) — Windows 官方 Idle 检测 API
