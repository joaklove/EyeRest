"""电源事件监控：监听系统睡眠 / 唤醒事件。

采用「轮询 + 时间差检测」方案：后台线程每隔 ``poll_interval`` 秒记录一次
系统时间，若两次采样之间的时间差超过 ``sleep_threshold``（默认 5 秒），
则判定系统进入了睡眠（S3/S4），随后恢复时依次触发：

1. :meth:`StateMachine.on_system_sleep` — 状态机进入 SLEEP，暂停计时
2. :meth:`StateMachine.on_system_wake` — 状态机回到 ACTIVE（根据空闲时长
   判断是否重置），恢复计时

同时通过 :class:`~app.core.event_bus.EventBus` 发布 ``SYSTEM_SLEEP`` /
``SYSTEM_WAKE`` 事件，供其他订阅者感知。

仅在 Windows 平台启用轮询；非 Windows 平台 :meth:`start` 为安全的空操作
（不崩溃、不抛异常）。

设计要点：

* 线程安全：所有可变状态由 ``threading.RLock`` 保护
* 可注入 ``time_func`` 便于测试模拟时间跳跃
* 轮询循环内任何异常都不会杀死线程
* 与 StateMachine 解耦：状态机方法异常被捕获，不影响监听线程
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Optional

from app.core.event_bus import EventBus, EventType, get_event_bus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PowerMonitor:
    """Windows 电源事件监控器（睡眠 / 唤醒）。

    通过后台轮询检测系统睡眠时间差，驱动状态机进入/退出 SLEEP 状态。
    """

    # 默认轮询间隔（秒）
    DEFAULT_POLL_INTERVAL: float = 2.0
    # 判定为睡眠的最小时间差（秒）。正常轮询抖动不会超过此值，
    # 系统睡眠后墙钟时间会跳变，远超此阈值。
    DEFAULT_SLEEP_THRESHOLD: float = 5.0

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_machine: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        sleep_threshold: float = DEFAULT_SLEEP_THRESHOLD,
        time_func: Callable[[], float] = time.time,
        idle_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        """初始化电源监控器。

        Args:
            event_bus: 事件总线；为 None 时使用全局单例。
            state_machine: 状态机实例，需实现 ``on_system_sleep()`` /
                ``on_system_wake(idle_seconds=...)``。可为 None（仅发布事件）。
            poll_interval: 轮询间隔（秒），最小不低于 0.5 秒。
            sleep_threshold: 判定系统睡眠的时间差阈值（秒）。
            time_func: 返回当前时间戳的可调用对象，默认 ``time.time``，
                便于测试时注入可控时间。
            idle_provider: 返回当前空闲秒数的可调用对象，唤醒时传入状态机；
                为 None 时唤醒不传 idle_seconds（状态机保守不重置）。
        """
        if poll_interval < 0.5:
            poll_interval = 0.5
        if sleep_threshold <= poll_interval:
            sleep_threshold = poll_interval * 2 + 1.0

        self._bus = event_bus if event_bus is not None else get_event_bus()
        self._state_machine = state_machine
        self._poll_interval = float(poll_interval)
        self._sleep_threshold = float(sleep_threshold)
        self._time_func = time_func
        self._idle_provider = idle_provider

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 标记是否已检测到睡眠，避免重复触发 sleep 事件
        self._sleep_detected: bool = False

    # ------------------------------------------------------------------
    # 公共查询
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        """轮询线程是否正在运行。"""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @property
    def sleep_threshold(self) -> float:
        return self._sleep_threshold

    # ------------------------------------------------------------------
    # 轮询控制
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动后台轮询线程（幂等）。

        非 Windows 平台为安全空操作，仅记录 debug 日志。
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                logger.debug("PowerMonitor 轮询已在运行，忽略 start")
                return

            if sys.platform != "win32":
                logger.debug("非 Windows 平台，PowerMonitor 不启动轮询")
                return

            self._stop_event.clear()
            self._sleep_detected = False
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="eyerest-power-monitor",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "PowerMonitor 轮询已启动 interval=%.2fs sleep_threshold=%.1fs",
                self._poll_interval,
                self._sleep_threshold,
            )

    def stop(self, timeout: float = 2.0) -> None:
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

        thread.join(timeout=timeout)

        with self._lock:
            if self._thread is thread:
                if thread.is_alive():
                    logger.warning("PowerMonitor 轮询线程在 %.1fs 内未退出", timeout)
                self._thread = None
        logger.info("PowerMonitor 轮询已停止")

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        """轮询线程主循环：检测时间跳跃。"""
        last_time = self._time_func()
        while not self._stop_event.wait(self._poll_interval):
            try:
                current_time = self._time_func()
                delta = current_time - last_time

                # 时间差超过阈值，说明系统睡眠了 delta 秒
                if delta > self._sleep_threshold:
                    logger.info(
                        "检测到系统睡眠/唤醒: delta=%.1fs (threshold=%.1fs)",
                        delta,
                        self._sleep_threshold,
                    )
                    self._on_sleep(delta)
                    self._on_wake(delta)

                last_time = current_time
            except Exception:  # noqa: BLE001
                # 轮询循环内任何异常都不应杀死线程
                logger.exception("PowerMonitor 轮询异常")
                # 出现异常时重置基准时间，避免下一轮重复触发
                try:
                    last_time = self._time_func()
                except Exception:  # noqa: BLE001
                    pass

    def _on_sleep(self, delta_seconds: float) -> None:
        """处理检测到的系统睡眠。

        发布 ``SYSTEM_SLEEP`` 事件并通知状态机。
        """
        with self._lock:
            if self._sleep_detected:
                # 已处于睡眠检测状态，不重复触发
                return
            self._sleep_detected = True

        payload = {
            "delta_seconds": float(delta_seconds),
            "reason": "time_delta_detected",
        }
        logger.info("系统睡眠事件: %s", payload)
        self._publish(EventType.SYSTEM_SLEEP, payload)
        self._notify_state_machine("on_system_sleep", payload)

    def _on_wake(self, delta_seconds: float) -> None:
        """处理检测到的系统唤醒。

        发布 ``SYSTEM_WAKE`` 事件并通知状态机，传入当前空闲秒数。
        """
        with self._lock:
            if not self._sleep_detected:
                return
            self._sleep_detected = False

        idle_seconds: Optional[float] = None
        if self._idle_provider is not None:
            try:
                idle_seconds = float(self._idle_provider())
            except Exception:  # noqa: BLE001
                logger.exception("获取空闲秒数失败，唤醒时不重置计时")
                idle_seconds = None

        payload = {
            "delta_seconds": float(delta_seconds),
            "idle_seconds": idle_seconds,
            "reason": "time_delta_detected",
        }
        logger.info("系统唤醒事件: %s", payload)
        self._publish(EventType.SYSTEM_WAKE, payload)
        self._notify_state_machine(
            "on_system_wake", payload, idle_seconds=idle_seconds
        )

    def _publish(self, event_type: EventType, data: Any = None) -> None:
        """发布事件（容错，不抛出异常）。"""
        if self._bus is None:
            return
        try:
            self._bus.publish(event_type, data)
        except Exception:  # noqa: BLE001
            logger.exception("发布 %s 事件失败", event_type.value)

    def _notify_state_machine(
        self, method_name: str, payload: dict, **kwargs: Any
    ) -> None:
        """调用状态机对应方法（容错）。

        若状态机无对应方法或调用抛异常，仅记录日志，不影响监听线程。
        """
        if self._state_machine is None:
            return
        method = getattr(self._state_machine, method_name, None)
        if method is None or not callable(method):
            return
        try:
            method(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("调用 StateMachine.%s 失败", method_name)
