"""计时引擎。

驱动用眼时间的累计与统计，核心原则：

* **Active Time（有效用眼时间）≠ App Running Time（应用运行时间）**
* 仅在 :attr:`ActivityState.ACTIVE` 状态下累加 ``active_seconds``
* :attr:`ActivityState.IDLE` 状态不累加 active，仅累计 idle
* :attr:`ActivityState.NATURAL_REST` 状态重置 ``active_seconds = 0``，并累计 idle

引擎通过后台线程以固定间隔（默认 1 秒）调用
:meth:`app.core.activity_monitor.ActivityMonitor.get_state` 判断当前状态，
线程安全，可在多线程环境下使用。

用法::

    from app.core.timer_engine import TimerEngine

    engine = TimerEngine(activity_monitor, event_bus=bus)
    engine.start()
    # ...
    stats = engine.get_stats()
    engine.stop()
"""

from __future__ import annotations

import threading
from typing import Optional

from app.core.activity_monitor import ActivityMonitor, ActivityState
from app.core.event_bus import EventBus, EventType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TimerEngine:
    """计时引擎。

    通过后台线程每秒采样活动状态，分别累计 active / idle / app_uptime 三类时间。
    使用 ``threading.RLock`` 保护所有可变状态，线程安全。
    """

    # 默认 tick 间隔（秒）
    DEFAULT_TICK_INTERVAL = 1.0

    def __init__(
        self,
        activity_monitor: ActivityMonitor,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """初始化计时引擎。

        Args:
            activity_monitor: 活动监控器，用于每秒获取当前活动状态。
            event_bus: 事件总线；为 None 时不发布事件。
        """
        self._monitor = activity_monitor
        self._bus = event_bus

        # 统计数据
        self._lock = threading.RLock()
        self._active_seconds: float = 0.0
        self._idle_seconds_total: float = 0.0
        self._app_uptime: float = 0.0

        # 运行控制
        self._running: bool = False
        self._paused: bool = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 生命周期控制
    # ------------------------------------------------------------------
    def start(self, interval: float = DEFAULT_TICK_INTERVAL) -> None:
        """启动计时引擎（开始后台 tick 累加）。

        Args:
            interval: tick 间隔（秒），默认 1.0 秒。最小不低于 0.1 秒。

        若已在运行则忽略。
        """
        if interval < 0.1:
            interval = 0.1  # 防御性下限

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("TimerEngine 已在运行，忽略 start")
                return

            self._stop_event.clear()
            self._running = True
            self._paused = False
            self._thread = threading.Thread(
                target=self._tick_loop,
                args=(interval,),
                name="eyerest-timer-engine",
                daemon=True,
            )
            self._thread.start()
            logger.info("TimerEngine 已启动 interval=%.2fs", interval)

    def stop(self, timeout: float = 2.0) -> None:
        """停止计时引擎（幂等）。

        Args:
            timeout: 等待后台线程退出的最长秒数。
        """
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._running = False
                return
            self._stop_event.set()
            self._running = False
            thread = self._thread

        # 在锁外 join，避免死锁
        thread.join(timeout=timeout)

        with self._lock:
            if self._thread is thread:
                if thread.is_alive():
                    logger.warning("TimerEngine 线程在 %.1fs 内未退出", timeout)
                self._thread = None
        logger.info("TimerEngine 已停止")

    def pause(self) -> None:
        """暂停计时（暂停 active / idle 累加，app_uptime 仍继续）。

        暂停期间 NATURAL_REST 不会重置 active_seconds。
        """
        with self._lock:
            was_paused = self._paused
            self._paused = True
        if not was_paused:
            logger.info("TimerEngine 已暂停")
            if self._bus is not None:
                self._bus.publish(EventType.APP_PAUSE, {"source": "timer_engine"})

    def resume(self) -> None:
        """恢复计时。"""
        with self._lock:
            was_paused = self._paused
            self._paused = False
        if was_paused:
            logger.info("TimerEngine 已恢复")
            if self._bus is not None:
                self._bus.publish(EventType.APP_RESUME, {"source": "timer_engine"})

    def reset(self) -> None:
        """重置 active_seconds 为 0（不影响 idle 和 app_uptime）。"""
        with self._lock:
            self._active_seconds = 0.0
        logger.info("TimerEngine active_seconds 已重置")

    # ------------------------------------------------------------------
    # tick 逻辑
    # ------------------------------------------------------------------
    def tick(self, delta_seconds: float = 1.0) -> None:
        """手动推进一个 tick（便于测试）。

        根据当前活动状态更新统计：

        * ACTIVE -> ``active_seconds += delta``
        * IDLE -> ``idle_seconds_total += delta``
        * NATURAL_REST -> ``idle_seconds_total += delta`` 且 ``active_seconds = 0``

        无论何种状态，``app_uptime`` 始终累加。暂停时不累加 active / idle。

        Args:
            delta_seconds: 本次推进的秒数，默认 1.0。
        """
        if delta_seconds <= 0:
            return

        # app_uptime 始终累加（应用运行时间不受 pause / 活动状态影响）
        with self._lock:
            self._app_uptime += delta_seconds
            if self._paused:
                return

        # 在锁外获取状态，避免长时间持锁（idle 检测可能涉及系统调用）
        try:
            state = self._monitor.get_state()
        except Exception:  # noqa: BLE001
            logger.exception("获取活动状态失败，跳过本次 tick")
            return

        with self._lock:
            if state is ActivityState.ACTIVE:
                self._active_seconds += delta_seconds
            elif state is ActivityState.IDLE:
                self._idle_seconds_total += delta_seconds
            elif state is ActivityState.NATURAL_REST:
                self._idle_seconds_total += delta_seconds
                self._active_seconds = 0.0
            else:
                logger.warning("未知活动状态: %r，跳过本次 tick", state)

    def _tick_loop(self, interval: float) -> None:
        """后台 tick 线程主循环。"""
        # 启动后立即 tick 一次，避免首个统计被延迟一个 interval
        self.tick(self.DEFAULT_TICK_INTERVAL)
        while not self._stop_event.wait(interval):
            try:
                self.tick(self.DEFAULT_TICK_INTERVAL)
            except Exception:  # noqa: BLE001
                # 循环内任何异常都不应杀死线程
                logger.exception("TimerEngine tick 异常")

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_active_seconds(self) -> float:
        """获取累计的有效用眼时间（秒）。"""
        with self._lock:
            return self._active_seconds

    def get_idle_seconds_total(self) -> float:
        """获取累计的空闲时间（秒）。"""
        with self._lock:
            return self._idle_seconds_total

    def get_app_uptime(self) -> float:
        """获取应用运行时间（秒）。"""
        with self._lock:
            return self._app_uptime

    def is_running(self) -> bool:
        """后台 tick 线程是否正在运行。"""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        """是否处于暂停状态。"""
        with self._lock:
            return self._paused

    def get_stats(self) -> dict:
        """获取所有统计数据的快照。

        Returns:
            包含 active_seconds / idle_seconds_total / app_uptime /
            is_running / is_paused 的字典。
        """
        with self._lock:
            return {
                "active_seconds": self._active_seconds,
                "idle_seconds_total": self._idle_seconds_total,
                "app_uptime": self._app_uptime,
                "is_running": self._running,
                "is_paused": self._paused,
            }
