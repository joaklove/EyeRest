"""BreakEngine 单元测试。

覆盖 20-20-20 规则的完整流程、边界条件、延迟机制、自然休息与事件发布。
使用 FakeTimerEngine 与 MockStateMachine 注入，避免真实系统依赖。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.config import defaults
from app.core.activity_monitor import ActivityMonitor
from app.core.break_engine import BreakEngine
from app.core.event_bus import EventBus, EventType


class FakeTimerEngine:
    """可控的 Fake TimerEngine，便于精确设置 active_seconds。"""

    def __init__(self) -> None:
        self.active_seconds: float = 0.0
        self.reset_calls: int = 0
        self.pause_calls: int = 0
        self.resume_calls: int = 0

    def get_active_seconds(self) -> float:
        return self.active_seconds

    def reset(self) -> None:
        self.reset_calls += 1
        self.active_seconds = 0.0

    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self) -> None:
        self.resume_calls += 1


class TestBreakEngine(unittest.TestCase):
    """BreakEngine 单元测试。"""

    def setUp(self) -> None:
        self.timer = FakeTimerEngine()
        self.state = MagicMock()
        self.bus = EventBus()
        # 记录所有发布的事件：EventBus 回调只传 data，需通过闭包捕获 event_type
        self.events: list[tuple[EventType, object]] = []
        for et in (
            EventType.BREAK_WARNING,
            EventType.BREAK_TRIGGERED,
            EventType.BREAK_COMPLETED,
            EventType.BREAK_SKIPPED,
            EventType.ACTIVITY_CHANGED,
        ):
            self.bus.subscribe(et, self._make_recorder(et))

        self.engine = BreakEngine(
            timer_engine=self.timer,
            state_machine=self.state,
            event_bus=self.bus,
        )
        self.engine.start()

    def _make_recorder(self, event_type: EventType):
        """生成一个记录 (event_type, data) 的订阅回调。"""

        def _record(data: object) -> None:
            self.events.append((event_type, data))

        return _record

    def tearDown(self) -> None:
        self.engine.stop()

    # ------------------------------------------------------------------
    # 基础生命周期
    # ------------------------------------------------------------------
    def test_start_stop_idempotent(self) -> None:
        self.assertTrue(self.engine.is_running())
        self.engine.start()  # 二次启动忽略
        self.assertTrue(self.engine.is_running())
        self.engine.stop()
        self.assertFalse(self.engine.is_running())
        self.engine.stop()  # 二次停止忽略
        self.assertFalse(self.engine.is_running())

    def test_tick_when_not_running_noop(self) -> None:
        self.engine.stop()
        self.timer.active_seconds = 9999
        self.engine.tick()  # 不应触发任何状态调用
        self.state.on_break_warning.assert_not_called()
        self.state.on_break_triggered.assert_not_called()

    # ------------------------------------------------------------------
    # 20-20-20 短休息完整流程
    # ------------------------------------------------------------------
    def test_short_break_full_flow(self) -> None:
        """20min → 30s warning → 20s break → reset 流程正确。"""
        work = defaults.SHORT_WORK_DURATION  # 1200
        warn = defaults.WARNING_DURATION  # 30

        # 1. active 未达警告阈值：无动作
        self.timer.active_seconds = work - warn - 1  # 1169
        self.engine.tick()
        self.state.on_break_warning.assert_not_called()
        self.assertFalse(self.engine.is_warning_triggered())

        # 2. active 达到警告阈值：触发警告
        self.timer.active_seconds = work - warn  # 1170
        self.engine.tick()
        self.state.on_break_warning.assert_called_once()
        self.assertTrue(self.engine.is_warning_triggered())
        self.assertEqual(self.engine.get_current_break_type(), "short")
        self.assertEqual(self.engine.get_remaining_seconds(), warn)
        self.assertTrue(
            any(et == EventType.BREAK_WARNING for et, _ in self.events)
        )

        # 3. 警告已触发后再次 tick 不重复触发
        self.engine.tick()
        self.state.on_break_warning.assert_called_once()

        # 4. active 达到工作时长：触发休息
        self.timer.active_seconds = work  # 1200
        self.engine.tick()
        self.state.on_break_triggered.assert_called_once_with("short")
        self.assertTrue(self.engine.is_break_in_progress())
        self.assertFalse(self.engine.is_warning_triggered())
        self.assertTrue(
            any(et == EventType.BREAK_TRIGGERED for et, _ in self.events)
        )

        # 5. 休息中 tick 不应触发新休息
        self.timer.active_seconds = work + 100
        self.engine.tick()
        self.state.on_break_triggered.assert_called_once()

        # 6. 休息完成：重置 active_time，计数 +1
        self.engine.on_break_complete()
        self.state.on_break_completed.assert_called_once()
        self.assertEqual(self.timer.reset_calls, 1)
        self.assertEqual(self.timer.active_seconds, 0.0)
        self.assertFalse(self.engine.is_break_in_progress())
        self.assertEqual(self.engine.get_break_count_today(), 1)
        self.assertEqual(self.engine.get_short_break_count(), 1)
        self.assertTrue(
            any(et == EventType.BREAK_COMPLETED for et, _ in self.events)
        )

    # ------------------------------------------------------------------
    # should_warn / should_break 边界条件
    # ------------------------------------------------------------------
    def test_should_warn_boundary(self) -> None:
        work = defaults.SHORT_WORK_DURATION
        warn = defaults.WARNING_DURATION

        # 刚好低于警告阈值
        self.timer.active_seconds = work - warn - 0.01
        self.assertFalse(self.engine.should_warn())
        # 刚好达到警告阈值
        self.timer.active_seconds = work - warn
        self.assertTrue(self.engine.should_warn())
        # 警告区间内
        self.timer.active_seconds = work - 1
        self.assertTrue(self.engine.should_warn())
        # 达到工作时长：不再警告（应休息）
        self.timer.active_seconds = work
        self.assertFalse(self.engine.should_warn())
        # 超过工作时长
        self.timer.active_seconds = work + 10
        self.assertFalse(self.engine.should_warn())

    def test_should_break_boundary(self) -> None:
        work = defaults.SHORT_WORK_DURATION

        # 刚好低于工作时长
        self.timer.active_seconds = work - 0.01
        self.assertFalse(self.engine.should_break())
        # 刚好达到工作时长
        self.timer.active_seconds = work
        self.assertTrue(self.engine.should_break())
        # 超过工作时长
        self.timer.active_seconds = work + 100
        self.assertTrue(self.engine.should_break())

    def test_remaining_seconds(self) -> None:
        work = defaults.SHORT_WORK_DURATION
        self.timer.active_seconds = 0
        self.assertEqual(self.engine.get_remaining_seconds(), work)
        self.timer.active_seconds = work - 50
        self.assertEqual(self.engine.get_remaining_seconds(), 50)
        self.timer.active_seconds = work + 100
        # 不会小于 0
        self.assertEqual(self.engine.get_remaining_seconds(), 0.0)

    # ------------------------------------------------------------------
    # 深度休息（V1.0 正式版：与短休息解耦，独立长周期账本驱动）
    # ------------------------------------------------------------------
    def test_deep_break_independent_of_short_count(self) -> None:
        """深度休息由独立长周期账本驱动：即使短休息被禁用（阈值极大），
        暴露时长达到 90 分钟也会触发深度休息。"""
        engine = BreakEngine(
            timer_engine=self.timer,
            state_machine=self.state,
            event_bus=self.bus,
            config={"SHORT_WORK_DURATION": 999999},  # 实际屏蔽短休息
        )
        engine.start()
        try:
            long_work = defaults.LONG_WORK_DURATION
            self.timer.active_seconds = long_work - 1
            engine.tick()
            self.state.on_break_triggered.assert_not_called()

            self.timer.active_seconds = long_work
            engine.tick()
            self.state.on_break_triggered.assert_called_once_with("long")
            engine.on_break_complete()

            # 深度休息完成后长周期账本清零
            self.assertAlmostEqual(
                engine.get_long_remaining_seconds(), long_work, delta=1.0
            )
        finally:
            engine.stop()

    def test_short_breaks_accumulate_toward_deep_break(self) -> None:
        """短休息清零暴露计时，但不清零长周期账本；累计满 90 分钟触发深度休息。"""
        work = defaults.SHORT_WORK_DURATION

        # 完成 3 次短休息：每轮 20 分钟计入长周期账本（共 60 分钟）
        for i in range(3):
            with self.subTest(iteration=i + 1):
                self.assertEqual(self.engine.get_current_break_type(), "short")
                self.timer.active_seconds = work
                self.engine.tick()
                self.state.on_break_triggered.assert_called_with("short")
                self.engine.on_break_complete()
                self.assertEqual(self.engine.get_short_break_count(), i + 1)
                self.state.reset_mock()

        # 3 轮短休息后账本 = 3600s，距深度休息还剩 1800s
        self.assertAlmostEqual(self.engine.get_long_remaining_seconds(), 1800.0, delta=1.0)

        # 第 4 轮短休息（20 分钟先于深度休息到期）
        self.timer.active_seconds = work
        self.engine.tick()
        self.state.on_break_triggered.assert_called_with("short")
        self.engine.on_break_complete()
        self.state.reset_mock()

        # 4 轮短休息后账本 = 4800s：再暴露 600s 深度休息先于第 5 轮短休息到期
        self.timer.active_seconds = 599
        self.engine.tick()
        self.state.on_break_triggered.assert_not_called()

        self.timer.active_seconds = 600
        self.engine.tick()
        self.state.on_break_triggered.assert_called_once_with("long")
        self.engine.on_break_complete()

        # 深度休息完成后短休息计数归零、账本清零
        self.assertEqual(self.engine.get_short_break_count(), 0)
        self.assertEqual(self.engine.get_break_count_today(), 5)
        self.assertAlmostEqual(
            self.engine.get_long_remaining_seconds(),
            defaults.LONG_WORK_DURATION,
            delta=1.0,
        )
        # 下一轮又是短休息（20 分钟）
        self.assertEqual(self.engine.get_current_break_type(), "short")

    def test_long_break_duration_in_event(self) -> None:
        """长休息触发事件中包含 300 秒时长。"""
        # 先完成 3 次短休息
        work = defaults.SHORT_WORK_DURATION
        for _ in range(3):
            self.timer.active_seconds = work
            self.engine.tick()
            self.engine.on_break_complete()
        self.events.clear()

        self.timer.active_seconds = defaults.LONG_WORK_DURATION
        self.engine.tick()

        break_events = [d for et, d in self.events if et == EventType.BREAK_TRIGGERED]
        self.assertTrue(break_events)
        self.assertEqual(break_events[-1]["break_type"], "long")
        self.assertEqual(break_events[-1]["duration"], defaults.LONG_BREAK_DURATION)

    # ------------------------------------------------------------------
    # 休息中断 / 暂停（V1 简化：休息中 tick 不推进）
    # ------------------------------------------------------------------
    def test_break_in_progress_blocks_further_triggers(self) -> None:
        """休息进行中，tick 不应触发新的休息或警告。"""
        work = defaults.SHORT_WORK_DURATION
        self.timer.active_seconds = work
        self.engine.tick()
        self.assertTrue(self.engine.is_break_in_progress())
        self.state.reset_mock()

        # 即使 active 继续累加，休息中 tick 无动作
        self.timer.active_seconds = work + 1000
        for _ in range(5):
            self.engine.tick()
        self.state.on_break_warning.assert_not_called()
        self.state.on_break_triggered.assert_not_called()

        # 完成后恢复正常检测
        self.engine.on_break_complete()
        self.timer.active_seconds = work - defaults.WARNING_DURATION
        self.engine.tick()
        self.state.on_break_warning.assert_called_once()

    # ------------------------------------------------------------------
    # 休息完成后 active_time 重置
    # ------------------------------------------------------------------
    def test_break_completed_resets_active_time(self) -> None:
        """休息完成后 active_time 重置为 0，timer.reset() 被调用。"""
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        self.engine.on_break_complete()
        self.assertEqual(self.timer.reset_calls, 1)
        self.assertEqual(self.timer.active_seconds, 0.0)

    def test_break_skip_resets_active_time(self) -> None:
        """跳过休息后 active_time 重置为 0。"""
        work = defaults.SHORT_WORK_DURATION
        self.timer.active_seconds = work - defaults.WARNING_DURATION
        self.engine.tick()  # 先警告
        self.timer.active_seconds = work
        self.engine.tick()  # 触发休息
        self.engine.on_break_skip()
        self.assertEqual(self.timer.reset_calls, 1)
        self.assertEqual(self.timer.active_seconds, 0.0)
        self.assertTrue(
            any(et == EventType.BREAK_SKIPPED for et, _ in self.events)
        )
        # 跳过也计入今日休息数
        self.assertEqual(self.engine.get_break_count_today(), 1)

    # ------------------------------------------------------------------
    # 延迟机制
    # ------------------------------------------------------------------
    def test_postpone_max_two(self) -> None:
        """最多延迟 2 次，第 3 次失败。"""
        self.assertTrue(self.engine.on_postpone())
        self.assertEqual(self.engine.get_postpone_count(), 1)
        self.assertTrue(self.engine.on_postpone())
        self.assertEqual(self.engine.get_postpone_count(), 2)
        # 第 3 次失败
        self.assertFalse(self.engine.on_postpone())
        self.assertEqual(self.engine.get_postpone_count(), 2)

    def test_postpone_extends_work_duration(self) -> None:
        """延迟后 work_duration 增加 POSTPONE_DURATION，需要更长时间才触发休息。"""
        work = defaults.SHORT_WORK_DURATION
        postpone = defaults.POSTPONE_DURATION  # 300

        self.assertTrue(self.engine.on_postpone())

        # 未延迟时 1200 就该休息，延迟后需要 1500
        self.timer.active_seconds = work  # 1200
        self.engine.tick()
        self.state.on_break_triggered.assert_not_called()
        self.assertFalse(self.engine.should_break())

        # 达到延迟后的阈值才休息
        self.timer.active_seconds = work + postpone  # 1500
        self.assertTrue(self.engine.should_break())
        self.engine.tick()
        self.state.on_break_triggered.assert_called_once()

    def test_postpone_resets_warning_state(self) -> None:
        """延迟后警告状态重置，重新累积到新阈值才再次警告。"""
        work = defaults.SHORT_WORK_DURATION
        warn = defaults.WARNING_DURATION

        # 触发警告
        self.timer.active_seconds = work - warn
        self.engine.tick()
        self.assertTrue(self.engine.is_warning_triggered())

        # 延迟
        self.assertTrue(self.engine.on_postpone())
        self.assertFalse(self.engine.is_warning_triggered())

        # 原阈值不再警告
        self.timer.active_seconds = work
        self.engine.tick()
        self.assertFalse(self.engine.is_warning_triggered())

        # 新阈值（work + postpone - warn）才警告
        self.timer.active_seconds = work + defaults.POSTPONE_DURATION - warn
        self.engine.tick()
        self.assertTrue(self.engine.is_warning_triggered())

    def test_postpone_reset_after_break(self) -> None:
        """休息完成后延迟计数重置。"""
        self.assertTrue(self.engine.on_postpone())
        self.assertTrue(self.engine.on_postpone())
        self.assertEqual(self.engine.get_postpone_count(), 2)

        self.timer.active_seconds = defaults.SHORT_WORK_DURATION + defaults.POSTPONE_DURATION * 2
        self.engine.tick()
        self.engine.on_break_complete()
        self.assertEqual(self.engine.get_postpone_count(), 0)
        self.assertTrue(self.engine.on_postpone())  # 可以再次延迟

    # ------------------------------------------------------------------
    # 自然休息
    # ------------------------------------------------------------------
    def test_natural_rest_does_not_increment_count(self) -> None:
        """自然休息不增加休息计数，但重置 active_time。"""
        work = defaults.SHORT_WORK_DURATION
        self.timer.active_seconds = work - defaults.WARNING_DURATION
        self.engine.tick()  # 触发警告
        self.assertEqual(self.engine.get_break_count_today(), 0)
        self.assertTrue(self.engine.is_warning_triggered())

        # 发布自然休息事件
        self.bus.publish(
            EventType.ACTIVITY_CHANGED,
            {
                "old_state": "active",
                "new_state": "natural_rest",
                "idle_seconds": 120,
                "is_natural_rest": True,
            },
        )

        # 状态机被调用
        self.state.on_natural_rest.assert_called_once()
        # active_time 被重置
        self.assertEqual(self.timer.reset_calls, 1)
        self.assertEqual(self.timer.active_seconds, 0.0)
        # 休息计数未增加
        self.assertEqual(self.engine.get_break_count_today(), 0)
        self.assertEqual(self.engine.get_short_break_count(), 0)
        # 警告状态被重置
        self.assertFalse(self.engine.is_warning_triggered())

    def test_natural_rest_ignored_when_not_flagged(self) -> None:
        """is_natural_rest=False 的事件不触发重置。"""
        self.timer.active_seconds = 500
        self.bus.publish(
            EventType.ACTIVITY_CHANGED,
            {"is_natural_rest": False, "new_state": "idle"},
        )
        self.state.on_natural_rest.assert_not_called()
        self.assertEqual(self.timer.reset_calls, 0)

    def test_natural_rest_ignored_during_break(self) -> None:
        """休息进行中不响应自然休息事件。"""
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        self.assertTrue(self.engine.is_break_in_progress())
        self.state.reset_mock()
        reset_before = self.timer.reset_calls

        self.bus.publish(
            EventType.ACTIVITY_CHANGED,
            {"is_natural_rest": True, "new_state": "natural_rest"},
        )
        self.state.on_natural_rest.assert_not_called()
        self.assertEqual(self.timer.reset_calls, reset_before)

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------
    def test_events_published_in_order(self) -> None:
        """警告 → 休息 → 完成 事件按正确顺序发布。"""
        work = defaults.SHORT_WORK_DURATION
        warn = defaults.WARNING_DURATION

        self.timer.active_seconds = work - warn
        self.engine.tick()
        self.timer.active_seconds = work
        self.engine.tick()
        self.engine.on_break_complete()

        types = [et for et, _ in self.events]
        self.assertIn(EventType.BREAK_WARNING, types)
        self.assertIn(EventType.BREAK_TRIGGERED, types)
        self.assertIn(EventType.BREAK_COMPLETED, types)
        # 顺序：warning 在 triggered 前，triggered 在 completed 前
        self.assertLess(types.index(EventType.BREAK_WARNING), types.index(EventType.BREAK_TRIGGERED))
        self.assertLess(types.index(EventType.BREAK_TRIGGERED), types.index(EventType.BREAK_COMPLETED))

    def test_warning_event_payload(self) -> None:
        """警告事件 payload 包含 break_type 与 remaining_seconds。"""
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        self.engine.tick()
        warn_events = [d for et, d in self.events if et == EventType.BREAK_WARNING]
        self.assertEqual(len(warn_events), 1)
        self.assertEqual(warn_events[0]["break_type"], "short")
        self.assertEqual(warn_events[0]["remaining_seconds"], defaults.WARNING_DURATION)

    def test_triggered_event_payload(self) -> None:
        """休息触发事件 payload 包含 break_type 与 duration。"""
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        trigger_events = [d for et, d in self.events if et == EventType.BREAK_TRIGGERED]
        self.assertEqual(len(trigger_events), 1)
        self.assertEqual(trigger_events[0]["break_type"], "short")
        self.assertEqual(trigger_events[0]["duration"], defaults.SHORT_BREAK_DURATION)

    def test_completed_event_payload(self) -> None:
        """休息完成事件 payload 包含 break_type 与 short_break_count。"""
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        self.engine.on_break_complete()
        complete_events = [d for et, d in self.events if et == EventType.BREAK_COMPLETED]
        self.assertEqual(len(complete_events), 1)
        self.assertEqual(complete_events[0]["break_type"], "short")
        self.assertEqual(complete_events[0]["short_break_count"], 1)

    # ------------------------------------------------------------------
    # 无 event_bus 的容错
    # ------------------------------------------------------------------
    def test_no_event_bus_still_works(self) -> None:
        """event_bus=None 时引擎仍可正常工作（不发布事件）。"""
        engine = BreakEngine(timer_engine=self.timer, state_machine=self.state, event_bus=None)
        engine.start()
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        engine.tick()
        self.state.on_break_triggered.assert_called_once_with("short")
        engine.on_break_complete()
        self.assertEqual(self.timer.reset_calls, 1)
        engine.stop()

    # ------------------------------------------------------------------
    # config 覆盖
    # ------------------------------------------------------------------
    def test_config_override(self) -> None:
        """config 字典可覆盖默认配置。"""
        engine = BreakEngine(
            timer_engine=self.timer,
            state_machine=self.state,
            event_bus=None,
            config={
                "SHORT_WORK_DURATION": 60,
                "WARNING_DURATION": 10,
                "SHORT_BREAK_DURATION": 5,
            },
        )
        engine.start()
        # 50 秒应触发警告（60-10）
        self.timer.active_seconds = 50
        engine.tick()
        self.state.on_break_warning.assert_called_once()
        # 60 秒应触发休息
        self.timer.active_seconds = 60
        engine.tick()
        self.state.on_break_triggered.assert_called_once_with("short")
        engine.stop()


class TestFullscreenDeferral(unittest.TestCase):
    """全屏延迟提醒/休息（V1.1 全屏检测）测试。"""

    def _build_engine(self, fullscreen_provider):
        timer = FakeTimerEngine()
        state = MagicMock()
        bus = EventBus()
        engine = BreakEngine(
            timer_engine=timer,
            state_machine=state,
            event_bus=bus,
            fullscreen_provider=fullscreen_provider,
        )
        engine.start()
        return engine, timer, state

    def test_fullscreen_defers_warning(self) -> None:
        engine, timer, state = self._build_engine(lambda: True)
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        state.on_break_warning.assert_not_called()
        self.assertFalse(engine.is_warning_triggered())
        engine.stop()

    def test_fullscreen_defers_break(self) -> None:
        engine, timer, state = self._build_engine(lambda: True)
        timer.active_seconds = defaults.SHORT_WORK_DURATION
        engine.tick()
        state.on_break_triggered.assert_not_called()
        self.assertFalse(engine.is_break_in_progress())
        engine.stop()

    def test_not_fullscreen_triggers_warning(self) -> None:
        engine, timer, state = self._build_engine(lambda: False)
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        state.on_break_warning.assert_called_once()
        engine.stop()

    def test_no_provider_triggers_normally(self) -> None:
        engine, timer, state = self._build_engine(None)
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        state.on_break_warning.assert_called_once()
        engine.stop()

    def test_provider_exception_treated_as_not_fullscreen(self) -> None:
        def boom() -> bool:
            raise RuntimeError("fullscreen check failed")

        engine, timer, state = self._build_engine(boom)
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()  # 不应崩溃，且正常触发警告
        state.on_break_warning.assert_called_once()
        engine.stop()

    def test_fullscreen_then_exit_resumes_break(self) -> None:
        # 全屏期间延迟，退出全屏后正常触发
        flag = {"fullscreen": True}
        engine, timer, state = self._build_engine(lambda: flag["fullscreen"])
        timer.active_seconds = defaults.SHORT_WORK_DURATION
        engine.tick()
        state.on_break_triggered.assert_not_called()

        flag["fullscreen"] = False
        engine.tick()
        state.on_break_triggered.assert_called_once_with("short")
        engine.stop()


class TestBreakEngineSettings(unittest.TestCase):
    """休息引擎运行时配置热更新与全屏开关测试。"""

    def _build_engine(self, fullscreen_provider=None, defer_on_fullscreen=True):
        timer = FakeTimerEngine()
        state = MagicMock()
        bus = EventBus()
        engine = BreakEngine(
            timer_engine=timer,
            state_machine=state,
            event_bus=bus,
            fullscreen_provider=fullscreen_provider,
            defer_on_fullscreen=defer_on_fullscreen,
        )
        engine.start()
        return engine, timer, state

    def test_defer_off_ignores_fullscreen(self) -> None:
        """defer_on_fullscreen=False 时，即使全屏也正常触发警告。"""
        engine, timer, state = self._build_engine(
            fullscreen_provider=lambda: True, defer_on_fullscreen=False
        )
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        state.on_break_warning.assert_called_once()
        engine.stop()

    def test_defer_on_respects_fullscreen(self) -> None:
        """defer_on_fullscreen=True（默认）时，全屏延迟警告。"""
        engine, timer, state = self._build_engine(
            fullscreen_provider=lambda: True, defer_on_fullscreen=True
        )
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        state.on_break_warning.assert_not_called()
        engine.stop()

    def test_update_settings_hot_updates_duration(self) -> None:
        """update_settings 应热更新工作阈值，且不重置已触发状态。"""
        engine, timer, state = self._build_engine()
        # 先触发警告
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()
        self.assertTrue(engine.is_warning_triggered())

        # 热更新：缩短短工作周期到 10 秒
        engine.update_settings({"short_work_duration": 10})
        # 已触发的警告标志不应被重置
        self.assertTrue(engine.is_warning_triggered())
        # 新的工作阈值应生效：只需 10 秒即触发休息
        timer.active_seconds = 10
        engine.tick()
        state.on_break_triggered.assert_called_once_with("short")
        engine.stop()

    def test_update_settings_toggles_defer_flag(self) -> None:
        """update_settings 可运行时切换 defer_on_fullscreen。"""
        engine, timer, state = self._build_engine(
            fullscreen_provider=lambda: True, defer_on_fullscreen=True
        )
        timer.active_seconds = defaults.SHORT_WORK_DURATION - defaults.WARNING_DURATION
        engine.tick()  # 全屏中，被延迟
        state.on_break_warning.assert_not_called()

        engine.update_settings({"defer_on_fullscreen": False})
        engine.tick()  # 关闭延迟后，全屏也触发
        state.on_break_warning.assert_called_once()
        engine.stop()


class TestActivityMonitorSettings(unittest.TestCase):
    """活动监控器阈值热更新测试。"""

    def setUp(self) -> None:
        self.monitor = ActivityMonitor()

    def tearDown(self) -> None:
        self.monitor.stop_polling()

    def test_update_thresholds(self) -> None:
        self.monitor.update_thresholds(90, 180)
        self.assertEqual(self.monitor.idle_threshold, 90)
        self.assertEqual(self.monitor.natural_rest_threshold, 180)

    def test_update_thresholds_clamps_invalid_order(self) -> None:
        """natural < idle 时自动抬升 natural 至 idle，维持不变量。"""
        self.monitor.update_thresholds(120, 60)
        self.assertEqual(self.monitor.idle_threshold, 120)
        self.assertEqual(self.monitor.natural_rest_threshold, 120)

    def test_update_thresholds_rejects_negative(self) -> None:
        """负数阈值被忽略，保持原值。"""
        self.monitor.update_thresholds(-5, -10)
        self.assertEqual(self.monitor.idle_threshold, defaults.IDLE_THRESHOLD)
        self.assertEqual(
            self.monitor.natural_rest_threshold, defaults.NATURAL_REST_THRESHOLD
        )


if __name__ == "__main__":
    unittest.main()
