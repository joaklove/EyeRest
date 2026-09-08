"""异常场景测试（Task 19）。

覆盖以下异常与边界场景：

a) 锁屏/睡眠/唤醒异常场景
b) 跨午夜场景
c) 长时间 Idle 场景
d) 持久化异常场景（V0.5：config.json / stats.json）
e) 程序重启后的数据保持（替代原崩溃恢复）
f) EventBus 异常容错
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock

from app.config import defaults
from app.config.settings_store import JsonSettingsStore
from app.services.stats_store import StatsStore
from app.core.activity_monitor import ActivityMonitor, ActivityState
from app.core.break_engine import BreakEngine
from app.core.event_bus import EventBus, EventType
from app.core.state_machine import AppState, StateMachine
from app.core.timer_engine import TimerEngine
from app.services.statistics_service import StatisticsService
from app.services.usage_service import UsageService


# ===========================================================================
# 测试辅助 Mock
# ===========================================================================
class MockTimerEngine:
    """可控的 TimerEngine mock，记录所有调用。"""

    def __init__(self) -> None:
        self.reset_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self._active_seconds: float = 0.0
        self._idle_seconds: float = 0.0
        self._paused: bool = False
        self._lock = threading.Lock()

    def tick(self, delta_seconds: float = 1.0) -> None:
        with self._lock:
            if not self._paused:
                self._active_seconds += delta_seconds

    def add_idle(self, delta: float = 1.0) -> None:
        with self._lock:
            self._idle_seconds += delta

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

    def get_idle_seconds_total(self) -> float:
        with self._lock:
            return self._idle_seconds

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused


class ControllableIdleProvider:
    """可控的空闲秒数提供者。"""

    def __init__(self, initial: float = 0.0) -> None:
        self._value = float(initial)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)


class FakeTimerEngine:
    """用于 BreakEngine 测试的轻量 fake。"""

    def __init__(self) -> None:
        self.active_seconds: float = 0.0
        self.reset_calls: int = 0

    def get_active_seconds(self) -> float:
        return self.active_seconds

    def reset(self) -> None:
        self.reset_calls += 1
        self.active_seconds = 0.0


def _utcnow() -> datetime:
    """返回 naive UTC 当前时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_sm(
    timer_engine: Optional[MockTimerEngine] = None,
    activity_monitor: Optional[ControllableIdleProvider] = None,
) -> tuple[StateMachine, MockTimerEngine, EventBus]:
    """构造状态机及其依赖。"""
    bus = EventBus()
    timer = timer_engine if timer_engine is not None else MockTimerEngine()
    am = MagicMock()
    if activity_monitor is not None:
        am.get_idle_seconds = activity_monitor.__call__
    sm = StateMachine(
        timer_engine=timer,
        event_bus=bus,
        activity_monitor=am,
    )
    return sm, timer, bus


# ===========================================================================
# a) 锁屏/睡眠/唤醒异常场景
# ===========================================================================
class TestLockSleepWakeExceptions(unittest.TestCase):
    """锁屏、睡眠、唤醒异常场景测试。"""

    def test_lock_unlock_short_idle_continues_timing(self) -> None:
        """锁屏 → 解锁（空闲 < 120s）→ 继续计时，active_seconds 保留。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(30):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 30.0)

        sm.on_system_lock()
        self.assertEqual(sm.get_state(), AppState.LOCKED)
        self.assertTrue(timer.is_paused())

        # 解锁，空闲 < 120s，不重置
        sm.on_system_unlock(idle_seconds=60.0)
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertFalse(timer.is_paused())
        self.assertEqual(timer.get_active_seconds(), 30.0)

    def test_lock_unlock_long_idle_resets_timing(self) -> None:
        """锁屏 → 解锁（空闲 ≥ 120s）→ 重置计时，active_seconds 归零。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(45):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 45.0)

        sm.on_system_lock()
        # 解锁，空闲 >= 120s，重置
        sm.on_system_unlock(idle_seconds=defaults.NATURAL_REST_THRESHOLD)
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_sleep_wake_short_idle_continues_timing(self) -> None:
        """睡眠 → 唤醒（空闲 < 120s）→ 继续计时，active_seconds 保留。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(20):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 20.0)

        sm.on_system_sleep()
        self.assertEqual(sm.get_state(), AppState.SLEEP)
        self.assertTrue(timer.is_paused())

        # 唤醒，空闲 < 120s，不重置
        sm.on_system_wake(idle_seconds=30.0)
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertFalse(timer.is_paused())
        self.assertEqual(timer.get_active_seconds(), 20.0)

    def test_sleep_wake_long_idle_resets_timing(self) -> None:
        """睡眠 → 唤醒（空闲 ≥ 300s）→ 重置计时，active_seconds 归零。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(50):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 50.0)

        sm.on_system_sleep()
        sm.on_system_wake(idle_seconds=320.0)
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_break_not_triggered_during_lock(self) -> None:
        """锁屏期间休息触发不应生效（LOCKED 状态拒绝 break_warning）。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(100):
            timer.tick()

        sm.on_system_lock()
        self.assertEqual(sm.get_state(), AppState.LOCKED)

        # 尝试触发休息警告 — 应被拒绝
        self.assertFalse(sm.on_break_warning())
        self.assertEqual(sm.get_state(), AppState.LOCKED)

        # 尝试触发休息 — 应被拒绝
        self.assertFalse(sm.on_break_triggered())
        self.assertEqual(sm.get_state(), AppState.LOCKED)

    def test_state_not_changed_during_sleep(self) -> None:
        """睡眠期间活动状态变化不应改变应用状态。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertEqual(sm.get_state(), AppState.SLEEP)

        # 活动状态变化应被忽略
        self.assertTrue(sm.on_activity_changed(ActivityState.ACTIVE))
        self.assertEqual(sm.get_state(), AppState.SLEEP)

        self.assertTrue(sm.on_activity_changed(ActivityState.IDLE))
        self.assertEqual(sm.get_state(), AppState.SLEEP)

        # 自然休息也不触发
        self.assertFalse(sm.on_natural_rest())
        self.assertEqual(sm.get_state(), AppState.SLEEP)

    def test_lock_during_break_warning_allowed(self) -> None:
        """BREAK_WARNING 状态下锁屏允许（用户离开时自动暂停）。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        sm.on_break_warning()
        self.assertEqual(sm.get_state(), AppState.BREAK_WARNING)

        self.assertTrue(sm.on_system_lock())
        self.assertEqual(sm.get_state(), AppState.LOCKED)
        self.assertTrue(timer.is_paused())

    def test_sleep_during_break_warning_allowed(self) -> None:
        """BREAK_WARNING 状态下睡眠允许。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        sm.on_break_warning()

        self.assertTrue(sm.on_system_sleep())
        self.assertEqual(sm.get_state(), AppState.SLEEP)
        self.assertTrue(timer.is_paused())

    def test_lock_then_sleep_then_wake_then_unlock(self) -> None:
        """锁屏 → 睡眠 → 唤醒 → 解锁 完整序列。"""
        sm, timer, _ = _make_sm()
        sm.start_protection()
        for _ in range(20):
            timer.tick()

        # 锁屏
        sm.on_system_lock()
        self.assertEqual(sm.get_state(), AppState.LOCKED)

        # 锁屏后睡眠
        sm.on_system_sleep()
        self.assertEqual(sm.get_state(), AppState.SLEEP)

        # 唤醒 → 回到 ACTIVE（从 SLEEP 直接回到 ACTIVE）
        sm.on_system_wake(idle_seconds=60.0)
        self.assertEqual(sm.get_state(), AppState.ACTIVE)

        # 累积时间应保留（空闲 < 120s 不重置）
        self.assertEqual(timer.get_active_seconds(), 20.0)


# ===========================================================================
# b) 跨午夜场景
# ===========================================================================
class TestCrossMidnightScenarios(unittest.TestCase):
    """跨午夜场景（V0.5：JSON 统计按本地日期归档）。"""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_midnight_"))
        self.store = StatsStore(self._tmpdir / "stats.json")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _yesterday(self) -> str:
        return (date.today() - timedelta(days=1)).isoformat()

    def _today(self) -> str:
        return date.today().isoformat()

    def test_new_day_starts_with_zero_counters(self) -> None:
        """进入新的一天时统计从零开始，不继承昨天。"""
        # 昨天有数据
        self.store._days[self._yesterday()] = {
            "screen_seconds": 3600.0,
            "blink_cue": 50,
            "look_away": 10,
            "move": 3,
            "deep_break": 1,
            "skipped_break": 0,
        }
        self.store.flush()

        reloaded = StatsStore(self._tmpdir / "stats.json")
        today = reloaded.today()
        self.assertEqual(today["date"], self._today())
        self.assertEqual(today["blink_cue"], 0)
        # 昨天的数据仍在
        self.assertEqual(reloaded.get_day(self._yesterday())["blink_cue"], 50)

    def test_statistics_service_returns_correct_date_stats(self) -> None:
        """按日期查询返回各自的数据。"""
        self.store._days[self._yesterday()] = {
            "screen_seconds": 1800.0,
            "blink_cue": 20,
            "look_away": 5,
            "move": 1,
            "deep_break": 0,
            "skipped_break": 2,
        }
        self.store.flush()

        service = StatisticsService(store=self.store)
        yesterday = service.get_daily_stats(self._yesterday())
        today = service.get_daily_stats(self._today())

        self.assertEqual(yesterday["total_active_seconds"], 1800.0)
        self.assertEqual(yesterday["total_skipped_breaks"], 2)
        self.assertEqual(today["total_active_seconds"], 0.0)
        self.assertEqual(today["total_skipped_breaks"], 0)

    def test_weekly_window_includes_yesterday(self) -> None:
        """周统计窗口包含昨天与今天。"""
        self.store._days[self._yesterday()] = {
            "screen_seconds": 7200.0,
            "blink_cue": 100,
            "look_away": 12,
            "move": 4,
            "deep_break": 2,
            "skipped_break": 1,
        }
        self.store.flush()

        service = StatisticsService(store=self.store)
        weekly = service.get_weekly_stats()
        dates = [row["date"] for row in weekly]

        self.assertIn(self._yesterday(), dates)
        self.assertIn(self._today(), dates)

    def test_usage_service_writes_to_today(self) -> None:
        """会话结束时把时长累计到今天。"""

        class _Timer:
            def __init__(self) -> None:
                self.active_seconds = 0.0

        timer = _Timer()
        service = UsageService(timer_engine=timer, store=self.store)
        service.start_session()
        timer.active_seconds = 120.0
        service.end_session()

        today = StatsStore(self._tmpdir / "stats.json").today()
        self.assertAlmostEqual(today["screen_seconds"], 120.0, places=1)

        summary = service.get_today_summary()
        self.assertAlmostEqual(summary["active_seconds"], 120.0, places=1)


class TestLongIdleScenarios(unittest.TestCase):
    """长时间 Idle 场景测试。"""

    def test_activity_monitor_long_idle_natural_rest(self) -> None:
        """ActivityMonitor 空闲 > 120s → NATURAL_REST 状态。"""
        bus = EventBus()
        idle_provider = ControllableIdleProvider(0.0)
        monitor = ActivityMonitor(
            event_bus=bus,
            idle_provider=idle_provider,
        )

        # 初始状态应为 ACTIVE
        self.assertEqual(monitor.get_state(), ActivityState.ACTIVE)

        # 空闲 < 60s → ACTIVE
        idle_provider.set(30.0)
        self.assertEqual(monitor.get_state(), ActivityState.ACTIVE)

        # 60s < 空闲 < 120s → IDLE
        idle_provider.set(90.0)
        self.assertEqual(monitor.get_state(), ActivityState.IDLE)

        # 空闲 >= 120s → NATURAL_REST
        idle_provider.set(defaults.NATURAL_REST_THRESHOLD)
        self.assertEqual(monitor.get_state(), ActivityState.NATURAL_REST)

        # 空闲远超 120s
        idle_provider.set(600.0)
        self.assertEqual(monitor.get_state(), ActivityState.NATURAL_REST)

    def test_timer_engine_natural_rest_no_active_accumulation(self) -> None:
        """TimerEngine 在 NATURAL_REST 期间不累加 active_seconds。"""
        events: list[tuple[EventType, object]] = []

        class StateMonitor:
            def __init__(self) -> None:
                self._state = ActivityState.ACTIVE

            def get_state(self) -> ActivityState:
                return self._state

            def set_state(self, state: ActivityState) -> None:
                self._state = state

        bus = EventBus()
        monitor = StateMonitor()
        engine = TimerEngine(monitor, event_bus=bus)

        # ACTIVE 状态累加
        monitor.set_state(ActivityState.ACTIVE)
        for _ in range(10):
            engine.tick()
        self.assertEqual(engine.get_active_seconds(), 10.0)

        # 切换到 NATURAL_REST — active_seconds 应归零
        monitor.set_state(ActivityState.NATURAL_REST)
        engine.tick()
        self.assertEqual(engine.get_active_seconds(), 0.0)

        # NATURAL_REST 期间继续 tick — active 不应累加
        for _ in range(20):
            engine.tick()
        self.assertEqual(engine.get_active_seconds(), 0.0)

        # idle_seconds 应累加
        self.assertGreater(engine.get_idle_seconds_total(), 0.0)

        engine.stop()

    def test_break_engine_natural_rest_resets_no_count_increment(self) -> None:
        """BreakEngine 在 NATURAL_REST 时重置但不增加休息计数。"""
        timer = FakeTimerEngine()
        timer.active_seconds = 600.0  # 接近触发阈值

        state_machine = MagicMock()
        bus = EventBus()
        engine = BreakEngine(
            timer_engine=timer,
            state_machine=state_machine,
            event_bus=bus,
        )
        engine.start()

        break_count_before = engine.get_break_count_today()

        # 发布 ACTIVITY_CHANGED 事件，标记为自然休息
        bus.publish(
            EventType.ACTIVITY_CHANGED,
            {
                "old_state": "idle",
                "new_state": "natural_rest",
                "idle_seconds": 150.0,
                "is_natural_rest": True,
            },
        )

        # 验证 active_seconds 被重置
        self.assertEqual(timer.active_seconds, 0.0)
        self.assertGreater(timer.reset_calls, 0)

        # 验证休息计数未增加
        self.assertEqual(engine.get_break_count_today(), break_count_before)

        # 验证状态机被调用
        state_machine.on_natural_rest.assert_called_once()

        engine.stop()

    def test_break_engine_natural_rest_resets_warning_and_postpone(self) -> None:
        """自然休息重置警告状态和延迟计数。"""
        timer = FakeTimerEngine()
        timer.active_seconds = 1000.0

        state_machine = MagicMock()
        bus = EventBus()
        engine = BreakEngine(
            timer_engine=timer,
            state_machine=state_machine,
            event_bus=bus,
        )
        engine.start()

        # 制造延迟
        engine.on_postpone()
        self.assertEqual(engine.get_postpone_count(), 1)

        # 触发自然休息
        bus.publish(
            EventType.ACTIVITY_CHANGED,
            {"is_natural_rest": True, "idle_seconds": 150.0},
        )

        # 延迟计数应归零
        self.assertEqual(engine.get_postpone_count(), 0)
        # 警告状态应归零
        self.assertFalse(engine.is_warning_triggered())

        engine.stop()


# ===========================================================================
# f) EventBus 异常容错
# ===========================================================================
class TestEventBusFaultTolerance(unittest.TestCase):
    """EventBus 异常容错测试。"""

    def test_subscriber_exception_does_not_affect_others(self) -> None:
        """订阅者抛异常时不影响其他订阅者。"""
        bus = EventBus()

        received: list[str] = []
        received_lock = threading.Lock()

        def good_handler(data) -> None:
            with received_lock:
                received.append("good")

        def bad_handler(data) -> None:
            raise RuntimeError("subscriber error")

        def another_good_handler(data) -> None:
            with received_lock:
                received.append("another_good")

        bus.subscribe(EventType.STATE_CHANGED, bad_handler)
        bus.subscribe(EventType.STATE_CHANGED, good_handler)
        bus.subscribe(EventType.STATE_CHANGED, another_good_handler)

        # 发布事件 — bad_handler 抛异常不应影响 good 和 another_good
        bus.publish(EventType.STATE_CHANGED, {"test": True})

        self.assertIn("good", received)
        self.assertIn("another_good", received)
        self.assertEqual(received.count("good"), 1)
        self.assertEqual(received.count("another_good"), 1)

    def test_publish_no_subscribers_does_not_crash(self) -> None:
        """发布没有订阅者的事件类型不崩溃。"""
        bus = EventBus()

        # 发布一个没有订阅者的事件
        bus.publish(EventType.BREAK_WARNING, {"test": True})
        bus.publish(EventType.SETTINGS_CHANGED, {"test": True})
        bus.publish(EventType.APP_PAUSE, {"test": True})

        # 不抛异常即通过

    def test_publish_with_none_data(self) -> None:
        """发布 None 数据不崩溃。"""
        bus = EventBus()

        received: list = []
        bus.subscribe(EventType.ACTIVITY_CHANGED, lambda data: received.append(data))

        bus.publish(EventType.ACTIVITY_CHANGED, None)
        self.assertEqual(len(received), 1)
        self.assertIsNone(received[0])

    def test_multiple_exception_subscribers(self) -> None:
        """多个订阅者都抛异常时，不崩溃且不影响后续发布。"""
        bus = EventBus()

        def bad1(data) -> None:
            raise ValueError("bad1")

        def bad2(data) -> None:
            raise TypeError("bad2")

        received: list[str] = []

        def good(data) -> None:
            received.append("ok")

        bus.subscribe(EventType.STATE_CHANGED, bad1)
        bus.subscribe(EventType.STATE_CHANGED, bad2)
        bus.subscribe(EventType.STATE_CHANGED, good)

        # 第一次发布 — bad1 和 bad2 抛异常，good 仍应执行
        bus.publish(EventType.STATE_CHANGED, {})
        self.assertEqual(received, ["ok"])

        # 第二次发布 — 仍不崩溃
        bus.publish(EventType.STATE_CHANGED, {})
        self.assertEqual(received, ["ok", "ok"])

    def test_unsubscribe_during_publish_safe(self) -> None:
        """回调中取消订阅不导致崩溃。"""
        bus = EventBus()

        received: list[str] = []

        def handler1(data) -> None:
            received.append("h1")

        def handler2(data) -> None:
            received.append("h2")
            bus.unsubscribe(EventType.STATE_CHANGED, handler1)

        bus.subscribe(EventType.STATE_CHANGED, handler1)
        bus.subscribe(EventType.STATE_CHANGED, handler2)

        # 发布事件 — handler2 在执行中取消 handler1 的订阅
        bus.publish(EventType.STATE_CHANGED, {})

        # 两个 handler 都应被执行（因为 handlers 在发布前已复制）
        self.assertEqual(received, ["h1", "h2"])

        # 再次发布 — handler1 已被取消
        received.clear()
        bus.publish(EventType.STATE_CHANGED, {})
        self.assertEqual(received, ["h2"])

    def test_clear_all_subscribers(self) -> None:
        """clear() 清空所有订阅后不再有回调。"""
        bus = EventBus()

        received: list = []
        bus.subscribe(EventType.STATE_CHANGED, lambda d: received.append(d))

        bus.clear()

        bus.publish(EventType.STATE_CHANGED, {"test": True})
        self.assertEqual(received, [])


# ===========================================================================
# 辅助 Repository（用于 SettingRepository 测试）
# ===========================================================================
# ===========================================================================
# d) 持久化异常场景（config.json / stats.json）
# ===========================================================================
class TestPersistenceExceptions(unittest.TestCase):
    """V0.5 持久化层异常场景：配置与统计文件在各种损坏下都不能拖垮应用。"""

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_persist_"))

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_config_file_auto_created(self) -> None:
        """配置文件与目录不存在时自动创建。"""
        path = self._tmpdir / "subdir" / "config.json"
        self.assertFalse(path.exists())

        store = JsonSettingsStore(path)
        # 首次启动构造即落盘空配置（{} = 全部走默认值），用户可见可改
        self.assertTrue(path.exists())
        self.assertEqual(store.all(), {})

        store.set("blink_cycle_seconds", 90)
        self.assertTrue(path.exists())

        reloaded = JsonSettingsStore(path)
        self.assertEqual(reloaded.get("blink_cycle_seconds"), 90)

    def test_config_corrupted_graceful_recovery(self) -> None:
        """配置文件损坏（非法 JSON）时回退默认值，不抛异常。"""
        path = self._tmpdir / "config.json"
        path.write_text("{ this is not json", encoding="utf-8")

        store = JsonSettingsStore(path)
        self.assertEqual(store.get("anything", "fallback"), "fallback")
        # 仍可正常写入，文件自愈
        store.set("enable_sound", True)
        self.assertTrue(JsonSettingsStore(path).get("enable_sound"))

    def test_config_empty_file_recovery(self) -> None:
        """空文件不应导致崩溃。"""
        path = self._tmpdir / "config.json"
        path.write_text("", encoding="utf-8")
        store = JsonSettingsStore(path)
        self.assertEqual(store.all(), {})

    def test_stats_corrupted_graceful_recovery(self) -> None:
        """统计文件损坏时重置为空统计，不抛异常。"""
        path = self._tmpdir / "stats.json"
        path.write_text("not-json-at-all", encoding="utf-8")

        store = StatsStore(path)
        today = store.today()
        self.assertEqual(today["blink_cue"], 0)
        store.record("blink_cue")
        self.assertEqual(StatsStore(path).today()["blink_cue"], 1)

    def test_stats_wrong_shape_recovery(self) -> None:
        """统计文件结构异常（缺 days 键）时安全重置。"""
        path = self._tmpdir / "stats.json"
        path.write_text('{"unexpected": 1}', encoding="utf-8")
        store = StatsStore(path)
        self.assertEqual(store.today()["look_away"], 0)

    def test_restart_keeps_data(self) -> None:
        """模拟重启：重新打开文件后数据仍在（替代原崩溃恢复测试）。"""
        cfg_path = self._tmpdir / "config.json"
        stats_path = self._tmpdir / "stats.json"

        cfg = JsonSettingsStore(cfg_path)
        cfg.set("move_interval", 3600)
        stats = StatsStore(stats_path)
        stats.record("deep_break")
        stats.add_screen_seconds(600.0)
        stats.flush()

        # "重启"
        cfg2 = JsonSettingsStore(cfg_path)
        stats2 = StatsStore(stats_path)
        self.assertEqual(cfg2.get("move_interval"), 3600)
        self.assertEqual(stats2.today()["deep_break"], 1)
        self.assertAlmostEqual(stats2.today()["screen_seconds"], 600.0, places=1)

    def test_concurrent_writes_do_not_corrupt(self) -> None:
        """多线程并发写入后文件仍可被解析（原子写保证）。"""
        path = self._tmpdir / "config.json"
        store = JsonSettingsStore(path)

        def _worker(index: int) -> None:
            for i in range(20):
                store.set(f"key_{index}_{i}", i)

        threads = [threading.Thread(target=_worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 文件必须仍是合法 JSON
        reloaded = JsonSettingsStore(path)
        self.assertGreater(len(reloaded.all()), 0)

    def test_no_temp_files_left_behind(self) -> None:
        """原子写不应在数据目录留下临时文件。"""
        path = self._tmpdir / "config.json"
        store = JsonSettingsStore(path)
        store.set("a", 1)
        store.set("b", 2)
        leftovers = [p.name for p in self._tmpdir.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
