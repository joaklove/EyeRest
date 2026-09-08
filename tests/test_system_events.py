"""系统事件监听单元测试（Task 15）。

覆盖 PowerMonitor（电源/睡眠）与 SessionMonitor（会话/锁屏）：

* 直接调用核心处理方法 ``_on_sleep`` / ``_on_wake`` / ``_on_lock`` /
  ``_on_unlock`` 验证事件发布与状态机驱动（确定性测试）
* 通过可注入的 time_func / session_state_provider 验证轮询循环
* 线程启动/停止幂等性
* 与真实 StateMachine 的集成（状态转换 + 计时器协同）
* 非 Windows 平台优雅降级
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from typing import Any, Optional
from unittest.mock import patch

from app.config import defaults
from app.core.event_bus import EventBus, EventType
from app.core.state_machine import AppState, StateMachine
from app.windows.power_monitor import PowerMonitor
from app.windows.session_monitor import SessionMonitor


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
class MockTimerEngine:
    """可控的 TimerEngine mock，记录所有调用。"""

    def __init__(self) -> None:
        self.reset_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self._active_seconds: float = 0.0
        self._paused: bool = False
        self._lock = threading.Lock()

    def tick(self, delta_seconds: float = 1.0) -> None:
        with self._lock:
            if not self._paused:
                self._active_seconds += delta_seconds

    def reset(self) -> None:
        with self._lock:
            self.reset_calls += 1
            self._active_seconds = 0.0

    def pause(self) -> None:
        with self._lock:
            self.pause_calls += 1
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self.resume_calls += 1
            self._paused = False

    def get_active_seconds(self) -> float:
        with self._lock:
            return self._active_seconds

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused


class ControllableActivityMonitor:
    """可控的 ActivityMonitor mock，返回指定空闲秒数。"""

    def __init__(self, initial_idle: float = 0.0) -> None:
        self._idle = float(initial_idle)
        self._lock = threading.Lock()

    def get_idle_seconds(self) -> float:
        with self._lock:
            return self._idle

    def set_idle(self, value: float) -> None:
        with self._lock:
            self._idle = float(value)


class EventCollector:
    """收集指定事件类型的事件。"""

    def __init__(self, bus: EventBus, event_type: EventType) -> None:
        self._bus = bus
        self._event_type = event_type
        self.events: list[Any] = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        bus.subscribe(event_type, self._handler)

    def _handler(self, data: Any) -> None:
        with self._lock:
            self.events.append(data)
            self._event.set()

    def get_events(self) -> list[Any]:
        with self._lock:
            return list(self.events)

    def clear(self) -> None:
        with self._lock:
            self.events.clear()
        self._event.clear()

    def wait_for_event(self, timeout: float = 2.0) -> bool:
        return self._event.wait(timeout=timeout)

    def unsubscribe(self) -> None:
        self._bus.unsubscribe(self._event_type, self._handler)


class ControllableTime:
    """可控时间源，用于模拟时间跳跃（系统睡眠）。"""

    def __init__(self, initial: float = 1000.0) -> None:
        self._now = float(initial)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, delta: float) -> None:
        with self._lock:
            self._now += delta

    def set(self, value: float) -> None:
        with self._lock:
            self._now = float(value)


class ControllableSessionState:
    """可控会话状态提供者。"""

    def __init__(self, initial: Optional[bool] = False) -> None:
        self._locked = initial
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self) -> Optional[bool]:
        with self._lock:
            self.calls += 1
            return self._locked

    def set(self, locked: Optional[bool]) -> None:
        with self._lock:
            self._locked = locked

    def get_calls(self) -> int:
        with self._lock:
            return self.calls


def make_state_machine(
    timer_engine: Optional[MockTimerEngine] = None,
    activity_monitor: Optional[ControllableActivityMonitor] = None,
) -> tuple[StateMachine, MockTimerEngine, EventBus]:
    """构造状态机及其依赖。"""
    bus = EventBus()
    timer = timer_engine if timer_engine is not None else MockTimerEngine()
    sm = StateMachine(
        timer_engine=timer,
        event_bus=bus,
        activity_monitor=activity_monitor,
    )
    return sm, timer, bus


# ===========================================================================
# PowerMonitor 测试
# ===========================================================================
class TestPowerMonitorDirectHandlers(unittest.TestCase):
    """直接测试 _on_sleep / _on_wake 处理方法（确定性）。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.sm, self.timer, _ = make_state_machine()
        self.sm.start_protection()
        self.collector_sleep = EventCollector(self.bus, EventType.SYSTEM_SLEEP)
        self.collector_wake = EventCollector(self.bus, EventType.SYSTEM_WAKE)
        self.monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            idle_provider=lambda: 30.0,
        )

    def tearDown(self) -> None:
        self.collector_sleep.unsubscribe()
        self.collector_wake.unsubscribe()
        self.bus.clear()

    def test_on_sleep_transitions_to_sleep(self) -> None:
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.monitor._on_sleep(10.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.SLEEP)
        self.assertTrue(self.timer.is_paused())

    def test_on_sleep_publishes_event(self) -> None:
        self.monitor._on_sleep(15.5)  # noqa: SLF001
        events = self.collector_sleep.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["delta_seconds"], 15.5)
        self.assertEqual(events[0]["reason"], "time_delta_detected")

    def test_on_wake_transitions_to_active(self) -> None:
        self.monitor._on_sleep(10.0)  # noqa: SLF001
        self.monitor._on_wake(10.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

    def test_on_wake_publishes_event_with_idle(self) -> None:
        self.monitor._on_sleep(10.0)  # noqa: SLF001
        self.monitor._on_wake(10.0)  # noqa: SLF001
        events = self.collector_wake.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["delta_seconds"], 10.0)
        self.assertEqual(events[0]["idle_seconds"], 30.0)

    def test_on_wake_long_idle_resets_timer(self) -> None:
        # idle >= 120s 应重置
        monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            idle_provider=lambda: defaults.NATURAL_REST_THRESHOLD + 10,
        )
        for _ in range(10):
            self.timer.tick()
        self.assertEqual(self.timer.get_active_seconds(), 10.0)

        monitor._on_sleep(10.0)  # noqa: SLF001
        monitor._on_wake(10.0)  # noqa: SLF001
        self.assertEqual(self.timer.get_active_seconds(), 0.0)

    def test_on_wake_short_idle_no_reset(self) -> None:
        for _ in range(10):
            self.timer.tick()
        self.assertEqual(self.timer.get_active_seconds(), 10.0)

        self.monitor._on_sleep(10.0)  # noqa: SLF001
        self.monitor._on_wake(10.0)  # noqa: SLF001
        self.assertEqual(self.timer.get_active_seconds(), 10.0)

    def test_sleep_then_wake_sequence(self) -> None:
        """完整的 睡眠→唤醒 序列。"""
        self.monitor._on_sleep(3600.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.SLEEP)
        self.monitor._on_wake(3600.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        sleep_events = self.collector_sleep.get_events()
        wake_events = self.collector_wake.get_events()
        self.assertEqual(len(sleep_events), 1)
        self.assertEqual(len(wake_events), 1)


class TestPowerMonitorPolling(unittest.TestCase):
    """PowerMonitor 轮询循环测试（注入可控时间源）。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.sm, self.timer, _ = make_state_machine()
        self.sm.start_protection()
        self.collector_sleep = EventCollector(self.bus, EventType.SYSTEM_SLEEP)
        self.collector_wake = EventCollector(self.bus, EventType.SYSTEM_WAKE)
        self.time_func = ControllableTime(1000.0)
        self.monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            sleep_threshold=5.0,
            time_func=self.time_func,
            idle_provider=lambda: 30.0,
        )

    def tearDown(self) -> None:
        self.monitor.stop()
        self.collector_sleep.unsubscribe()
        self.collector_wake.unsubscribe()
        self.bus.clear()

    def test_polling_detects_sleep_wake(self) -> None:
        """时间差 > 阈值时检测到睡眠/唤醒。"""
        # 先启动并等待一个轮询周期（记录基准时间）
        self.monitor.start()
        time.sleep(0.7)
        self.assertTrue(self.monitor.is_running())

        # 模拟系统睡眠 60 秒
        self.collector_sleep.clear()
        self.collector_wake.clear()
        self.time_func.advance(60.0)

        # 等待下一个轮询周期检测到跳跃
        self.assertTrue(
            self.collector_sleep.wait_for_event(timeout=2.0),
            "未检测到系统睡眠事件",
        )
        self.assertTrue(
            self.collector_wake.wait_for_event(timeout=1.0),
            "未检测到系统唤醒事件",
        )
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

    def test_polling_no_event_when_delta_small(self) -> None:
        """正常时间差不触发事件。"""
        self.monitor.start()
        # 正常推进时间（小于阈值）
        for _ in range(5):
            self.time_func.advance(1.0)
            time.sleep(0.6)
        self.assertEqual(len(self.collector_sleep.get_events()), 0)
        self.assertEqual(len(self.collector_wake.get_events()), 0)
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

    def test_start_stop_idempotent(self) -> None:
        self.assertFalse(self.monitor.is_running())
        self.monitor.start()
        time.sleep(0.6)
        self.assertTrue(self.monitor.is_running())
        # 重复 start 不重复启动
        self.monitor.start()
        self.monitor.stop()
        self.assertFalse(self.monitor.is_running())
        # 重复 stop 不报错
        self.monitor.stop()

    def test_stop_when_not_running(self) -> None:
        self.monitor.stop()
        self.assertFalse(self.monitor.is_running())

    def test_non_windows_no_crash(self) -> None:
        """非 Windows 平台 start 为空操作不崩溃。"""
        with patch.object(sys, "platform", "darwin"):
            monitor = PowerMonitor(
                event_bus=self.bus,
                state_machine=self.sm,
                poll_interval=0.5,
                time_func=self.time_func,
            )
            monitor.start()
            self.assertFalse(monitor.is_running())
            monitor.stop()  # 不崩溃

    def test_no_state_machine_no_crash(self) -> None:
        """state_machine=None 时仅发布事件不崩溃。"""
        monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=None,
            poll_interval=0.5,
            time_func=self.time_func,
        )
        monitor._on_sleep(10.0)  # noqa: SLF001
        monitor._on_wake(10.0)  # noqa: SLF001
        # 事件仍发布
        self.assertEqual(len(self.collector_sleep.get_events()), 1)
        self.assertEqual(len(self.collector_wake.get_events()), 1)

    def test_idle_provider_exception_tolerated(self) -> None:
        """idle_provider 抛异常时唤醒不崩溃，idle_seconds=None。"""

        def broken_idle() -> float:
            raise RuntimeError("broken")

        monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            time_func=self.time_func,
            idle_provider=broken_idle,
        )
        monitor._on_sleep(10.0)  # noqa: SLF001
        monitor._on_wake(10.0)  # noqa: SLF001
        events = self.collector_wake.get_events()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["idle_seconds"])

    def test_state_machine_exception_tolerated(self) -> None:
        """状态机方法抛异常时监听不崩溃。"""

        class BrokenSM:
            def on_system_sleep(self) -> None:
                raise RuntimeError("broken")

            def on_system_wake(self, idle_seconds: Optional[float] = None) -> None:
                raise RuntimeError("broken")

        monitor = PowerMonitor(
            event_bus=self.bus,
            state_machine=BrokenSM(),
            poll_interval=0.5,
            time_func=self.time_func,
        )
        # 不崩溃
        monitor._on_sleep(10.0)  # noqa: SLF001
        monitor._on_wake(10.0)  # noqa: SLF001


# ===========================================================================
# SessionMonitor 测试
# ===========================================================================
class TestSessionMonitorDirectHandlers(unittest.TestCase):
    """直接测试 _on_lock / _on_unlock 处理方法（确定性）。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.sm, self.timer, _ = make_state_machine()
        self.sm.start_protection()
        self.collector_lock = EventCollector(self.bus, EventType.SYSTEM_LOCK)
        self.collector_unlock = EventCollector(self.bus, EventType.SYSTEM_UNLOCK)
        self.monitor = SessionMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            use_qt_notifications=False,
            idle_provider=lambda: 45.0,
        )

    def tearDown(self) -> None:
        self.collector_lock.unsubscribe()
        self.collector_unlock.unsubscribe()
        self.bus.clear()

    def test_on_lock_transitions_to_locked(self) -> None:
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.monitor._on_lock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)
        self.assertTrue(self.timer.is_paused())

    def test_on_lock_publishes_event(self) -> None:
        self.monitor._on_lock()  # noqa: SLF001
        events = self.collector_lock.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "session_lock")

    def test_on_unlock_transitions_to_active(self) -> None:
        self.monitor._on_lock()  # noqa: SLF001
        self.monitor._on_unlock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

    def test_on_unlock_publishes_event_with_idle(self) -> None:
        self.monitor._on_lock()  # noqa: SLF001
        self.monitor._on_unlock()  # noqa: SLF001
        events = self.collector_unlock.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["idle_seconds"], 45.0)

    def test_on_unlock_long_idle_resets_timer(self) -> None:
        monitor = SessionMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            use_qt_notifications=False,
            idle_provider=lambda: defaults.NATURAL_REST_THRESHOLD + 20,
        )
        for _ in range(10):
            self.timer.tick()
        self.assertEqual(self.timer.get_active_seconds(), 10.0)

        monitor._on_lock()  # noqa: SLF001
        monitor._on_unlock()  # noqa: SLF001
        self.assertEqual(self.timer.get_active_seconds(), 0.0)

    def test_lock_unlock_full_sequence(self) -> None:
        """完整的 锁屏→解锁 序列。"""
        self.monitor._on_lock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)
        self.monitor._on_unlock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertEqual(len(self.collector_lock.get_events()), 1)
        self.assertEqual(len(self.collector_unlock.get_events()), 1)

    def test_handle_session_change_lock(self) -> None:
        """测试 _handle_session_change 路由锁屏。"""
        from app.windows.session_monitor import WTS_SESSION_LOCK

        self.monitor._handle_session_change(WTS_SESSION_LOCK)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)

    def test_handle_session_change_unlock(self) -> None:
        """测试 _handle_session_change 路由解锁。"""
        from app.windows.session_monitor import WTS_SESSION_LOCK, WTS_SESSION_UNLOCK

        self.monitor._handle_session_change(WTS_SESSION_LOCK)  # noqa: SLF001
        self.monitor._handle_session_change(WTS_SESSION_UNLOCK)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

    def test_handle_session_change_ignores_unknown(self) -> None:
        """未知的 wParam 被忽略。"""
        self.monitor._handle_session_change(0x999)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)


class TestSessionMonitorPolling(unittest.TestCase):
    """SessionMonitor 轮询模式测试（注入可控会话状态源）。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.sm, self.timer, _ = make_state_machine()
        self.sm.start_protection()
        self.collector_lock = EventCollector(self.bus, EventType.SYSTEM_LOCK)
        self.collector_unlock = EventCollector(self.bus, EventType.SYSTEM_UNLOCK)
        self.session_state = ControllableSessionState(initial=False)
        self.monitor = SessionMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            use_qt_notifications=False,
            session_state_provider=self.session_state,
            idle_provider=lambda: 45.0,
        )

    def tearDown(self) -> None:
        self.monitor.stop()
        self.collector_lock.unsubscribe()
        self.collector_unlock.unsubscribe()
        self.bus.clear()

    def test_polling_detects_lock(self) -> None:
        """会话状态变为锁定时触发锁屏。"""
        self.monitor.start()
        time.sleep(0.7)

        self.collector_lock.clear()
        self.session_state.set(True)
        self.assertTrue(
            self.collector_lock.wait_for_event(timeout=2.0),
            "未检测到锁屏事件",
        )
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)

    def test_polling_detects_unlock(self) -> None:
        """会话状态变为解锁时触发解锁。"""
        self.monitor.start()
        time.sleep(0.7)

        # 先锁屏
        self.session_state.set(True)
        self.assertTrue(self.collector_lock.wait_for_event(timeout=2.0))
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)

        # 再解锁
        self.collector_unlock.clear()
        self.session_state.set(False)
        self.assertTrue(self.collector_unlock.wait_for_event(timeout=2.0))
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

    def test_polling_ignores_none_state(self) -> None:
        """provider 返回 None（未知）时不触发事件。"""
        self.monitor.start()
        time.sleep(0.7)
        self.session_state.set(None)
        time.sleep(1.5)
        self.assertEqual(len(self.collector_lock.get_events()), 0)
        self.assertEqual(len(self.collector_unlock.get_events()), 0)
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

    def test_polling_no_duplicate_events(self) -> None:
        """状态不变时不重复触发事件。"""
        self.monitor.start()
        time.sleep(0.7)
        # 保持 locked 状态不变
        self.session_state.set(True)
        time.sleep(1.5)
        events = self.collector_lock.get_events()
        self.assertEqual(len(events), 1)

    def test_start_stop_idempotent(self) -> None:
        self.assertFalse(self.monitor.is_running())
        self.monitor.start()
        time.sleep(0.6)
        self.assertTrue(self.monitor.is_running())
        self.monitor.start()  # 不重复启动
        self.monitor.stop()
        self.assertFalse(self.monitor.is_running())
        self.monitor.stop()  # 不崩溃

    def test_non_windows_no_crash(self) -> None:
        """非 Windows 平台 start 为空操作不崩溃。"""
        with patch.object(sys, "platform", "linux"):
            monitor = SessionMonitor(
                event_bus=self.bus,
                state_machine=self.sm,
                poll_interval=0.5,
                use_qt_notifications=False,
                session_state_provider=self.session_state,
            )
            monitor.start()
            self.assertFalse(monitor.is_running())
            monitor.stop()

    def test_no_state_machine_no_crash(self) -> None:
        """state_machine=None 时仅发布事件不崩溃。"""
        monitor = SessionMonitor(
            event_bus=self.bus,
            state_machine=None,
            poll_interval=0.5,
            use_qt_notifications=False,
            session_state_provider=self.session_state,
        )
        monitor._on_lock()  # noqa: SLF001
        monitor._on_unlock()  # noqa: SLF001
        self.assertEqual(len(self.collector_lock.get_events()), 1)
        self.assertEqual(len(self.collector_unlock.get_events()), 1)


# ===========================================================================
# 集成测试：监控器 + 真实状态机
# ===========================================================================
class TestSystemEventsIntegration(unittest.TestCase):
    """PowerMonitor / SessionMonitor 与 StateMachine 集成测试。"""

    def setUp(self) -> None:
        self.activity = ControllableActivityMonitor(30.0)
        # 使用 make_state_machine 返回的 bus，确保状态机与监控器共享同一事件总线
        self.sm, self.timer, self.bus = make_state_machine(activity_monitor=self.activity)
        self.sm.start_protection()

    def _make_monitors(
        self, time_func: Optional[ControllableTime] = None
    ) -> tuple[PowerMonitor, SessionMonitor]:
        idle_provider = self.activity.get_idle_seconds
        power = PowerMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            sleep_threshold=5.0,
            time_func=time_func or ControllableTime(),
            idle_provider=idle_provider,
        )
        session = SessionMonitor(
            event_bus=self.bus,
            state_machine=self.sm,
            poll_interval=0.5,
            use_qt_notifications=False,
            session_state_provider=ControllableSessionState(False),
            idle_provider=idle_provider,
        )
        return power, session

    def tearDown(self) -> None:
        self.bus.clear()

    def test_power_sleep_wake_resumes_protection(self) -> None:
        """睡眠→唤醒后保护恢复，计时器恢复。"""
        power, _ = self._make_monitors()
        for _ in range(20):
            self.timer.tick()
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

        power._on_sleep(600.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.SLEEP)
        self.assertTrue(self.timer.is_paused())

        power._on_wake(600.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

    def test_session_lock_unlock_resumes_protection(self) -> None:
        """锁屏→解锁后保护恢复，计时器恢复。"""
        _, session = self._make_monitors()
        for _ in range(20):
            self.timer.tick()

        session._on_lock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)
        self.assertTrue(self.timer.is_paused())

        session._on_unlock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

    def test_unlock_with_long_idle_resets_counter(self) -> None:
        """解锁时空闲 >= 300s 重置工作计数（V1.0 新阈值）。"""
        _, session = self._make_monitors()
        for _ in range(20):
            self.timer.tick()
        self.assertEqual(self.timer.get_active_seconds(), 20.0)

        session._on_lock()  # noqa: SLF001
        # 模拟用户离开 320 秒（超过新的自然休息阈值 300s）
        self.activity.set_idle(320.0)
        session._on_unlock()  # noqa: SLF001
        self.assertEqual(self.timer.get_active_seconds(), 0.0)

    def test_wake_with_long_idle_resets_counter(self) -> None:
        """唤醒时空闲 >= 300s 重置工作计数（V1.0 新阈值）。"""
        power, _ = self._make_monitors()
        for _ in range(20):
            self.timer.tick()

        power._on_sleep(600.0)  # noqa: SLF001
        self.activity.set_idle(320.0)
        power._on_wake(600.0)  # noqa: SLF001
        self.assertEqual(self.timer.get_active_seconds(), 0.0)

    def test_lock_then_sleep_then_wake_unlock_sequence(self) -> None:
        """锁屏 → 睡眠 → 唤醒 → 解锁 复杂序列。"""
        power, session = self._make_monitors()

        # 锁屏
        session._on_lock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)
        self.assertTrue(self.timer.is_paused())

        # 锁屏后睡眠
        power._on_sleep(300.0)  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.SLEEP)

        # 唤醒（仍锁屏）
        power._on_wake(300.0)  # noqa: SLF001
        # 唤醒后空闲 30s，< 120s 不重置
        self.activity.set_idle(30.0)
        # 注意：on_system_wake 从 SLEEP 回到 ACTIVE，但此时可能仍锁屏
        # 实际场景中唤醒后会收到锁屏通知，这里直接解锁
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)

        # 立即收到锁屏通知
        session._on_lock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.LOCKED)

        # 最终解锁
        self.activity.set_idle(60.0)
        session._on_unlock()  # noqa: SLF001
        self.assertEqual(self.sm.get_state(), AppState.ACTIVE)
        self.assertFalse(self.timer.is_paused())

    def test_monitor_events_and_state_changed_both_published(self) -> None:
        """系统事件与状态机 STATE_CHANGED 事件都被发布。"""
        state_collector = EventCollector(self.bus, EventType.STATE_CHANGED)
        lock_collector = EventCollector(self.bus, EventType.SYSTEM_LOCK)
        try:
            # 收集器创建在 start_protection 之后，因此只能捕获 system_lock
            # 的 STATE_CHANGED 事件（start_protection 的事件已在 setUp 中发布）
            state_collector.clear()
            _, session = self._make_monitors()
            session._on_lock()  # noqa: SLF001

            self.assertEqual(len(lock_collector.get_events()), 1)
            state_events = state_collector.get_events()
            # 至少捕获到 system_lock 的状态变更事件
            self.assertGreaterEqual(len(state_events), 1)
            self.assertEqual(state_events[-1]["new_state"], "LOCKED")
            self.assertEqual(state_events[-1]["reason"], "system_lock")
        finally:
            state_collector.unsubscribe()
            lock_collector.unsubscribe()


# ===========================================================================
# 边界与容错
# ===========================================================================
class TestEdgeCases(unittest.TestCase):
    """边界条件与容错测试。"""

    def test_power_monitor_default_bus(self) -> None:
        """未传入 event_bus 时使用全局单例。"""
        from app.core.event_bus import get_event_bus

        monitor = PowerMonitor()
        self.assertIs(monitor._bus, get_event_bus())  # noqa: SLF001

    def test_session_monitor_default_bus(self) -> None:
        from app.core.event_bus import get_event_bus

        monitor = SessionMonitor()
        self.assertIs(monitor._bus, get_event_bus())  # noqa: SLF001

    def test_power_monitor_min_poll_interval(self) -> None:
        """poll_interval 过低时被钳制到 0.5。"""
        monitor = PowerMonitor(poll_interval=0.01)
        self.assertEqual(monitor.poll_interval, 0.5)

    def test_session_monitor_min_poll_interval(self) -> None:
        monitor = SessionMonitor(poll_interval=0.01)
        self.assertEqual(monitor.poll_interval, 0.5)

    def test_power_monitor_sleep_threshold_auto_adjusted(self) -> None:
        """sleep_threshold <= poll_interval 时自动调整。"""
        monitor = PowerMonitor(poll_interval=2.0, sleep_threshold=1.0)
        self.assertGreater(monitor.sleep_threshold, monitor.poll_interval)

    def test_power_monitor_none_bus_no_crash(self) -> None:
        """bus=None 时发布事件不崩溃。"""
        monitor = PowerMonitor(event_bus=None, state_machine=None)
        monitor._on_sleep(10.0)  # noqa: SLF001
        monitor._on_wake(10.0)  # noqa: SLF001

    def test_session_monitor_none_bus_no_crash(self) -> None:
        monitor = SessionMonitor(event_bus=None, state_machine=None)
        monitor._on_lock()  # noqa: SLF001
        monitor._on_unlock()  # noqa: SLF001

    def test_session_monitor_using_qt_default_false_when_no_qt(self) -> None:
        """无 QApplication 时 using_qt_notifications 为 False。"""
        bus = EventBus()
        sm, _, _ = make_state_machine()
        monitor = SessionMonitor(
            event_bus=bus,
            state_machine=sm,
            use_qt_notifications=True,
        )
        # 在测试环境下若无 QApplication，start 后不会使用 Qt
        with patch.object(sys, "platform", "win32"):
            with patch.object(monitor, "_try_start_qt_notifications", return_value=False):
                monitor.start()
                self.assertFalse(monitor.using_qt_notifications)
                monitor.stop()


if __name__ == "__main__":
    unittest.main()
