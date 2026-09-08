"""计时引擎单元测试。

通过注入可控的 mock ActivityMonitor 来模拟不同活动状态，避免真实等待系统空闲检测。
"""

from __future__ import annotations

import threading
import time
import unittest

from app.core.activity_monitor import ActivityState
from app.core.event_bus import EventBus, EventType
from app.core.timer_engine import TimerEngine


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
class ControllableActivityMonitor:
    """可控的 ActivityMonitor mock，用于返回指定的活动状态。"""

    def __init__(self, initial_state: ActivityState = ActivityState.ACTIVE) -> None:
        self._state = initial_state
        self._lock = threading.Lock()
        self.calls = 0

    def get_state(self) -> ActivityState:
        with self._lock:
            self.calls += 1
            return self._state

    def set_state(self, state: ActivityState) -> None:
        with self._lock:
            self._state = state

    def get_calls(self) -> int:
        with self._lock:
            return self.calls


# ---------------------------------------------------------------------------
# 核心逻辑测试（手动 tick，不启动后台线程）
# ---------------------------------------------------------------------------
class TestTimerEngineCoreLogic(unittest.TestCase):
    """TimerEngine 核心累加逻辑测试。"""

    def setUp(self) -> None:
        self.monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        self.engine = TimerEngine(self.monitor)

    def tearDown(self) -> None:
        self.engine.stop()

    # ----- ACTIVE -----
    def test_active_seconds_accumulates_on_active(self) -> None:
        """ACTIVE 状态下 active_seconds 应正确累加。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(10):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 10.0)

    def test_active_seconds_accumulates_with_custom_delta(self) -> None:
        """自定义 delta 也能正确累加。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        self.engine.tick(2.5)
        self.engine.tick(3.0)
        self.assertEqual(self.engine.get_active_seconds(), 5.5)

    # ----- IDLE -----
    def test_idle_does_not_accumulate_active(self) -> None:
        """IDLE 状态下 active_seconds 不累加。"""
        # 先在 ACTIVE 下累加一些
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(5):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 5.0)

        # 切换到 IDLE，继续 tick
        self.monitor.set_state(ActivityState.IDLE)
        for _ in range(10):
            self.engine.tick()
        # active 不变
        self.assertEqual(self.engine.get_active_seconds(), 5.0)
        # idle 累加
        self.assertEqual(self.engine.get_idle_seconds_total(), 10.0)

    def test_idle_accumulates_idle_total(self) -> None:
        """IDLE 状态下 idle_seconds_total 正确累加。"""
        self.monitor.set_state(ActivityState.IDLE)
        for _ in range(7):
            self.engine.tick()
        self.assertEqual(self.engine.get_idle_seconds_total(), 7.0)
        self.assertEqual(self.engine.get_active_seconds(), 0.0)

    # ----- NATURAL_REST -----
    def test_natural_rest_resets_active(self) -> None:
        """NATURAL_REST 状态重置 active_seconds = 0。"""
        # 先在 ACTIVE 下累加
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(20):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 20.0)

        # 切换到 NATURAL_REST
        self.monitor.set_state(ActivityState.NATURAL_REST)
        self.engine.tick()
        # active 被重置
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        # idle 累加
        self.assertEqual(self.engine.get_idle_seconds_total(), 1.0)

    def test_natural_rest_resets_active_each_tick(self) -> None:
        """连续 NATURAL_REST tick 始终保持 active = 0。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(15):
            self.engine.tick()

        self.monitor.set_state(ActivityState.NATURAL_REST)
        for _ in range(5):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        self.assertEqual(self.engine.get_idle_seconds_total(), 5.0)

    def test_active_resumes_after_natural_rest(self) -> None:
        """NATURAL_REST 后回到 ACTIVE 可重新累加。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(10):
            self.engine.tick()

        self.monitor.set_state(ActivityState.NATURAL_REST)
        self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 0.0)

        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 3.0)

    # ----- reset -----
    def test_reset_clears_active_seconds(self) -> None:
        """reset() 正确重置 active_seconds。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(8):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 8.0)

        self.engine.reset()
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        # reset 不影响 idle
        self.assertEqual(self.engine.get_idle_seconds_total(), 0.0)

    def test_reset_does_not_affect_idle_or_uptime(self) -> None:
        """reset() 不影响 idle_seconds_total 和 app_uptime。"""
        self.monitor.set_state(ActivityState.IDLE)
        for _ in range(4):
            self.engine.tick()

        self.engine.reset()
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        self.assertEqual(self.engine.get_idle_seconds_total(), 4.0)
        self.assertEqual(self.engine.get_app_uptime(), 4.0)

    # ----- app_uptime -----
    def test_app_uptime_always_accumulates(self) -> None:
        """app_uptime 在任何状态下都累加。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        self.engine.tick()
        self.monitor.set_state(ActivityState.IDLE)
        self.engine.tick()
        self.monitor.set_state(ActivityState.NATURAL_REST)
        self.engine.tick()
        self.assertEqual(self.engine.get_app_uptime(), 3.0)

    # ----- 无效 delta -----
    def test_tick_zero_delta_does_nothing(self) -> None:
        """delta <= 0 时 tick 不做任何事。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        self.engine.tick(0)
        self.engine.tick(-5)
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        self.assertEqual(self.engine.get_app_uptime(), 0.0)


# ---------------------------------------------------------------------------
# pause / resume 测试
# ---------------------------------------------------------------------------
class TestTimerEnginePauseResume(unittest.TestCase):
    """pause() / resume() 行为测试。"""

    def setUp(self) -> None:
        self.monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        self.engine = TimerEngine(self.monitor)

    def tearDown(self) -> None:
        self.engine.stop()

    def test_pause_stops_active_accumulation(self) -> None:
        """pause() 后 active_seconds 不再累加。"""
        self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 1.0)

        self.engine.pause()
        self.assertTrue(self.engine.is_paused())
        for _ in range(5):
            self.engine.tick()
        # active 不变
        self.assertEqual(self.engine.get_active_seconds(), 1.0)

    def test_pause_stops_idle_accumulation(self) -> None:
        """pause() 后 idle_seconds_total 也不累加。"""
        self.monitor.set_state(ActivityState.IDLE)
        self.engine.tick()
        self.assertEqual(self.engine.get_idle_seconds_total(), 1.0)

        self.engine.pause()
        for _ in range(5):
            self.engine.tick()
        self.assertEqual(self.engine.get_idle_seconds_total(), 1.0)

    def test_pause_does_not_reset_active_on_natural_rest(self) -> None:
        """暂停期间 NATURAL_REST 不应重置 active_seconds。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(10):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 10.0)

        self.engine.pause()
        self.monitor.set_state(ActivityState.NATURAL_REST)
        for _ in range(3):
            self.engine.tick()
        # active 保持不变
        self.assertEqual(self.engine.get_active_seconds(), 10.0)

    def test_pause_app_uptime_continues(self) -> None:
        """暂停期间 app_uptime 仍继续累加。"""
        self.engine.tick()
        self.engine.pause()
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.engine.get_app_uptime(), 4.0)

    def test_resume_restores_accumulation(self) -> None:
        """resume() 恢复 active 累加。"""
        self.engine.tick()
        self.engine.pause()
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 1.0)

        self.engine.resume()
        self.assertFalse(self.engine.is_paused())
        for _ in range(4):
            self.engine.tick()
        self.assertEqual(self.engine.get_active_seconds(), 5.0)

    def test_pause_resume_idempotent(self) -> None:
        """重复 pause / resume 不报错。"""
        self.engine.pause()
        self.engine.pause()
        self.engine.resume()
        self.engine.resume()
        self.assertFalse(self.engine.is_paused())

    def test_pause_resume_publishes_events(self) -> None:
        """pause / resume 发布 APP_PAUSE / APP_RESUME 事件。"""
        bus = EventBus()
        events: list = []
        bus.subscribe(EventType.APP_PAUSE, lambda d: events.append(("pause", d)))
        bus.subscribe(EventType.APP_RESUME, lambda d: events.append(("resume", d)))

        engine = TimerEngine(self.monitor, event_bus=bus)
        engine.pause()
        engine.resume()

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "pause")
        self.assertEqual(events[1][0], "resume")
        bus.clear()


# ---------------------------------------------------------------------------
# get_stats 测试
# ---------------------------------------------------------------------------
class TestTimerEngineStats(unittest.TestCase):
    """get_stats() 返回值测试。"""

    def setUp(self) -> None:
        self.monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        self.engine = TimerEngine(self.monitor)

    def tearDown(self) -> None:
        self.engine.stop()

    def test_get_stats_returns_all_fields(self) -> None:
        stats = self.engine.get_stats()
        self.assertIn("active_seconds", stats)
        self.assertIn("idle_seconds_total", stats)
        self.assertIn("app_uptime", stats)
        self.assertIn("is_running", stats)
        self.assertIn("is_paused", stats)

    def test_get_stats_reflects_values(self) -> None:
        self.monitor.set_state(ActivityState.ACTIVE)
        for _ in range(5):
            self.engine.tick()
        self.monitor.set_state(ActivityState.IDLE)
        for _ in range(3):
            self.engine.tick()

        stats = self.engine.get_stats()
        self.assertEqual(stats["active_seconds"], 5.0)
        self.assertEqual(stats["idle_seconds_total"], 3.0)
        self.assertEqual(stats["app_uptime"], 8.0)
        self.assertFalse(stats["is_running"])
        self.assertFalse(stats["is_paused"])

    def test_get_stats_reflects_paused(self) -> None:
        self.engine.pause()
        stats = self.engine.get_stats()
        self.assertTrue(stats["is_paused"])


# ---------------------------------------------------------------------------
# 后台线程测试
# ---------------------------------------------------------------------------
class TestTimerEngineThreading(unittest.TestCase):
    """后台 tick 线程测试。"""

    def setUp(self) -> None:
        self.monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        self.engine = TimerEngine(self.monitor)

    def tearDown(self) -> None:
        self.engine.stop()

    def test_start_stop_lifecycle(self) -> None:
        """start / stop 生命周期。"""
        self.assertFalse(self.engine.is_running())
        self.engine.start(interval=0.1)
        time.sleep(0.3)
        self.assertTrue(self.engine.is_running())
        self.engine.stop()
        self.assertFalse(self.engine.is_running())

    def test_start_idempotent(self) -> None:
        """重复 start 不创建多个线程。"""
        self.engine.start(interval=0.1)
        first_thread = self.engine._thread
        self.engine.start(interval=0.1)
        self.assertIs(self.engine._thread, first_thread)

    def test_stop_idempotent(self) -> None:
        """未启动时 stop 不报错。"""
        self.engine.stop()
        self.engine.stop()

    def test_background_thread_accumulates(self) -> None:
        """后台线程自动累加 active_seconds。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        self.engine.start(interval=0.05)
        time.sleep(0.35)
        self.engine.stop()
        # 至少累加了几次（首次立即 + 多次间隔）
        self.assertGreater(self.engine.get_active_seconds(), 0.0)
        self.assertGreater(self.monitor.get_calls(), 1)

    def test_background_thread_respects_state_change(self) -> None:
        """后台线程响应状态变化。"""
        self.monitor.set_state(ActivityState.ACTIVE)
        self.engine.start(interval=0.05)
        time.sleep(0.15)
        active_before = self.engine.get_active_seconds()

        # 切换到 NATURAL_REST
        self.monitor.set_state(ActivityState.NATURAL_REST)
        time.sleep(0.15)
        self.engine.stop()
        # active 应被重置
        self.assertEqual(self.engine.get_active_seconds(), 0.0)
        self.assertGreater(active_before, 0.0)


# ---------------------------------------------------------------------------
# 线程安全测试
# ---------------------------------------------------------------------------
class TestTimerEngineThreadSafety(unittest.TestCase):
    """多线程并发 tick 线程安全测试。"""

    def test_concurrent_ticks_no_loss(self) -> None:
        """多线程并发 tick 不丢失累加。"""
        monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        engine = TimerEngine(monitor)

        num_threads = 8
        ticks_per_thread = 500
        barrier = threading.Barrier(num_threads)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                barrier.wait()
                for _ in range(ticks_per_thread):
                    engine.tick()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(errors, [])
        # 总共应该累加 num_threads * ticks_per_thread
        expected = float(num_threads * ticks_per_thread)
        self.assertEqual(engine.get_active_seconds(), expected)
        self.assertEqual(engine.get_app_uptime(), expected)

    def test_concurrent_ticks_with_state_changes(self) -> None:
        """并发 tick + 状态切换下不崩溃，最终值一致。"""
        monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        engine = TimerEngine(monitor)

        num_threads = 4
        ticks_per_thread = 300
        barrier = threading.Barrier(num_threads)

        def ticker(state: ActivityState) -> None:
            monitor.set_state(state)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return
            for _ in range(ticks_per_thread):
                engine.tick()

        states = [
            ActivityState.ACTIVE,
            ActivityState.IDLE,
            ActivityState.NATURAL_REST,
            ActivityState.ACTIVE,
        ]
        threads = [
            threading.Thread(target=ticker, args=(s,)) for s in states
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # 主要验证不崩溃、值不为负
        self.assertGreaterEqual(engine.get_active_seconds(), 0.0)
        self.assertGreaterEqual(engine.get_idle_seconds_total(), 0.0)
        self.assertEqual(
            engine.get_app_uptime(), float(num_threads * ticks_per_thread)
        )

    def test_concurrent_pause_resume_tick(self) -> None:
        """并发 pause / resume / tick 不崩溃。"""
        monitor = ControllableActivityMonitor(ActivityState.ACTIVE)
        engine = TimerEngine(monitor)

        stop_event = threading.Event()

        def ticker() -> None:
            while not stop_event.is_set():
                engine.tick()

        def pauser() -> None:
            while not stop_event.is_set():
                engine.pause()
                time.sleep(0.001)
                engine.resume()
                time.sleep(0.001)

        ticker_threads = [
            threading.Thread(target=ticker) for _ in range(4)
        ]
        pauser_threads = [threading.Thread(target=pauser) for _ in range(2)]
        for t in ticker_threads + pauser_threads:
            t.start()
        time.sleep(0.3)
        stop_event.set()
        for t in ticker_threads + pauser_threads:
            t.join(timeout=5.0)

        # 不崩溃即为通过，值应合理
        self.assertGreaterEqual(engine.get_app_uptime(), 0.0)
        self.assertGreaterEqual(engine.get_active_seconds(), 0.0)


# ---------------------------------------------------------------------------
# get_state 异常容错测试
# ---------------------------------------------------------------------------
class TestTimerEngineErrorHandling(unittest.TestCase):
    """ActivityMonitor.get_state() 异常时的容错测试。"""

    def test_get_state_exception_skips_tick(self) -> None:
        """get_state 抛异常时，active 不累加，但 app_uptime 仍累加。"""

        class BrokenMonitor:
            def get_state(self) -> ActivityState:
                raise RuntimeError("模拟系统调用失败")

        engine = TimerEngine(BrokenMonitor())
        engine.tick()
        engine.tick()
        # app_uptime 仍累加
        self.assertEqual(engine.get_app_uptime(), 2.0)
        # active 不累加
        self.assertEqual(engine.get_active_seconds(), 0.0)


if __name__ == "__main__":
    unittest.main()
