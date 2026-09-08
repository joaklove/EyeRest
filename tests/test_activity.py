"""活动监控与空闲检测单元测试。

由于 ``get_idle_seconds()`` 依赖 Windows API，测试通过注入可控的
``idle_provider`` 来模拟不同空闲时长，避免真实键鼠输入的不确定性。
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from app.config import defaults
from app.core.activity_monitor import ActivityMonitor, ActivityState
from app.core.event_bus import EventBus, EventType
from app.windows import idle_detector


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
class ControllableIdleProvider:
    """可控的空闲秒数提供者，用于替换真实 Windows API。"""

    def __init__(self, initial: float = 0.0) -> None:
        self._value = float(initial)
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self) -> float:
        with self._lock:
            self.calls += 1
            return self._value

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)

    def get_calls(self) -> int:
        with self._lock:
            return self.calls


class TestIdleDetector(unittest.TestCase):
    """空闲检测器测试。"""

    def test_get_idle_seconds_returns_nonnegative_float(self) -> None:
        """真实 API 调用应返回非负浮点数（仅 Windows 上运行真实检测）。"""
        idle = idle_detector.get_idle_seconds()
        self.assertIsInstance(idle, float)
        self.assertGreaterEqual(idle, 0.0)

    def test_get_idle_seconds_handles_api_failure(self) -> None:
        """API 调用失败时应返回 0.0 而非抛出异常。"""
        # 仅在 Windows 上能 patch 内部 API
        if sys.platform != "win32":
            self.skipTest("非 Windows 平台，跳过 Win32 API 异常路径测试")

        with patch.object(
            idle_detector, "_GetLastInputInfo", return_value=False
        ):
            idle = idle_detector.get_idle_seconds()
            self.assertEqual(idle, 0.0)

    def test_idle_detector_class_delegates(self) -> None:
        """IdleDetector 类应委托给 get_idle_seconds。"""
        detector = idle_detector.IdleDetector()
        with patch.object(
            idle_detector, "get_idle_seconds", return_value=42.0
        ) as mock_fn:
            self.assertEqual(detector.get_idle_seconds(), 42.0)
            mock_fn.assert_called_once()


class TestActivityState(unittest.TestCase):
    """ActivityState 枚举与阈值边界测试。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.provider = ControllableIdleProvider(0.0)
        self.monitor = ActivityMonitor(
            event_bus=self.bus, idle_provider=self.provider
        )

    def tearDown(self) -> None:
        self.monitor.stop_polling()
        self.bus.clear()

    def test_state_active(self) -> None:
        self.provider.set(0.0)
        self.assertEqual(self.monitor.get_state(), ActivityState.ACTIVE)

    def test_state_active_at_threshold(self) -> None:
        """空闲 == idle_threshold 时仍为 ACTIVE（<= 规则）。"""
        self.provider.set(defaults.IDLE_THRESHOLD)
        self.assertEqual(self.monitor.get_state(), ActivityState.ACTIVE)

    def test_state_idle_above_idle_threshold(self) -> None:
        self.provider.set(defaults.IDLE_THRESHOLD + 0.001)
        self.assertEqual(self.monitor.get_state(), ActivityState.IDLE)

    def test_state_idle(self) -> None:
        self.provider.set(90.0)
        self.assertEqual(self.monitor.get_state(), ActivityState.IDLE)

    def test_state_idle_below_natural_rest(self) -> None:
        self.provider.set(defaults.NATURAL_REST_THRESHOLD - 0.001)
        self.assertEqual(self.monitor.get_state(), ActivityState.IDLE)

    def test_state_natural_rest_at_threshold(self) -> None:
        """空闲 == natural_rest_threshold 时为 NATURAL_REST（>= 规则）。"""
        self.provider.set(defaults.NATURAL_REST_THRESHOLD)
        self.assertEqual(self.monitor.get_state(), ActivityState.NATURAL_REST)

    def test_state_natural_rest(self) -> None:
        self.provider.set(300.0)
        self.assertEqual(self.monitor.get_state(), ActivityState.NATURAL_REST)

    def test_is_active(self) -> None:
        self.provider.set(10.0)
        self.assertTrue(self.monitor.is_active())
        self.provider.set(90.0)
        self.assertFalse(self.monitor.is_active())
        self.provider.set(130.0)
        self.assertFalse(self.monitor.is_active())

    def test_get_idle_seconds_delegates(self) -> None:
        self.provider.set(55.5)
        self.assertEqual(self.monitor.get_idle_seconds(), 55.5)

    def test_invalid_threshold_raises(self) -> None:
        with self.assertRaises(ValueError):
            ActivityMonitor(
                idle_threshold=120,
                natural_rest_threshold=60,
                event_bus=self.bus,
                idle_provider=self.provider,
            )
        with self.assertRaises(ValueError):
            ActivityMonitor(
                idle_threshold=-1,
                natural_rest_threshold=120,
                event_bus=self.bus,
                idle_provider=self.provider,
            )


class TestActivityMonitorPolling(unittest.TestCase):
    """轮询线程与事件发布测试。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.provider = ControllableIdleProvider(0.0)
        self.monitor = ActivityMonitor(
            event_bus=self.bus, idle_provider=self.provider
        )

    def tearDown(self) -> None:
        self.monitor.stop_polling()
        self.bus.clear()

    def test_start_stop_polling(self) -> None:
        self.assertFalse(self.monitor.is_running())
        self.monitor.start_polling(interval=0.05)
        time.sleep(0.15)
        self.assertTrue(self.monitor.is_running())
        self.monitor.stop_polling()
        self.assertFalse(self.monitor.is_running())

    def test_stop_polling_idempotent(self) -> None:
        # 未启动时停止不应报错
        self.monitor.stop_polling()
        self.monitor.stop_polling()
        # 启动后停止两次也不应报错
        self.monitor.start_polling(interval=0.05)
        time.sleep(0.1)
        self.monitor.stop_polling()
        self.monitor.stop_polling()

    def test_start_polling_ignores_if_already_running(self) -> None:
        self.monitor.start_polling(interval=0.05)
        time.sleep(0.1)
        first_thread = self.monitor._thread
        self.monitor.start_polling(interval=0.05)
        # 应保持同一个线程，不重复启动
        self.assertIs(self.monitor._thread, first_thread)

    def test_polling_calls_provider(self) -> None:
        self.monitor.start_polling(interval=0.05)
        time.sleep(0.3)
        self.monitor.stop_polling()
        # 至少被调用过多次（首次立即 + 多次间隔）
        self.assertGreater(self.provider.get_calls(), 1)

    def test_state_change_publishes_event(self) -> None:
        events: list[dict] = []
        evt = threading.Event()

        def handler(data: dict) -> None:
            events.append(data)
            evt.set()

        self.bus.subscribe(EventType.ACTIVITY_CHANGED, handler)
        try:
            self.monitor.start_polling(interval=0.05)
            # 等待初始 ACTIVE 事件
            self.assertTrue(evt.wait(timeout=2.0), "未收到初始事件")
            self.assertEqual(events[-1]["new_state"], "active")

            # 切换到 IDLE
            evt.clear()
            self.provider.set(90.0)
            self.assertTrue(evt.wait(timeout=2.0), "未收到 IDLE 事件")
            last = events[-1]
            self.assertEqual(last["new_state"], "idle")
            self.assertEqual(last["old_state"], "active")
            self.assertFalse(last["is_natural_rest"])
            self.assertAlmostEqual(last["idle_seconds"], 90.0)
        finally:
            self.bus.unsubscribe(EventType.ACTIVITY_CHANGED, handler)

    def test_natural_rest_publishes_special_flag(self) -> None:
        events: list[dict] = []
        evt = threading.Event()

        def handler(data: dict) -> None:
            events.append(data)
            evt.set()

        self.bus.subscribe(EventType.ACTIVITY_CHANGED, handler)
        try:
            self.monitor.start_polling(interval=0.05)
            self.assertTrue(evt.wait(timeout=2.0), "未收到初始事件")

            # 切换到自然休息（新阈值 300s：键鼠空闲 3 分钟仍可能是在看屏幕）
            evt.clear()
            self.provider.set(320.0)
            self.assertTrue(evt.wait(timeout=2.0), "未收到 NATURAL_REST 事件")
            last = events[-1]
            self.assertEqual(last["new_state"], "natural_rest")
            self.assertTrue(last["is_natural_rest"])
        finally:
            self.bus.unsubscribe(EventType.ACTIVITY_CHANGED, handler)

    def test_back_to_active_publishes_event(self) -> None:
        events: list[dict] = []
        evt = threading.Event()

        def handler(data: dict) -> None:
            events.append(data)
            evt.set()

        self.bus.subscribe(EventType.ACTIVITY_CHANGED, handler)
        try:
            self.monitor.start_polling(interval=0.05)
            self.assertTrue(evt.wait(timeout=2.0))

            # 进入 IDLE
            evt.clear()
            self.provider.set(80.0)
            self.assertTrue(evt.wait(timeout=2.0))

            # 回到 ACTIVE（用户输入）
            evt.clear()
            self.provider.set(1.0)
            self.assertTrue(evt.wait(timeout=2.0), "未收到回到 ACTIVE 的事件")
            last = events[-1]
            self.assertEqual(last["new_state"], "active")
            self.assertEqual(last["old_state"], "idle")
            self.assertFalse(last["is_natural_rest"])
        finally:
            self.bus.unsubscribe(EventType.ACTIVITY_CHANGED, handler)

    def test_no_duplicate_events_when_state_unchanged(self) -> None:
        events: list[dict] = []

        def handler(data: dict) -> None:
            events.append(data)

        self.bus.subscribe(EventType.ACTIVITY_CHANGED, handler)
        try:
            self.provider.set(10.0)
            self.monitor.start_polling(interval=0.05)
            # 保持 ACTIVE 状态轮询一段时间
            time.sleep(0.4)
            self.monitor.stop_polling()
            # 只有首次初始事件，不应有重复
            self.assertEqual(len(events), 1)
        finally:
            self.bus.unsubscribe(EventType.ACTIVITY_CHANGED, handler)

    def test_polling_interval_respected(self) -> None:
        """轮询间隔不应显著小于设定值（避免高频轮询）。"""
        call_times: list[float] = []
        lock = threading.Lock()

        def timing_provider() -> float:
            with lock:
                call_times.append(time.monotonic())
            return 0.0

        monitor = ActivityMonitor(event_bus=self.bus, idle_provider=timing_provider)
        try:
            monitor.start_polling(interval=0.1)
            time.sleep(0.6)
            monitor.stop_polling()
        finally:
            pass

        # 计算相邻调用间隔
        if len(call_times) >= 3:
            intervals = [
                call_times[i + 1] - call_times[i]
                for i in range(len(call_times) - 1)
            ]
            # 首次立即调用，后续应 >= ~0.08（留 20% 误差）
            for gap in intervals[1:]:
                self.assertGreaterEqual(
                    gap, 0.08, f"轮询间隔 {gap:.3f}s 小于设定值"
                )


class TestActivityMonitorCustomThresholds(unittest.TestCase):
    """自定义阈值测试。"""

    def setUp(self) -> None:
        self.bus = EventBus()
        self.provider = ControllableIdleProvider(0.0)

    def tearDown(self) -> None:
        self.bus.clear()

    def test_custom_thresholds(self) -> None:
        monitor = ActivityMonitor(
            idle_threshold=5,
            natural_rest_threshold=15,
            event_bus=self.bus,
            idle_provider=self.provider,
        )
        self.provider.set(5.0)
        self.assertEqual(monitor.get_state(), ActivityState.ACTIVE)
        self.provider.set(5.1)
        self.assertEqual(monitor.get_state(), ActivityState.IDLE)
        self.provider.set(14.9)
        self.assertEqual(monitor.get_state(), ActivityState.IDLE)
        self.provider.set(15.0)
        self.assertEqual(monitor.get_state(), ActivityState.NATURAL_REST)


class TestActivityMonitorDefaultBus(unittest.TestCase):
    """未传入 event_bus 时应使用全局单例。"""

    def test_default_bus_is_global(self) -> None:
        from app.core.event_bus import get_event_bus

        monitor = ActivityMonitor(idle_provider=ControllableIdleProvider(0.0))
        self.assertIs(monitor._bus, get_event_bus())


if __name__ == "__main__":
    unittest.main()
