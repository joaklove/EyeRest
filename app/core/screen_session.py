"""屏幕暴露会话引擎（Screen Exposure Session）—— EyeRest V1.0 核心。

## 为什么需要它

旧模型把「键鼠无操作」直接等价于「眼睛没在看屏幕」，这是错的：

- 看 PDF：3 分钟不动鼠标，眼睛一直在读
- 看视频 / 会议：完全不碰键鼠，视觉负荷最高
- 看代码思考：长时间静止，正是最需要护眼的时刻

``GetLastInputInfo`` 的官方定位是 **input idle detection**（输入空闲检测），
它不是「用户有没有看屏幕」的传感器。

因此本引擎重新定义核心量：

> **Screen Exposure（屏幕暴露）** —— 屏幕亮着 + 未锁屏 + 程序处于正常桌面会话
> + 未进入明确长时间离开状态，即认为处于暴露中。

这不是断言「你一定正在看屏幕」，而是「电脑处于你可能正在进行视觉任务的状态」，
产品上更诚实。

## 三态模型

```text
ON    电脑处于视觉工作状态，累计 exposure_seconds
AWAY  疑似离开（idle 超过 away_threshold），暂停累计但**不重置**计时器
OFF   锁屏 / 休眠 / 自然休息，结束会话
```

``AWAY`` 不重置计时器是关键——它保证「看长文档 3 分钟」不会被误判成休息。

## 全屏内容消费的例外（后补的关键修正）

上面的模型有个自相矛盾的地方：文档一边说「看视频 / 会议：完全不碰键鼠，
视觉负荷最高」，一边又用同一套阈值把「看视频」判成 AWAY —— 而 AWAY 会让
BlinkEngine 停发眨眼提示。结果是**用户最需要护眼的场景，提示反而全停**。

修正方式：注入 ``fullscreen_provider`` 后，检测到「全屏内容消费」
（看视频 / 全屏演示 / 全屏应用，由 ``SHQueryUserNotificationState`` 判定）
时改用 ``fullscreen_away_threshold`` / ``fullscreen_natural_rest_threshold``
这组放宽阈值 —— 只要没超过那个时长，就认为人还在屏幕前，继续累计暴露、
继续走提示节奏。只有超过放宽阈值仍无输入，才回到「真的离开了」的判断。

真到了全屏但人确实走了的情况（比如视频挂着人去吃饭），放宽阈值兜底：
超过 ``fullscreen_natural_rest_threshold`` 仍会判自然休息。

## 用法

```python
engine = ScreenSessionEngine(clock=FakeClock(), idle_provider=get_idle_seconds)
engine.tick()              # 每秒调用一次
snap = engine.get_snapshot()
```

引擎不直接依赖 Qt 或 Windows API，``idle_provider`` 为可调用对象，
返回「距上次键鼠输入的秒数」，便于测试与跨平台。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from app.config import defaults
from app.core.clock import Clock, default_clock
from app.core.event_bus import EventBus, EventType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ScreenSessionState(str, Enum):
    """屏幕暴露会话状态。"""

    #: 屏幕暴露中：累计 exposure_seconds
    ON = "on"
    #: 疑似离开：暂停累计，但不重置计时器（看长文档 / 思考）
    AWAY = "away"
    #: 会话结束：锁屏 / 休眠 / 判定自然休息
    OFF = "off"


@dataclass(frozen=True)
class ScreenSessionSnapshot:
    """屏幕暴露会话快照（不可变，供 UI 与引擎消费）。"""

    state: ScreenSessionState
    #: 当前会话内累计的屏幕暴露秒数
    exposure_seconds: float
    #: 当前会话内累计的「疑似离开」秒数（AWAY 期间）
    away_seconds: float
    #: 当前会话开始的单调时刻
    session_started_at: float
    #: 距上次键鼠输入的秒数
    idle_seconds: float
    #: 会话是否已因锁屏/休眠/自然休息而结束
    ended_reason: Optional[str] = None
    #: 当前是否处于「全屏内容消费」（看视频 / 全屏演示 / 全屏应用）。
    #: 为 ``True`` 时启用放宽阈值，避免把「看视频」误判成「离开」。
    fullscreen: bool = False


class ScreenSessionEngine:
    """屏幕暴露会话引擎。

    职责：
    1. 依据 idle 时长与系统锁屏/休眠信号维护三态（ON / AWAY / OFF）
    2. 在 ON 状态下累计屏幕暴露时间
    3. 判定自然休息（idle ≥ natural_rest_threshold）并发布事件
    4. 提供快照给 BlinkEngine / BreakEngine / UI

    不负责：提醒 UI、数据持久化（由上层服务订阅事件后处理）。
    """

    def __init__(
        self,
        clock: Optional[Clock] = None,
        idle_provider: Optional[Callable[[], float]] = None,
        *,
        away_threshold: Optional[float] = None,
        natural_rest_threshold: Optional[float] = None,
        resume_threshold: Optional[float] = None,
        fullscreen_provider: Optional[Callable[[], bool]] = None,
        fullscreen_away_threshold: Optional[float] = None,
        fullscreen_natural_rest_threshold: Optional[float] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """初始化。

        Args:
            clock: 时钟实现，默认 :data:`~app.core.clock.default_clock`。
            idle_provider: 返回距上次输入的秒数；为 None 时视为始终活跃（0）。
            away_threshold: 进入 AWAY 的 idle 秒数，默认
                ``defaults.AWAY_THRESHOLD``。
            natural_rest_threshold: 判定自然休息的 idle 秒数，默认
                ``defaults.NATURAL_REST_THRESHOLD``。
            resume_threshold: 从 AWAY/OFF 恢复到 ON 的 idle 上限，默认
                ``defaults.SESSION_RESUME_THRESHOLD``。
            fullscreen_provider: 返回「当前是否处于全屏内容消费」的可调用
                对象（看视频 / 演示 / 全屏应用）；为 None 时不启用全屏豁免。
            fullscreen_away_threshold: 全屏内容消费期间进入 AWAY 的 idle
                秒数，默认 ``defaults.FULLSCREEN_AWAY_THRESHOLD``。
            fullscreen_natural_rest_threshold: 全屏内容消费期间判定自然
                休息的 idle 秒数，默认
                ``defaults.FULLSCREEN_NATURAL_REST_THRESHOLD``。
            event_bus: 事件总线；为 None 时不发布事件。
        """
        self._clock: Clock = clock or default_clock
        self._idle_provider = idle_provider
        self._away_threshold = float(
            away_threshold
            if away_threshold is not None
            else defaults.AWAY_THRESHOLD
        )
        self._natural_rest_threshold = float(
            natural_rest_threshold
            if natural_rest_threshold is not None
            else defaults.NATURAL_REST_THRESHOLD
        )
        self._resume_threshold = float(
            resume_threshold
            if resume_threshold is not None
            else defaults.SESSION_RESUME_THRESHOLD
        )
        self._fullscreen_provider = fullscreen_provider
        self._fullscreen_away_threshold = float(
            fullscreen_away_threshold
            if fullscreen_away_threshold is not None
            else defaults.FULLSCREEN_AWAY_THRESHOLD
        )
        self._fullscreen_natural_rest_threshold = float(
            fullscreen_natural_rest_threshold
            if fullscreen_natural_rest_threshold is not None
            else defaults.FULLSCREEN_NATURAL_REST_THRESHOLD
        )
        self._bus = event_bus

        self._lock = threading.RLock()

        now = self._clock.now()
        self._state = ScreenSessionState.ON
        self._exposure_seconds = 0.0
        self._away_seconds = 0.0
        self._session_started_at = now
        self._last_tick_at = now
        self._idle_seconds = 0.0
        self._fullscreen = False
        self._locked = False
        self._ended_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def state(self) -> ScreenSessionState:
        """当前会话状态。"""
        with self._lock:
            return self._state

    @property
    def exposure_seconds(self) -> float:
        """当前会话累计屏幕暴露秒数。"""
        with self._lock:
            return self._exposure_seconds

    @property
    def away_seconds(self) -> float:
        """当前会话累计 AWAY 秒数。"""
        with self._lock:
            return self._away_seconds

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """每秒调用一次：推进计时并按需转移状态。

        使用真实时间差（而非固定 +1 秒）累加，避免 tick 迟到/堆积造成漂移。
        """
        with self._lock:
            now = self._clock.now()
            delta = max(0.0, now - self._last_tick_at)
            self._last_tick_at = now

            previous = self._state

            idle = self._read_idle_seconds()
            self._idle_seconds = idle

            # 锁屏期间一律不计暴露，且不进行任何状态推导
            if self._locked:
                self._state = ScreenSessionState.OFF
                self._publish_if_changed(previous)
                return

            self._apply_idle_rules(idle, delta)
            self._publish_if_changed(previous)

    def _read_idle_seconds(self) -> float:
        """读取距上次输入的秒数；读取失败时视为 0（活跃）。"""
        if self._idle_provider is None:
            return 0.0
        try:
            return max(0.0, float(self._idle_provider()))
        except Exception:  # noqa: BLE001
            logger.exception("读取空闲时长失败，按活跃处理")
            return 0.0

    def _read_fullscreen(self) -> bool:
        """读取「是否处于全屏内容消费」；未注入或读取失败时视为 False。"""
        if self._fullscreen_provider is None:
            return False
        try:
            return bool(self._fullscreen_provider())
        except Exception:  # noqa: BLE001
            logger.exception("全屏内容检测失败，视为非全屏")
            return False

    def _apply_idle_rules(self, idle: float, delta: float) -> None:
        """依据 idle 时长推进状态并累加时间。

        全屏内容消费（看视频 / 演示 / 全屏应用）期间改用**放宽阈值**：
        那正是视觉负荷最高的场景，而人恰恰几乎不碰键鼠。若沿用常规阈值，
        「看视频」会被判成「离开」，眨眼提示与休息计时全部停摆。
        """
        fullscreen = self._read_fullscreen()
        if fullscreen != self._fullscreen:
            self._fullscreen = fullscreen
            logger.info(
                "全屏内容消费%s：豁免阈值 %.0fs / %.0fs（常规 %.0fs / %.0fs）",
                "开始" if fullscreen else "结束",
                self._fullscreen_away_threshold,
                self._fullscreen_natural_rest_threshold,
                self._away_threshold,
                self._natural_rest_threshold,
            )

        if fullscreen:
            away_threshold = self._fullscreen_away_threshold
            natural_rest_threshold = self._fullscreen_natural_rest_threshold
        else:
            away_threshold = self._away_threshold
            natural_rest_threshold = self._natural_rest_threshold

        if idle >= natural_rest_threshold:
            # 明确离开：结束会话（重置计时器）
            if self._state is not ScreenSessionState.OFF:
                self._away_seconds += delta
                self._end_session("natural_rest", publish_natural_rest=True)
            return

        if idle >= away_threshold:
            # 疑似离开：暂停累计，但**不重置**——回来接着算
            self._away_seconds += delta
            self._state = ScreenSessionState.AWAY
            return

        # 活跃：恢复 ON 并累计暴露
        if self._state is ScreenSessionState.OFF:
            # 会话已结束（自然休息/锁屏后），重新开始一个新会话
            self._start_new_session()
        elif (
            self._state is ScreenSessionState.AWAY
            and not fullscreen
            and idle > self._resume_threshold
        ):
            # 仍在 AWAY 与 ON 的灰区：保持在 AWAY，不累计暴露。
            # 全屏内容消费时豁免此约束——用户可能刚读完长文档就全屏放视频，
            # 那种情况应当立刻恢复为「在看内容」。
            return
        self._state = ScreenSessionState.ON
        self._exposure_seconds += delta

    def _publish_if_changed(self, previous: ScreenSessionState) -> None:
        """状态变化时发布事件。"""
        if previous is self._state or self._bus is None:
            return
        try:
            self._bus.publish(
                EventType.SCREEN_SESSION_CHANGED,
                {
                    "old_state": previous.value,
                    "new_state": self._state.value,
                    "exposure_seconds": self._exposure_seconds,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 SCREEN_SESSION_CHANGED 失败")

    # ------------------------------------------------------------------
    # 会话生命周期
    # ------------------------------------------------------------------
    def _start_new_session(self) -> None:
        """开启新会话，清零计数。"""
        self._session_started_at = self._clock.now()
        self._exposure_seconds = 0.0
        self._away_seconds = 0.0
        self._ended_reason = None

    def _end_session(self, reason: str, *, publish_natural_rest: bool = False) -> None:
        """结束当前会话。

        Args:
            reason: 结束原因（natural_rest / lock / sleep / app_exit）。
            publish_natural_rest: 是否发布 NATURAL_REST_DETECTED 事件。
        """
        self._state = ScreenSessionState.OFF
        self._ended_reason = reason
        logger.info(
            "屏幕暴露会话结束: reason=%s exposure=%.1fs away=%.1fs",
            reason,
            self._exposure_seconds,
            self._away_seconds,
        )
        if publish_natural_rest and self._bus is not None:
            try:
                self._bus.publish(
                    EventType.NATURAL_REST_DETECTED,
                    {
                        "away_seconds": self._away_seconds,
                        "exposure_seconds": self._exposure_seconds,
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("发布 NATURAL_REST_DETECTED 失败")

    def reset_session(self, reason: str = "manual") -> None:
        """手动重置会话（休息完成、用户重置计时等场景）。

        Args:
            reason: 结束原因，写入快照的 ``ended_reason``。
        """
        with self._lock:
            self._end_session(reason, publish_natural_rest=False)
            self._start_new_session()
            self._state = ScreenSessionState.ON

    # ------------------------------------------------------------------
    # 系统事件
    # ------------------------------------------------------------------
    def on_system_lock(self) -> None:
        """系统锁屏：立即结束会话，不累计暴露。"""
        with self._lock:
            self._locked = True
            self._end_session("lock", publish_natural_rest=False)

    def on_system_unlock(self) -> None:
        """系统解锁：重新开始会话。"""
        with self._lock:
            self._locked = False
            self._start_new_session()
            self._state = ScreenSessionState.ON
            self._last_tick_at = self._clock.now()

    def on_system_sleep(self) -> None:
        """系统休眠：结束会话。"""
        with self._lock:
            self._end_session("sleep", publish_natural_rest=False)

    def on_system_wake(self) -> None:
        """系统唤醒：重新开始会话。"""
        self.on_system_unlock()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_snapshot(self) -> ScreenSessionSnapshot:
        """返回当前会话快照。"""
        with self._lock:
            return ScreenSessionSnapshot(
                state=self._state,
                exposure_seconds=self._exposure_seconds,
                away_seconds=self._away_seconds,
                session_started_at=self._session_started_at,
                idle_seconds=self._idle_seconds,
                ended_reason=self._ended_reason,
                fullscreen=self._fullscreen,
            )

    def is_exposed(self) -> bool:
        """是否处于屏幕暴露状态（ON）。"""
        return self.state is ScreenSessionState.ON

    def update_thresholds(
        self,
        away: Optional[float] = None,
        natural_rest: Optional[float] = None,
        resume: Optional[float] = None,
        fullscreen_away: Optional[float] = None,
        fullscreen_natural_rest: Optional[float] = None,
    ) -> None:
        """热更新阈值（设置页修改后立即生效）。"""
        with self._lock:
            if away is not None:
                self._away_threshold = float(away)
            if natural_rest is not None:
                self._natural_rest_threshold = float(natural_rest)
            if resume is not None:
                self._resume_threshold = float(resume)
            if fullscreen_away is not None:
                self._fullscreen_away_threshold = float(fullscreen_away)
            if fullscreen_natural_rest is not None:
                self._fullscreen_natural_rest_threshold = float(
                    fullscreen_natural_rest
                )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        snap = self.get_snapshot()
        return (
            f"ScreenSessionEngine(state={snap.state.value}, "
            f"exposure={snap.exposure_seconds:.1f}s, "
            f"away={snap.away_seconds:.1f}s, "
            f"fullscreen={snap.fullscreen})"
        )
