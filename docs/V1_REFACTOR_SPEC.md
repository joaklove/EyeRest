# EyeRest V1.0 重构规格

> 产品定位变更：从「电脑休息计时器」→ **「屏幕使用节奏管理器」**
> 核心概念变更：从 `Active Time`（键鼠活跃）→ **`Screen Exposure`（屏幕暴露）**

---

## 零、为什么重构（要解决的问题）

| # | 问题 | 现状代码 | 后果 |
|---|---|---|---|
| P1 | 键鼠无操作 ≠ 眼睛没看屏幕 | `activity_monitor` 用 `GetLastInputInfo` 直接推导 `NATURAL_REST` | 看 PDF / 视频 / 代码思考时不被计为用眼 |
| P2 | 用眼节奏只有一层 | 只有 20 分钟休息 | 1 分钟眨眼提醒完全缺失 |
| P3 | 多时钟多状态源 | `TimerEngine` 增量累加 `active_seconds += delta`；`BreakWindow` 自己倒计时 | 时间不可靠，暂停/休眠后漂移 |
| P4 | C 盘 Runtime | PyInstaller One-file 每次启动解压 `_MEI` 到 C 盘 Temp | 与「低磁盘写入」产品理念冲突 |

---

## 一、状态模型（Step 1）

### 1.1 三维状态（新）

```
ScreenSession   ON / AWAY / OFF          屏幕暴露状态
BreakState      NONE / BLINK_CUE / SHORT_BREAK / LONG_BREAK
ProtectionState NORMAL / PAUSED
```

### 1.2 与现有 `AppState` 的关系（关键决策）

**不删除 `AppState`**，而是把它降级为**对外兼容视图**——由三维状态推导，供 UI / 托盘 / 现有测试消费。

理由：直接废弃会摧毁 `test_state_machine.py`（约 80 用例）等 4 个核心测试文件；降级为视图可在**零测试破坏**的前提下完成内核替换。

映射表：

| ScreenSession | BreakState | ProtectionState | 推导出的 AppState |
|---|---|---|---|
| OFF | * | * | `INACTIVE` |
| AWAY | * | NORMAL | `IDLE` |
| ON | NONE | NORMAL | `ACTIVE` |
| ON | NONE | PAUSED | `PAUSED` |
| ON | BLINK_CUE | * | `ACTIVE`（眨眼提示不打断状态） |
| ON | SHORT_BREAK | * | `SHORT_BREAK` |
| ON | LONG_BREAK | * | `LONG_BREAK` |
| — | — | 系统锁屏 | `LOCKED` |
| — | — | 系统休眠 | `SLEEP` |

> `BREAK_WARNING` 保留，由 BreakEngine 在休息前 `WARNING_DURATION` 发布。

### 1.3 ScreenSession 判定规则

输入：`idle_seconds`（`idle_detector`）、锁屏/休眠信号。

```text
idle <  180s            → ON     继续累计暴露（看PDF 3分钟不被误判休息）
180s ≤ idle < 300s      → AWAY   暂停累计，但不重置（回来接着算）
idle ≥ 300s             → OFF    判定 Natural Rest，重置 Blink/Short Clock
锁屏 / 休眠 / 显示器关闭 → OFF    立即，不受 idle 阈值约束
idle < 60s（从 AWAY/OFF 回来） → ON  恢复累计
```

> `AWAY` 期间的时长计入 `screen_sessions.away_seconds`，不重置计时器——这是「看长文档/思考」场景的关键保护。

---

## 二、模块设计

### 2.1 `app/core/clock.py`（新，Step 5）

统一单调时间源，便于测试注入假时钟。

```python
class MonotonicClock:
    def now(self) -> float: ...
    def deadline(self, duration_seconds: float) -> float: ...     # now() + duration
    def remaining(self, deadline: float) -> float: ...            # max(0.0, deadline - now())

clock = MonotonicClock()   # 模块级默认实例，测试可替换
```

所有引擎通过构造参数接收 `clock`，**禁止**直接使用 `time.time()` / `time.monotonic()`。

### 2.2 `app/core/screen_session.py`（新，Step 2）

```python
class ScreenSessionState(Enum):
    ON = "on"
    AWAY = "away"
    OFF = "off"

@dataclass
class ScreenSessionSnapshot:
    state: ScreenSessionState
    exposure_seconds: float      # 当前 Session 内累计暴露
    away_seconds: float
    session_started_at: float    # clock 时间
    today_exposure_seconds: float

class ScreenSessionEngine:
    def __init__(self, clock, idle_provider, *,
                 away_threshold=180.0, natural_rest_threshold=300.0,
                 resume_threshold=60.0, event_bus=None) -> None

    def tick(self) -> None                    # 每秒调用
    def get_snapshot(self) -> ScreenSessionSnapshot
    def reset_session(self) -> None           # 休息完成/自然休息后调用
    def update_thresholds(self, away=None, natural_rest=None, resume=None) -> None
    def on_system_lock(self) -> None
    def on_system_sleep(self) -> None
    def on_system_unlock(self) -> None
    def on_system_wake(self) -> None
```

事件：
- `SCREEN_SESSION_CHANGED`（新）`{"old": str, "new": str, "exposure_seconds": float}`
- `NATURAL_REST_DETECTED`（新）`{"away_seconds": float}` —— idle ≥ 300s 时发布

### 2.3 `app/core/blink_engine.py`（新，Step 3）

```python
class BlinkEngine:
    def __init__(self, clock, session_engine, *,
                 interval=60.0, cue_duration=3.0,
                 event_bus=None) -> None

    def tick(self) -> None
    def seconds_until_next_cue(self) -> float
    def on_cue_completed(self) -> None     # 用户完成眨眼
    def on_cue_skipped(self) -> None       # 忽略 → 提升提醒强度
    def reset(self) -> None
    def update_settings(self, interval=None, cue_duration=None) -> None
```

**提醒疲劳度（escalation）**：连续跳过次数 → 强度等级

```text
skip_streak 0      → level 0  极轻（小圆点呼吸动画，1.5s）
skip_streak 1~2    → level 1  轻（眨眼动画 + 文字，3s）
skip_streak ≥ 3    → level 2  明显（状态栏 + 动画，5s）
完成一次           → skip_streak 归零
```

事件：`BLINK_CUE`（新）`{"level": int, "duration": float}`

**产品文案约束**：对外一律称「眨眼提示 Blink Cue」，不称「眨眼检测」——无摄像头，不做医疗/算法准确性承诺。

### 2.4 `app/core/break_engine.py`（重构，Step 4）

保留类名与公开 API，内部改为：
- 计时源从 `TimerEngine.active_seconds` → `ScreenSessionEngine.exposure_seconds`
- 阈值：`SHORT_WORK_DURATION=1200`（20min）、`LONG_WORK_DURATION=5400`（90min）
- 新增前置轻提示：`WARNING_DURATION` 前发布 `BREAK_WARNING`
- 倒计时统一目标时间模型：`end_at = clock.deadline(duration)`

```python
@dataclass
class BreakProgress:
    break_type: str
    end_at: float          # 单调目标时间
    remaining: float       # clock.remaining(end_at)
```

### 2.5 `app/ui/break_window.py`（重构倒计时）

```python
# 旧：remaining -= 1
# 新：
self._end_at = clock.deadline(duration)
remaining = max(0, clock.remaining(self._end_at))
```

---

## 三、数据库（Step 6）

### 3.1 新增表

```sql
-- 屏幕暴露会话
CREATE TABLE screen_sessions (
    id                INTEGER PRIMARY KEY,
    started_at        DATETIME NOT NULL,
    ended_at          DATETIME,
    exposure_seconds  INTEGER NOT NULL DEFAULT 0,
    away_seconds      INTEGER NOT NULL DEFAULT 0,
    ended_reason      TEXT              -- 'natural_rest' | 'lock' | 'sleep' | 'app_exit'
);

-- 眨眼提示事件
CREATE TABLE blink_events (
    id             INTEGER PRIMARY KEY,
    triggered_at   DATETIME NOT NULL,
    level          INTEGER NOT NULL DEFAULT 0,
    completed      BOOLEAN,
    responded_at   DATETIME
);
```

### 3.2 `daily_stats` 新增列

```text
screen_exposure_seconds   INTEGER DEFAULT 0
blink_prompt_count        INTEGER DEFAULT 0
blink_completed_count     INTEGER DEFAULT 0
short_break_completed     INTEGER DEFAULT 0
long_break_completed      INTEGER DEFAULT 0
longest_screen_session    INTEGER DEFAULT 0
```

### 3.3 迁移

`migrations.CURRENT_VERSION` → `2`，注册 `_migrate_v2`。
**向后兼容**：`usage_sessions` 保留不动（不删列、不删表），仅停止新增写入。

### 3.4 配置项新增（`defaults.py` + `DEFAULT_SETTINGS`）

```text
BLINK_INTERVAL          = 60.0     # 眨眼提示间隔
BLINK_CUE_DURATION      = 3.0
AWAY_THRESHOLD          = 180.0    # 进入 AWAY
NATURAL_REST_THRESHOLD  = 300.0    # 判定自然休息（已存在，语义调整）
SESSION_RESUME_THRESHOLD= 60.0     # 从 AWAY/OFF 恢复
```

---

## 四、事件总线扩展

新增 `EventType`：

```python
SCREEN_SESSION_CHANGED = "screen_session_changed"
NATURAL_REST_DETECTED  = "natural_rest_detected"
BLINK_CUE              = "blink_cue"
BLINK_COMPLETED        = "blink_completed"
BLINK_SKIPPED          = "blink_skipped"
```

---

## 五、Dashboard（Step 7）

```text
┌──────────────────────────────┐
│ 当前状态  ● 屏幕使用中          │
│ ─────────────────────────── │
│ 👁 下一次眨眼提示     00:18    │
│ 🌿 下一次远眺       12:32    │
│ 🧘 长休息          01:08:21  │
│ ─────────────────────────── │
│ 今日                         │
│ 屏幕暴露        06h 28m      │
│ 眨眼提示        74           │
│ 远眺完成        16 / 18      │
│ 长休息           4 / 4       │
│ ─────────────────────────── │
│ [立即休息] [暂停30m] [重置]   │
└──────────────────────────────┘
```

---

## 六、C 盘策略（Step 9-10）

### 6.1 打包模式：`onefile` → **onedir**

```text
dist/EyeRest/
├── EyeRest.exe
├── _internal/          # 依赖，不解压、不产生 _MEI
└── data/               # 用户数据（EYEREST_DATA_DIR 兼容）
```

优势：无 `_MEI` 解压、启动更快、适合常驻后台、符合低磁盘写入理念。

### 6.2 Runtime 兜底（若保留 onefile）

`EyeRest-Portable.bat` 继续重定向 `TEMP`/`TMP` 到 exe 同级 `.tmp\`。

---

## 七、实施顺序

| Step | 内容 | 状态 |
|---|---|---|
| 1 | `clock.py` 单调时钟 | ✅ |
| 2 | `screen_session.py` + 事件扩展 | ✅ |
| 3 | `blink_engine.py` | ✅ |
| 4 | `break_engine.py` 重构（exposure 驱动 + 目标时间倒计时） | ✅ |
| 5 | 数据库 v2 迁移 + 新表/新列 + 配置项 | ✅ |
| 6 | `main.py` 装配 | ✅ |
| 7 | Dashboard / Statistics / Settings UI | ✅ |
| 8 | 测试补充 | ✅ |
| 9 | 打包 onedir | ✅ |

## 八、明确不做（V1）

摄像头、AI 眨眼检测、医疗健康评分、蓝光护眼、复杂色温、云端同步。接口预留，不实现。

---

## 九、正式版规格落地记录（2026-09-06）

产品规格正式收敛为「四层节奏 + 统一视觉组件 + 用户最高控制权」，本节
记录与上文的差异（以本节为准）：

| 层级 | 原方案 | 正式版 |
|---|---|---|
| 👁 Blink | 60s 一次 Cue | **Blink Cycle 模型**：60s 周期 × 每 10s 一次 Cue（5 次/周期），文字与纯动画交替 |
| 🌿 Look Away | 全屏短休息窗口 | **非模态视觉 Cue**：「看远处 20 秒」，不再打断工作流 |
| 🚶 Move | 无 | **新增**：45min 暴露时长触发活动提醒（`move_engine.py`），默认建议 3 分钟 |
| 🧘 Deep Break | 3 次短休息后触发 | 与短休息计数**完全解耦**，独立 90min 累计（周期/时长可自定义） |

组件变更：

- `app/ui/blink_cue.py` → **`app/ui/visual_cue.py`**：统一四类提示
  （👁/🌿/🚶/🧘），三种 Skin（minimal/cartoon/character）、三档强度
  （quiet/standard/prominent）、可拖动位置（边缘保护 + 位置锁定 +
  显示器记忆）
- 新增 `app/core/move_engine.py`：MOVE_CUE 事件，活动时长结束或深度
  休息完成后重置
- 统计口径：记录 `blink_cycle_started / cue_shown / cycle_finished`，
  展示为「眨眼提示 N 次」而非「眨眼 N 次」（数据诚实原则）
- 托盘：新增暂停 2 小时、游戏/会议模式（等价手动暂停）
- 测试基线：**515 passed + 3 subtests**（BlinkEngine 测试类按 Cycle
  模型重写，新增 `test_move_engine.py`）
