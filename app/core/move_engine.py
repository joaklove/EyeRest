"""活动提醒引擎（Move Engine）—— 四层护眼节奏的第三层。

## 产品定位

WHO 的建议是**减少久坐、用身体活动替代久坐**，并不存在
"坐满 X 分钟必须起身"的医学硬阈值。因此 45 分钟只是 EyeRest
的**默认建议值**，用户可以自由调整（30/60/90/120 分钟均可）。

Move 提醒的语义是：

> "差不多该起来动一动了。"

而不是"45 分钟之后必须离开电脑"。

## 计时基准

与 Blink / Look Away 一致，基于 :class:`ScreenSessionEngine` 的
**屏幕暴露时间**——键鼠空闲不中断计时（看视频 45 分钟同样需要活动）。

## 重置时机

* 用户完成活动（点击提示上的"活动完成"或自行起身回来）
* 深度休息完成（离开屏幕本身就是活动）
* 自然休息（离开超过 natural_rest_threshold，人已经离开屏幕）
* 锁屏 / 休眠（会话结束）

注意：20 秒远眺（Look Away）**不会**重置 Move 计时——
看远处不是身体活动。

## 提醒策略

Cue 发出后如果用户没有响应，每 60 秒温和地重新提示一次，
直到用户完成活动或计时被重置。不做全屏打断，不抢焦点。
"""

from __future__ import annotations

import threading
from typing import Optional

from app.config import defaults
from app.core.clock import Clock, default_clock
from app.core.event_bus import EventBus, EventType
from app.core.screen_session import ScreenSessionEngine, ScreenSessionState
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: Cue 未响应时的重复提醒间隔（秒）
RE_CUE_INTERVAL = 60.0


class MoveEngine:
    """活动提醒引擎（Move）。

    职责单一：在屏幕暴露时间累计达到 ``interval`` 时发布 :attr:`MOVE_CUE`，
    并在活动完成 / 深度休息 / 自然休息后重置。**不负责任何 UI**。
    """

    def __init__(
        self,
        clock: Optional[Clock] = None,
        session_engine: Optional[ScreenSessionEngine] = None,
        *,
        interval: Optional[float] = None,
        duration: Optional[float] = None,
        enabled: bool = True,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """初始化。

        Args:
            clock: 时钟实现。
            session_engine: 屏幕暴露会话引擎，提供计时基准。
            interval: 活动提醒间隔（秒），默认 45 分钟。
            duration: 建议活动时长（秒），默认 3 分钟。
            enabled: 是否启用。
            event_bus: 事件总线。
        """
        self._clock: Clock = clock or default_clock
        self._session = session_engine
        self._interval = float(
            interval if interval is not None else defaults.MOVE_INTERVAL
        )
        self._duration = float(
            duration if duration is not None else defaults.MOVE_DURATION
        )
        self._enabled = bool(enabled)
        self._bus = event_bus

        self._lock = threading.RLock()
        #: 已累计的暴露秒数（自上次活动完成以来）
        self._accum = 0.0
        #: 上次读取到的暴露秒数（用于计算增量）
        self._last_exposure = 0.0
        #: Cue 是否待响应
        self._cue_active = False
        #: 上次发 Cue 时的 clock 时间（用于重复提醒节奏）
        self._last_cue_at = 0.0

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def interval(self) -> float:
        """活动提醒间隔（秒）。"""
        return self._interval

    @property
    def duration(self) -> float:
        """建议活动时长（秒）。"""
        return self._duration

    @property
    def is_enabled(self) -> bool:
        """是否启用活动提醒。"""
        return self._enabled

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """每秒调用一次：累计暴露时间并按需发布活动提醒。"""
        if not self._enabled or self._session is None:
            return

        with self._lock:
            snap = self._session.get_snapshot()
            exposure = snap.exposure_seconds
            delta = exposure - self._last_exposure
            self._last_exposure = exposure

            if snap.state is ScreenSessionState.OFF:
                # 会话结束：人已经离开屏幕（自然休息/锁屏/休眠），重置计时
                if self._accum != 0.0 or self._cue_active:
                    self.reset()
                return

            if delta > 0:
                self._accum += delta

            if not self._cue_active:
                if self._accum >= self._interval:
                    self._trigger_cue()
                return

            # Cue 已发出但未响应：每 RE_CUE_INTERVAL 温和重复一次
            if self._clock.now() - self._last_cue_at >= RE_CUE_INTERVAL:
                self._trigger_cue()

    def _trigger_cue(self) -> None:
        """发布一次活动提醒。"""
        self._cue_active = True
        self._last_cue_at = self._clock.now()
        logger.debug(
            "活动提醒: accum=%.0fs interval=%.0fs overdue=%.0fs",
            self._accum,
            self._interval,
            self._accum - self._interval,
        )
        if self._bus is None:
            return
        try:
            self._bus.publish(
                EventType.MOVE_CUE,
                {
                    "kind": "move",
                    "duration": self._duration,
                    "accum_seconds": self._accum,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 MOVE_CUE 失败")

    # ------------------------------------------------------------------
    # 用户响应
    # ------------------------------------------------------------------
    def complete_activity(self) -> None:
        """用户完成活动：重置计时，进入下一个周期。"""
        with self._lock:
            self.reset()
            logger.debug("活动已完成，Move 计时重置")

    # ------------------------------------------------------------------
    # 查询与控制
    # ------------------------------------------------------------------
    def get_remaining_seconds(self) -> float:
        """距离下一次活动提醒还有多少秒（不会小于 0）。"""
        with self._lock:
            return max(0.0, self._interval - self._accum)

    def is_cue_active(self) -> bool:
        """当前是否有待响应的活动提醒。"""
        with self._lock:
            return self._cue_active

    def reset(self) -> None:
        """重置计时（活动完成 / 深度休息完成 / 手动重置后调用）。"""
        with self._lock:
            self._accum = 0.0
            self._cue_active = False
            self._last_cue_at = 0.0
            if self._session is not None:
                try:
                    self._last_exposure = float(self._session.exposure_seconds)
                except Exception:  # noqa: BLE001
                    self._last_exposure = 0.0

    def update_settings(
        self,
        interval: Optional[float] = None,
        duration: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """热更新设置（设置页修改后立即生效）。"""
        with self._lock:
            if interval is not None and interval > 0:
                self._interval = float(interval)
            if duration is not None and duration > 0:
                self._duration = float(duration)
            if enabled is not None:
                self._enabled = bool(enabled)
                if not self._enabled:
                    self._cue_active = False

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"MoveEngine(interval={self._interval / 60:.0f}min, "
            f"accum={self._accum / 60:.1f}min, enabled={self._enabled})"
        )
