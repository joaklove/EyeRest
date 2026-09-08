"""活动监控。

结合 :mod:`app.windows.idle_detector` 提供的空闲秒数，将用户活动划分为三种状态：

* :attr:`ActivityState.ACTIVE` — 空闲 <= idle_threshold（默认 60 秒）
* :attr:`ActivityState.IDLE` — idle_threshold < 空闲 < natural_rest_threshold（默认 120 秒）
* :attr:`ActivityState.NATURAL_REST` — 空闲 >= natural_rest_threshold（默认 120 秒），自然休息

状态变化时通过 :class:`app.core.event_bus.EventBus` 发布
``ActivityChanged`` 事件，事件数据包含::

    {
        "old_state": "active" | "idle" | "natural_rest" | None,
        "new_state": "active" | "idle" | "natural_rest",
        "idle_seconds": float,
        "is_natural_rest": bool,
    }
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Callable, Optional

from app.config import defaults
from app.core.event_bus import EventBus, EventType, get_event_bus
from app.utils.logger import get_logger
from app.windows import idle_detector as _idle_detector

logger = get_logger(__name__)


class ActivityState(Enum):
    """用户活动状态。"""

    ACTIVE = auto()
    IDLE = auto()
    NATURAL_REST = auto()  # 自然休息（空闲 >= natural_rest_threshold）

    @property
    def value_str(self) -> str:
        """返回小写字符串表示，便于事件序列化。"""
        return self.name.lower()


class ActivityMonitor:
    """活动监控器。

    通过后台线程以固定间隔（默认 1 秒）轮询空闲时长，在状态变化时发布事件。
    线程安全，可在多线程环境下使用。
    """

    # 默认轮询间隔（秒），避免高频轮询
    DEFAULT_POLL_INTERVAL = 1.0

    def __init__(
        self,
        idle_threshold: float = defaults.IDLE_THRESHOLD,
        natural_rest_threshold: float = defaults.NATURAL_REST_THRESHOLD,
        event_bus: Optional[EventBus] = None,
        idle_provider: Callable[[], float] = _idle_detector.get_idle_seconds,
    ) -> None:
        """初始化活动监控器。

        Args:
            idle_threshold: 判定为 IDLE 的空闲秒数阈值（不含）。
            natural_rest_threshold: 判定为自然休息的空闲秒数阈值（含）。
            event_bus: 事件总线；为 None 时使用全局单例。
            idle_provider: 返回当前空闲秒数的可调用对象，便于测试替换。
        """
        if natural_rest_threshold < idle_threshold:
            raise ValueError(
                f"natural_rest_threshold({natural_rest_threshold}) "
                f"不能小于 idle_threshold({idle_threshold})"
            )
        if idle_threshold < 0 or natural_rest_threshold < 0:
            raise ValueError("阈值不能为负数")

        self._idle_threshold = float(idle_threshold)
        self._natural_rest_threshold = float(natural_rest_threshold)
        self._bus = event_bus if event_bus is not None else get_event_bus()
        self._idle_provider = idle_provider

        # 状态与线程控制
        self._lock = threading.RLock()
        self._current_state: Optional[ActivityState] = None
        self._current_idle: float = 0.0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 公共查询接口
    # ------------------------------------------------------------------
    def get_idle_seconds(self) -> float:
        """获取当前空闲秒数。"""
        return self._idle_provider()

    def get_state(self) -> ActivityState:
        """根据当前空闲秒数判断活动状态（不触发事件发布）。"""
        idle = self.get_idle_seconds()
        return self._state_from_idle(idle)

    def get_current_state(self) -> ActivityState:
        """获取最近一次轮询得到的活动状态。

        若尚未开始轮询，会立即采样一次并返回。
        """
        with self._lock:
            if self._current_state is None:
                idle = self._idle_provider()
                self._current_state = self._state_from_idle(idle)
                self._current_idle = idle
            return self._current_state

    def get_current_idle_seconds(self) -> float:
        """获取最近一次轮询得到的空闲秒数。"""
        with self._lock:
            return self._current_idle

    def is_active(self) -> bool:
        """是否处于活跃状态（空闲 <= idle_threshold）。"""
        return self.get_state() == ActivityState.ACTIVE

    def is_running(self) -> bool:
        """轮询线程是否正在运行。"""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # 阈值
    # ------------------------------------------------------------------
    @property
    def idle_threshold(self) -> float:
        return self._idle_threshold

    @property
    def natural_rest_threshold(self) -> float:
        return self._natural_rest_threshold

    def update_thresholds(
        self,
        idle_threshold: float,
        natural_rest_threshold: float,
    ) -> None:
        """运行时热更新阈值（线程安全）。

        供设置页保存后即时生效。参数非法时记录日志并忽略更新，保持原阈值有效；
        若 ``natural_rest_threshold`` 小于 ``idle_threshold``，自动将其抬升到
        ``idle_threshold`` 以维持判定不变量（避免 IDLE 区间退化）。

        Args:
            idle_threshold: 判定为 IDLE 的空闲秒数阈值（不含）。
            natural_rest_threshold: 判定为自然休息的空闲秒数阈值（含）。
        """
        try:
            idle = float(idle_threshold)
            natural = float(natural_rest_threshold)
        except (TypeError, ValueError):
            logger.warning(
                "阈值热更新被忽略：参数非法 idle=%r natural=%r",
                idle_threshold,
                natural_rest_threshold,
            )
            return
        if idle < 0 or natural < 0:
            logger.warning(
                "阈值热更新被忽略：阈值不能为负 idle=%.1f natural=%.1f", idle, natural
            )
            return
        if natural < idle:
            logger.warning(
                "natural_rest_threshold(%.1f) 小于 idle_threshold(%.1f)，已抬升到 %.1f",
                natural,
                idle,
                idle,
            )
            natural = idle
        with self._lock:
            self._idle_threshold = idle
            self._natural_rest_threshold = natural
        logger.info(
            "ActivityMonitor 阈值已热更新 idle_threshold=%.0fs natural_rest_threshold=%.0fs",
            idle,
            natural,
        )

    # ------------------------------------------------------------------
    # 轮询控制
    # ------------------------------------------------------------------
    def start_polling(self, interval: float = DEFAULT_POLL_INTERVAL) -> None:
        """启动后台轮询线程。

        Args:
            interval: 轮询间隔（秒），默认 1 秒。最小不低于 0.1 秒以避免高频轮询。

        若已在运行则忽略。
        """
        if interval < 0.1:
            interval = 0.1  # 防御性下限，避免高频轮询

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("ActivityMonitor 轮询已在运行，忽略 start_polling")
                return

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                args=(interval,),
                name="eyerest-activity-monitor",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "ActivityMonitor 轮询已启动 interval=%.2fs idle_threshold=%.0fs natural_rest_threshold=%.0fs",
                interval,
                self._idle_threshold,
                self._natural_rest_threshold,
            )

    def stop_polling(self, timeout: float = 2.0) -> None:
        """停止轮询线程（幂等）。

        Args:
            timeout: 等待线程退出的最长秒数。
        """
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = None
                return
            self._stop_event.set()
            thread = self._thread

        # 在锁外 join，避免死锁
        thread.join(timeout=timeout)

        with self._lock:
            if self._thread is thread:
                if thread.is_alive():
                    logger.warning("ActivityMonitor 轮询线程在 %.1fs 内未退出", timeout)
                self._thread = None
        logger.info("ActivityMonitor 轮询已停止")

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------
    def _state_from_idle(self, idle_seconds: float) -> ActivityState:
        """根据空闲秒数推导活动状态。

        边界规则（与 PRD 一致）：

        * 空闲 <= idle_threshold -> ACTIVE
        * idle_threshold < 空闲 < natural_rest_threshold -> IDLE
        * 空闲 >= natural_rest_threshold -> NATURAL_REST
        """
        if idle_seconds <= self._idle_threshold:
            return ActivityState.ACTIVE
        if idle_seconds >= self._natural_rest_threshold:
            return ActivityState.NATURAL_REST
        return ActivityState.IDLE

    def _poll_loop(self, interval: float) -> None:
        """轮询线程主循环。"""
        # 启动后立即采样一次，避免首个状态被延迟一个 interval
        self._check_and_notify()
        while not self._stop_event.wait(interval):
            try:
                self._check_and_notify()
            except Exception:  # noqa: BLE001
                # 轮询循环内任何异常都不应杀死线程
                logger.exception("ActivityMonitor 轮询异常")

    def _check_and_notify(self) -> None:
        """采样当前空闲状态，若发生变化则发布事件。"""
        idle = self._idle_provider()
        new_state = self._state_from_idle(idle)

        with self._lock:
            old_state = self._current_state
            self._current_state = new_state
            self._current_idle = idle

        if old_state is new_state:
            return

        payload = {
            "old_state": old_state.value_str if old_state is not None else None,
            "new_state": new_state.value_str,
            "idle_seconds": idle,
            "is_natural_rest": new_state is ActivityState.NATURAL_REST,
        }
        logger.info(
            "活动状态变化: %s -> %s (idle=%.1fs)",
            old_state.value_str if old_state is not None else "none",
            new_state.value_str,
            idle,
        )
        self._bus.publish(EventType.ACTIVITY_CHANGED, payload)
