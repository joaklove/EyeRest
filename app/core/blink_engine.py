"""眨眼提示引擎（Blink Engine）—— 四层护眼节奏的第一层。

## 产品定位（V1.0 正式版）

持续盯屏时人会不自觉减少**完整眨眼**次数，导致泪膜蒸发、眼表干燥。
本引擎做的是 **Blink Cue（眨眼提示）**，不是 **Blink Detection（眨眼检测）**：

- 无摄像头，不检测用户真实眨眼频率
- 不做医疗判断，不承诺算法准确性
- 只是用低打扰的视觉 Cue，帮助用户恢复"完整眨眼"的意识与节奏

## Blink Cycle：60 秒 = 一个节奏周期

V1.0 正式版不再"每 60 秒提醒一次"——那只是每分钟一次提醒，
无法形成一分钟内的眨眼节奏。正式模型：

```text
一个周期 = 60 秒（BLINK_CYCLE_SECONDS）
Cue 间隔 = 10 秒（BLINK_CUE_INTERVAL）
每周期   = 5 次 Cue

00s ───────────────────────── 60s
     10   20   30   40   50
      👁    👁    👁    👁    👁
```

一次 Cue 不是要求用户"点击确认"，而是让眼睛自然完成 1~2 次完整眨眼。

## 文字与动画交替

为对抗视觉习惯化，同一个周期内 Cue 交替出现：

```text
第 1/3/5 次：👁 + "眨眨眼"（文字传递意义）
第 2/4 次  ：👁 仅动画（动画维持节奏）
```

## 计时基准

基于 :class:`ScreenSessionEngine` 的**屏幕暴露时间**，而不是墙钟：

```text
暴露中（ON）    → 累计并按节奏发 Cue
疑似离开（AWAY）→ 暂停（回来接着算，不重置）
会话结束（OFF）  → 重置当前周期
```

## 数据诚实原则

V1 没有摄像头，因此**不发布**"用户完成了眨眼"这类无法证实的事件。
统计口径为：``cycle_started`` / ``cue_shown`` / ``cycle_finished``，
UI 上展示"今天眨眼提示 N 次"，而不是"你眨了 N 次眼"。
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


class BlinkEngine:
    """眨眼节奏引擎（Blink Cycle 模型）。

    职责单一：在屏幕暴露时间内按 ``cue_interval`` 节奏发布 :attr:`BLINK_CUE`，
    并按 ``cycle_seconds`` 维护周期边界（发布 cycle_started / cycle_finished）。
    **不负责任何 UI**，也不判断用户是否真的眨了眼。
    """

    def __init__(
        self,
        clock: Optional[Clock] = None,
        session_engine: Optional[ScreenSessionEngine] = None,
        *,
        cycle_seconds: Optional[float] = None,
        cue_interval: Optional[float] = None,
        cue_duration: Optional[float] = None,
        enabled: bool = True,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """初始化。

        Args:
            clock: 时钟实现。
            session_engine: 屏幕暴露会话引擎，提供计时基准。
            cycle_seconds: Blink Cycle 长度（秒），默认 60。
            cue_interval: 周期内 Cue 间隔（秒），默认 10。
            cue_duration: 单次 Cue 建议展示时长（秒）。
            enabled: 是否启用。
            event_bus: 事件总线。
        """
        self._clock: Clock = clock or default_clock
        self._session = session_engine
        self._cycle_seconds = float(
            cycle_seconds if cycle_seconds is not None else defaults.BLINK_CYCLE_SECONDS
        )
        self._cue_interval = float(
            cue_interval if cue_interval is not None else defaults.BLINK_CUE_INTERVAL
        )
        self._cue_duration = float(
            cue_duration if cue_duration is not None else defaults.BLINK_CUE_DURATION
        )
        self._enabled = bool(enabled)
        self._bus = event_bus

        self._lock = threading.RLock()
        #: 当前周期起点对应的暴露秒数
        self._cycle_start_exposure = 0.0
        #: 上次 Cue 时的暴露秒数（下一次 Cue 的基准线）
        self._last_cue_exposure = 0.0
        #: 当前周期内已发出的 Cue 数（0-based；0/2/4 带文字，1/3 仅动画）
        self._cue_index_in_cycle = 0

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def cycle_seconds(self) -> float:
        """Blink Cycle 长度（秒）。"""
        return self._cycle_seconds

    @property
    def cue_interval(self) -> float:
        """周期内 Cue 间隔（秒）。"""
        return self._cue_interval

    @property
    def cue_duration(self) -> float:
        """单次 Cue 建议展示时长（秒）。"""
        return self._cue_duration

    @property
    def interval(self) -> float:
        """兼容别名：Cue 间隔（Dashboard 进度条用）。"""
        return self._cue_interval

    @property
    def is_enabled(self) -> bool:
        """是否启用眨眼提示。"""
        return self._enabled

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def tick(self) -> None:
        """每秒调用一次：按暴露时间推进周期边界与 Cue 节奏。

        仅在屏幕暴露状态（ON）下累计；AWAY 时暂停（不重置），
        OFF 时随会话重置当前周期。
        """
        if not self._enabled or self._session is None:
            return

        with self._lock:
            snap = self._session.get_snapshot()

            if snap.state is ScreenSessionState.OFF:
                # 会话结束（自然休息/锁屏/休眠）：重置当前周期
                if self._cycle_start_exposure != 0.0 or self._last_cue_exposure != 0.0:
                    self._reset_cycle(0.0)
                return

            if snap.state is not ScreenSessionState.ON:
                # AWAY：暂停计时，回来接着算
                return

            exposure = snap.exposure_seconds

            # 周期边界：可能一次跨过多个周期（理论上不会，防御性处理）
            while exposure - self._cycle_start_exposure >= self._cycle_seconds:
                self._publish_cycle_finished()
                self._reset_cycle(self._cycle_start_exposure + self._cycle_seconds)
                self._publish_cycle_started()

            # Cue 节奏：暴露时间每过 cue_interval 发一次
            if exposure - self._last_cue_exposure >= self._cue_interval:
                self._trigger_cue(exposure)

    def _trigger_cue(self, exposure: float) -> None:
        """发布一次眨眼 Cue。"""
        index = self._cue_index_in_cycle
        # 第 1/3/5 次（0/2/4）带文字；第 2/4 次仅动画
        with_text = index % 2 == 0
        self._last_cue_exposure = exposure
        self._cue_index_in_cycle += 1

        logger.debug(
            "眨眼 Cue: cycle_index=%d with_text=%s exposure=%.1fs",
            index,
            with_text,
            exposure,
        )

        if self._bus is None:
            return
        try:
            self._bus.publish(
                EventType.BLINK_CUE,
                {
                    "kind": "blink",
                    "index": index,
                    "with_text": with_text,
                    "duration": self._cue_duration,
                    "exposure_seconds": exposure,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 BLINK_CUE 失败")

    def _publish_cycle_started(self) -> None:
        """发布周期开始事件。"""
        if self._bus is None:
            return
        try:
            self._bus.publish(
                EventType.BLINK_CYCLE_STARTED,
                {"cycle_start_exposure": self._cycle_start_exposure},
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 BLINK_CYCLE_STARTED 失败")

    def _publish_cycle_finished(self) -> None:
        """发布周期结束事件（含本周期 Cue 次数）。"""
        if self._bus is None:
            return
        try:
            self._bus.publish(
                EventType.BLINK_CYCLE_FINISHED,
                {"cue_count": self._cue_index_in_cycle},
            )
        except Exception:  # noqa: BLE001
            logger.exception("发布 BLINK_CYCLE_FINISHED 失败")

    def _reset_cycle(self, cycle_start_exposure: float) -> None:
        """重置周期状态（调用方需持锁）。"""
        self._cycle_start_exposure = cycle_start_exposure
        self._last_cue_exposure = cycle_start_exposure
        self._cue_index_in_cycle = 0

    # ------------------------------------------------------------------
    # 查询与控制
    # ------------------------------------------------------------------
    def seconds_until_next_cue(self) -> float:
        """距离下一次眨眼 Cue 还有多少秒。

        - ON：正常倒计时
        - AWAY：返回**冻结**的剩余时间（回来继续，不重置）
        - OFF：会话已结束/重置，返回完整间隔

        Returns:
            剩余秒数，不为负。
        """
        if self._session is None:
            return self._cue_interval
        snap = self._session.get_snapshot()
        if snap.state is ScreenSessionState.OFF:
            return self._cue_interval
        # AWAY 时 exposure_seconds 停止增长，剩余时间自然冻结
        remaining = self._cue_interval - (snap.exposure_seconds - self._last_cue_exposure)
        return max(0.0, remaining)

    def seconds_until_cycle_end(self) -> float:
        """距离当前 Blink Cycle 结束还有多少秒（Dashboard 展示用）。"""
        if self._session is None:
            return self._cycle_seconds
        snap = self._session.get_snapshot()
        if snap.state is ScreenSessionState.OFF:
            return self._cycle_seconds
        remaining = self._cycle_seconds - (
            snap.exposure_seconds - self._cycle_start_exposure
        )
        return max(0.0, remaining)

    def reset(self) -> None:
        """重置当前周期（休息完成或用户手动重置后调用）。"""
        with self._lock:
            base = 0.0
            if self._session is not None:
                try:
                    base = float(self._session.exposure_seconds)
                except Exception:  # noqa: BLE001
                    base = 0.0
            self._reset_cycle(base)

    def update_settings(
        self,
        cycle_seconds: Optional[float] = None,
        cue_interval: Optional[float] = None,
        cue_duration: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """热更新设置（设置页修改后立即生效）。

        周期参数变化时重置当前周期，从下一个整周期开始按新节奏运行。
        """
        with self._lock:
            changed = False
            if cycle_seconds is not None and cycle_seconds > 0:
                self._cycle_seconds = float(cycle_seconds)
                changed = True
            if cue_interval is not None and cue_interval > 0:
                self._cue_interval = float(cue_interval)
                changed = True
            if cue_duration is not None and cue_duration > 0:
                self._cue_duration = float(cue_duration)
            if enabled is not None:
                self._enabled = bool(enabled)
                changed = changed or not self._enabled
            if changed:
                base = 0.0
                if self._session is not None:
                    try:
                        base = float(self._session.exposure_seconds)
                    except Exception:  # noqa: BLE001
                        base = 0.0
                self._reset_cycle(base)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return (
            f"BlinkEngine(cycle={self._cycle_seconds:.0f}s, "
            f"cue_interval={self._cue_interval:.0f}s, enabled={self._enabled})"
        )
