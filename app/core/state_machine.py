"""应用状态机（StateMachine）。

EyeRest 的核心模块，管理 9 种应用状态及其转换。80% 的 Bug 会出在状态切换，
因此所有转换路径都有单元测试覆盖，并严格遵守预定义的转换规则。

状态转换图::

    INACTIVE ──start──→ ACTIVE ──warn──→ BREAK_WARNING ──trigger──→ SHORT_BREAK ──done──→ ACTIVE
                          ↑  ↓ idle          ↓ natural              ↓ skip                ↑
                          ←── IDLE ←──────────┘                      └────────────────────┘
                          ↑                                                        ↑
                          └────────── natural_rest (≥120s) ────────────────────────┘
    ACTIVE ──pause──→ PAUSED ──resume──→ ACTIVE
    ACTIVE ──lock──→ LOCKED ──unlock──→ ACTIVE
    ACTIVE ──sleep──→ SLEEP ──wake──→ ACTIVE
    任意状态 ──stop──→ INACTIVE

设计原则：

* 所有状态转换通过 :meth:`transition_to` 统一入口，确保事件发布与历史记录一致
* 非法转换被拒绝并记录日志
* 线程安全，使用 ``threading.RLock`` 保护所有可变状态
* 记录最近 10 条转换历史
* 与 TimerEngine 协同：自然休息 / 休息完成 / 跳过休息时重置 active_seconds；
  暂停 / 锁屏 / 睡眠时暂停计时，恢复时恢复计时
"""

from __future__ import annotations

import threading
import time
from collections import deque
from enum import Enum, auto
from typing import Any, Optional

from app.config import defaults
from app.core.activity_monitor import ActivityState
from app.core.event_bus import EventBus, EventType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class AppState(Enum):
    """应用状态枚举（9 种状态）。"""

    INACTIVE = auto()      # 未激活（应用刚启动/未开始保护）
    ACTIVE = auto()        # 活跃保护中
    IDLE = auto()          # 空闲（60-120秒无输入）
    BREAK_WARNING = auto() # 休息警告（倒计时30秒）
    SHORT_BREAK = auto()   # 短休息中（20秒）
    LONG_BREAK = auto()    # 长休息中（5分钟）
    PAUSED = auto()        # 用户暂停
    LOCKED = auto()        # 系统锁屏
    SLEEP = auto()         # 系统睡眠


# 允许的状态转换表：{ 当前状态: { 可转换到的状态集合 } }
# 注意：同状态转换（self-transition）在 transition_to 中单独处理，始终允许。
_ALLOWED_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.INACTIVE: {
        AppState.ACTIVE,
    },
    AppState.ACTIVE: {
        AppState.IDLE,
        AppState.BREAK_WARNING,
        AppState.PAUSED,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.IDLE: {
        AppState.ACTIVE,
        AppState.PAUSED,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.BREAK_WARNING: {
        AppState.SHORT_BREAK,
        AppState.LONG_BREAK,
        AppState.ACTIVE,
        AppState.PAUSED,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.SHORT_BREAK: {
        AppState.ACTIVE,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.LONG_BREAK: {
        AppState.ACTIVE,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.PAUSED: {
        AppState.ACTIVE,
        AppState.LOCKED,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.LOCKED: {
        AppState.ACTIVE,
        AppState.SLEEP,
        AppState.INACTIVE,
    },
    AppState.SLEEP: {
        AppState.ACTIVE,
        AppState.LOCKED,
        AppState.INACTIVE,
    },
}


class StateMachine:
    """EyeRest 应用状态机。

    管理 9 种应用状态，严格遵守预定义的转换规则。所有转换通过
    :meth:`transition_to` 统一入口，转换时发布 ``STATE_CHANGED`` 事件
    并记录历史（最近 10 条）。

    线程安全：所有可变状态由 ``threading.RLock`` 保护。
    """

    # 转换历史最大条数
    MAX_HISTORY = 10

    def __init__(
        self,
        timer_engine: Any = None,
        event_bus: Optional[EventBus] = None,
        activity_monitor: Any = None,
    ) -> None:
        """初始化状态机。

        Args:
            timer_engine: 计时引擎，用于暂停/恢复/重置 active_seconds。
                为 None 时状态机仍可工作，但不驱动计时引擎。
            event_bus: 事件总线；为 None 时使用全局单例。
            activity_monitor: 活动监控器，用于解锁/唤醒时获取当前空闲秒数。
                为 None 时解锁/唤醒默认不重置 active_seconds（除非显式传入 idle_seconds）。
        """
        self._lock = threading.RLock()
        self._state: AppState = AppState.INACTIVE
        self._timer_engine = timer_engine
        self._bus = event_bus if event_bus is not None else _get_global_bus()
        self._activity_monitor = activity_monitor
        self._history: deque[dict] = deque(maxlen=self.MAX_HISTORY)
        self._break_type: str = "short"  # 当前休息类型

    # ------------------------------------------------------------------
    # 基础接口
    # ------------------------------------------------------------------
    def get_state(self) -> AppState:
        """获取当前状态。"""
        with self._lock:
            return self._state

    def can_transition(self, new_state: AppState) -> bool:
        """检查是否可以转换到目标状态。

        Args:
            new_state: 目标状态。

        Returns:
            是否允许转换。同状态转换始终返回 True（幂等）。
        """
        with self._lock:
            if new_state == self._state:
                return True
            return new_state in _ALLOWED_TRANSITIONS.get(self._state, set())

    def transition_to(self, new_state: AppState, **kwargs: Any) -> bool:
        """尝试状态转换，返回是否成功。

        转换成功时发布 ``STATE_CHANGED`` 事件（payload 含 old_state / new_state /
        reason 及额外 kwargs）并记录历史。

        Args:
            new_state: 目标状态。
            **kwargs: 额外事件数据，通常包含 ``reason`` 转换原因。

        Returns:
            转换是否成功。非法转换返回 False 并记录警告日志。
        """
        with self._lock:
            old_state = self._state
            if not self.can_transition(new_state):
                logger.warning(
                    "非法状态转换: %s -> %s", old_state.name, new_state.name
                )
                return False

            if old_state == new_state:
                # 同状态：幂等返回 True。若提供了 reason（如 natural_rest），
                # 仍发布事件并记录历史，以便订阅者感知重置动作。
                reason = kwargs.get("reason", "")
                if reason:
                    self._publish_state_changed(old_state, new_state, **kwargs)
                    self._record_history(old_state, new_state, **kwargs)
                return True

            self._state = new_state
            self._publish_state_changed(old_state, new_state, **kwargs)
            self._record_history(old_state, new_state, **kwargs)
            logger.info(
                "状态转换: %s -> %s (%s)",
                old_state.name,
                new_state.name,
                kwargs.get("reason", ""),
            )
            return True

    def get_history(self) -> list[dict]:
        """获取最近 10 条状态转换历史的副本。

        Returns:
            转换历史列表，每条包含 old_state / new_state / reason / timestamp 等字段。
        """
        with self._lock:
            return list(self._history)

    @property
    def break_type(self) -> str:
        """获取当前休息类型（``"short"`` 或 ``"long"``）。"""
        with self._lock:
            return self._break_type

    # ------------------------------------------------------------------
    # 保护生命周期
    # ------------------------------------------------------------------
    def start_protection(self) -> bool:
        """开始保护：INACTIVE → ACTIVE。

        转换成功后重置计时引擎（清零 active_seconds）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            result = self.transition_to(AppState.ACTIVE, reason="start_protection")
            if result and self._state == AppState.ACTIVE:
                self._reset_timer()
            return result

    def stop_protection(self) -> bool:
        """停止保护：任意状态 → INACTIVE。

        转换成功后暂停计时引擎。

        Returns:
            转换是否成功。
        """
        with self._lock:
            self._pause_timer()
            return self.transition_to(AppState.INACTIVE, reason="stop_protection")

    # ------------------------------------------------------------------
    # 活动事件处理
    # ------------------------------------------------------------------
    def on_activity_changed(self, activity_state: ActivityState) -> bool:
        """处理活动状态变化事件。

        * ACTIVE 活动状态：若当前为 IDLE 则恢复到 ACTIVE
        * IDLE 活动状态：若当前为 ACTIVE 则进入 IDLE
        * NATURAL_REST：触发 :meth:`on_natural_rest`

        在 LOCKED / SLEEP / PAUSED / BREAK_WARNING / *_BREAK 等非活动主导状态下，
        活动事件被忽略（返回 True 表示已处理，但不转换）。

        Args:
            activity_state: 新的活动状态。

        Returns:
            事件是否被处理（INACTIVE 状态下拒绝返回 False，其余情况返回 True）。
        """
        with self._lock:
            if self._state == AppState.INACTIVE:
                return False

            if activity_state == ActivityState.NATURAL_REST:
                return self.on_natural_rest()

            if activity_state == ActivityState.ACTIVE:
                if self._state == AppState.IDLE:
                    self.transition_to(AppState.ACTIVE, reason="activity_resumed")
                return True

            if activity_state == ActivityState.IDLE:
                if self._state == AppState.ACTIVE:
                    self.transition_to(AppState.IDLE, reason="activity_idle")
                return True

            return False

    def on_natural_rest(self) -> bool:
        """自然休息触发（idle ≥ 120秒）：当前状态 → ACTIVE（重置计数）。

        无论当前是 ACTIVE 还是 IDLE，都转为 ACTIVE 并重置计时引擎。
        在 INACTIVE / PAUSED / LOCKED / SLEEP 状态下不响应自然休息
        （这些状态下用户可能不在电脑前，不应重置工作计数）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            old = self._state
            if old in (
                AppState.INACTIVE,
                AppState.PAUSED,
                AppState.LOCKED,
                AppState.SLEEP,
            ):
                return False

            result = self.transition_to(AppState.ACTIVE, reason="natural_rest")
            if result:
                self._reset_timer()
            return result

    # ------------------------------------------------------------------
    # 休息流程
    # ------------------------------------------------------------------
    def on_break_warning(self) -> bool:
        """休息警告：ACTIVE → BREAK_WARNING。

        仅在 ACTIVE 状态下触发。IDLE 状态不累加 active_seconds，因此不触发警告。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state != AppState.ACTIVE:
                return False
            return self.transition_to(AppState.BREAK_WARNING, reason="break_warning")

    def on_break_triggered(self, break_type: str = "short") -> bool:
        """休息触发：BREAK_WARNING → SHORT_BREAK / LONG_BREAK。

        Args:
            break_type: 休息类型，``"short"`` 或 ``"long"``。
                其他值会被归一化为 ``"short"``。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state != AppState.BREAK_WARNING:
                return False
            normalized_type = "long" if break_type == "long" else "short"
            new_state = (
                AppState.LONG_BREAK
                if normalized_type == "long"
                else AppState.SHORT_BREAK
            )
            self._break_type = normalized_type
            return self.transition_to(
                new_state, reason="break_triggered", break_type=normalized_type
            )

    def on_break_completed(self) -> bool:
        """休息完成：SHORT_BREAK / LONG_BREAK → ACTIVE（重置 active_time）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state not in (AppState.SHORT_BREAK, AppState.LONG_BREAK):
                return False
            result = self.transition_to(AppState.ACTIVE, reason="break_completed")
            if result:
                self._reset_timer()
            return result

    def on_break_skipped(self) -> bool:
        """跳过休息：BREAK_WARNING / SHORT_BREAK / LONG_BREAK → ACTIVE（重置 active_time）。

        支持在警告阶段或休息阶段跳过。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state not in (
                AppState.BREAK_WARNING,
                AppState.SHORT_BREAK,
                AppState.LONG_BREAK,
            ):
                return False
            result = self.transition_to(AppState.ACTIVE, reason="break_skipped")
            if result:
                self._reset_timer()
            return result

    # ------------------------------------------------------------------
    # 暂停 / 恢复
    # ------------------------------------------------------------------
    def pause(self, duration_minutes: Optional[int] = None) -> bool:
        """暂停保护：ACTIVE / IDLE / BREAK_WARNING → PAUSED。

        休息中不可暂停（SHORT_BREAK / LONG_BREAK 被拒绝），符合
        PRD「休息中不暂停」的规则。

        Args:
            duration_minutes: 可选的暂停时长（分钟），仅记录到事件 payload。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state not in (
                AppState.ACTIVE,
                AppState.IDLE,
                AppState.BREAK_WARNING,
            ):
                return False
            result = self.transition_to(
                AppState.PAUSED, reason="pause", duration_minutes=duration_minutes
            )
            if result:
                self._pause_timer()
            return result

    def resume(self) -> bool:
        """恢复保护：PAUSED → ACTIVE。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state != AppState.PAUSED:
                return False
            result = self.transition_to(AppState.ACTIVE, reason="resume")
            if result:
                self._resume_timer()
            return result

    # ------------------------------------------------------------------
    # 系统事件
    # ------------------------------------------------------------------
    def on_system_lock(self) -> bool:
        """系统锁屏：→ LOCKED（暂停计时引擎）。

        从 INACTIVE（未保护）和 LOCKED（已锁屏）状态不响应。
        SLEEP 状态下也允许转换到 LOCKED（唤醒后可能先检测到锁屏）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state in (AppState.INACTIVE, AppState.LOCKED):
                return False
            result = self.transition_to(AppState.LOCKED, reason="system_lock")
            if result:
                self._pause_timer()
            return result

    def on_system_unlock(self, idle_seconds: Optional[float] = None) -> bool:
        """系统解锁：LOCKED → 根据空闲判断（≥120s 重置，<120s → ACTIVE）。

        Args:
            idle_seconds: 当前空闲秒数。为 None 时尝试从 activity_monitor 获取。
                无法获取时默认不重置 active_seconds（保守策略）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state != AppState.LOCKED:
                return False
            if idle_seconds is None:
                idle_seconds = self._get_idle_seconds()

            result = self.transition_to(
                AppState.ACTIVE,
                reason="system_unlock",
                idle_seconds=idle_seconds,
            )
            if result:
                if (
                    idle_seconds is not None
                    and idle_seconds >= defaults.NATURAL_REST_THRESHOLD
                ):
                    self._reset_timer()
                self._resume_timer()
            return result

    def on_system_sleep(self) -> bool:
        """系统睡眠：→ SLEEP（暂停计时引擎）。

        从 INACTIVE（未保护）和 SLEEP（已睡眠）状态不响应。
        LOCKED 状态下也允许转换到 SLEEP（锁屏后系统进入睡眠）。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state in (AppState.INACTIVE, AppState.SLEEP):
                return False
            result = self.transition_to(AppState.SLEEP, reason="system_sleep")
            if result:
                self._pause_timer()
            return result

    def on_system_wake(self, idle_seconds: Optional[float] = None) -> bool:
        """系统唤醒：SLEEP → 根据空闲判断（≥120s 重置，<120s → ACTIVE）。

        Args:
            idle_seconds: 当前空闲秒数。为 None 时尝试从 activity_monitor 获取。

        Returns:
            转换是否成功。
        """
        with self._lock:
            if self._state != AppState.SLEEP:
                return False
            if idle_seconds is None:
                idle_seconds = self._get_idle_seconds()

            result = self.transition_to(
                AppState.ACTIVE,
                reason="system_wake",
                idle_seconds=idle_seconds,
            )
            if result:
                if (
                    idle_seconds is not None
                    and idle_seconds >= defaults.NATURAL_REST_THRESHOLD
                ):
                    self._reset_timer()
                self._resume_timer()
            return result

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _publish_state_changed(
        self, old_state: AppState, new_state: AppState, **kwargs: Any
    ) -> None:
        """发布 STATE_CHANGED 事件（容错，不抛出异常）。"""
        if self._bus is None:
            return
        payload = {
            "old_state": old_state.name,
            "new_state": new_state.name,
            "reason": kwargs.get("reason", ""),
            **kwargs,
        }
        try:
            self._bus.publish(EventType.STATE_CHANGED, payload)
        except Exception:  # noqa: BLE001
            logger.exception("发布 STATE_CHANGED 事件失败")

    def _record_history(
        self, old_state: AppState, new_state: AppState, **kwargs: Any
    ) -> None:
        """记录转换历史到 deque（自动保留最近 MAX_HISTORY 条）。"""
        record = {
            "old_state": old_state.name,
            "new_state": new_state.name,
            "reason": kwargs.get("reason", ""),
            "timestamp": time.monotonic(),
        }
        # 附加额外关键字段（避免覆盖核心字段）
        for key, value in kwargs.items():
            if key not in record:
                record[key] = value
        self._history.append(record)

    def _reset_timer(self) -> None:
        """重置计时引擎（容错）。"""
        if self._timer_engine is None:
            return
        try:
            self._timer_engine.reset()
        except Exception:  # noqa: BLE001
            logger.exception("重置 TimerEngine 失败")

    def _pause_timer(self) -> None:
        """暂停计时引擎（容错）。"""
        if self._timer_engine is None:
            return
        try:
            self._timer_engine.pause()
        except Exception:  # noqa: BLE001
            logger.exception("暂停 TimerEngine 失败")

    def _resume_timer(self) -> None:
        """恢复计时引擎（容错）。"""
        if self._timer_engine is None:
            return
        try:
            self._timer_engine.resume()
        except Exception:  # noqa: BLE001
            logger.exception("恢复 TimerEngine 失败")

    def _get_idle_seconds(self) -> Optional[float]:
        """从 activity_monitor 获取当前空闲秒数（容错）。"""
        if self._activity_monitor is None:
            return None
        try:
            return float(self._activity_monitor.get_idle_seconds())
        except Exception:  # noqa: BLE001
            logger.exception("获取空闲秒数失败")
            return None


def _get_global_bus() -> Optional[EventBus]:
    """获取全局事件总线单例（容错，导入失败时返回 None）。"""
    try:
        from app.core.event_bus import get_event_bus

        return get_event_bus()
    except Exception:  # noqa: BLE001
        return None
