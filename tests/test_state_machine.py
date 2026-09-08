"""状态机单元测试。

覆盖所有状态转换路径、非法转换拒绝、事件发布、TimerEngine 协同、历史记录与线程安全。
由于 StateMachine 依赖 TimerEngine 和 ActivityMonitor，测试通过 mock 对象隔离外部依赖。
"""

from __future__ import annotations

import threading
import unittest
from typing import Optional

from app.config import defaults
from app.core.activity_monitor import ActivityState
from app.core.event_bus import EventBus, EventType
from app.core.state_machine import AppState, StateMachine


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


class StateChangeCollector:
    """收集 STATE_CHANGED 事件的辅助器。"""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.events: list[dict] = []
        self._lock = threading.Lock()
        bus.subscribe(EventType.STATE_CHANGED, self._handler)

    def _handler(self, data: dict) -> None:
        with self._lock:
            self.events.append(data)

    def get_events(self) -> list[dict]:
        with self._lock:
            return list(self.events)

    def clear(self) -> None:
        with self._lock:
            self.events.clear()

    def unsubscribe(self) -> None:
        self._bus.unsubscribe(EventType.STATE_CHANGED, self._handler)


def make_sm(
    timer_engine: Optional[MockTimerEngine] = None,
    activity_monitor: Optional[ControllableActivityMonitor] = None,
) -> tuple[StateMachine, MockTimerEngine, EventBus, StateChangeCollector]:
    """构造状态机及其依赖，返回 (sm, timer, bus, collector)。"""
    bus = EventBus()
    timer = timer_engine if timer_engine is not None else MockTimerEngine()
    sm = StateMachine(
        timer_engine=timer,
        event_bus=bus,
        activity_monitor=activity_monitor,
    )
    collector = StateChangeCollector(bus)
    return sm, timer, bus, collector


# ---------------------------------------------------------------------------
# 初始化与基础接口
# ---------------------------------------------------------------------------
class TestStateMachineInit(unittest.TestCase):
    """初始化与基础接口测试。"""

    def test_initial_state_is_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_app_state_has_9_members(self) -> None:
        self.assertEqual(len(AppState), 9)

    def test_app_state_names(self) -> None:
        names = {s.name for s in AppState}
        expected = {
            "INACTIVE", "ACTIVE", "IDLE", "BREAK_WARNING",
            "SHORT_BREAK", "LONG_BREAK", "PAUSED", "LOCKED", "SLEEP",
        }
        self.assertEqual(names, expected)

    def test_can_transition_same_state_always_true(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE → INACTIVE 幂等
        self.assertTrue(sm.can_transition(AppState.INACTIVE))

    def test_transition_to_returns_true_on_success(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertTrue(sm.transition_to(AppState.ACTIVE, reason="test"))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)

    def test_transition_to_returns_false_on_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE → BREAK_WARNING 非法
        self.assertFalse(sm.transition_to(AppState.BREAK_WARNING, reason="test"))
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_transition_to_same_state_noop_without_reason(self) -> None:
        sm, _, _, collector = make_sm()
        sm.transition_to(AppState.ACTIVE, reason="start")
        collector.clear()
        # 同状态无 reason 不发布事件
        self.assertTrue(sm.transition_to(AppState.ACTIVE))
        self.assertEqual(len(collector.get_events()), 0)

    def test_transition_to_same_state_with_reason_publishes(self) -> None:
        sm, _, _, collector = make_sm()
        sm.transition_to(AppState.ACTIVE, reason="start")
        collector.clear()
        # 同状态有 reason 仍发布（如 natural_rest 自转换）
        self.assertTrue(sm.transition_to(AppState.ACTIVE, reason="natural_rest"))
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "natural_rest")


# ---------------------------------------------------------------------------
# 正常休息流程
# ---------------------------------------------------------------------------
class TestNormalBreakFlow(unittest.TestCase):
    """正常流程：INACTIVE → ACTIVE → BREAK_WARNING → SHORT_BREAK → ACTIVE。"""

    def test_full_short_break_flow(self) -> None:
        sm, timer, _, _ = make_sm()

        # INACTIVE → ACTIVE
        self.assertTrue(sm.start_protection())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.reset_calls, 1)

        # 累计一些 active 时间
        for _ in range(10):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 10.0)

        # ACTIVE → BREAK_WARNING
        self.assertTrue(sm.on_break_warning())
        self.assertEqual(sm.get_state(), AppState.BREAK_WARNING)

        # BREAK_WARNING → SHORT_BREAK
        self.assertTrue(sm.on_break_triggered("short"))
        self.assertEqual(sm.get_state(), AppState.SHORT_BREAK)
        self.assertEqual(sm.break_type, "short")

        # SHORT_BREAK → ACTIVE（完成，重置）
        self.assertTrue(sm.on_break_completed())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        # 休息完成后 active_seconds 被重置
        self.assertEqual(timer.get_active_seconds(), 0.0)
        self.assertEqual(timer.reset_calls, 2)

    def test_full_long_break_flow(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()

        self.assertTrue(sm.on_break_warning())
        self.assertTrue(sm.on_break_triggered("long"))
        self.assertEqual(sm.get_state(), AppState.LONG_BREAK)
        self.assertEqual(sm.break_type, "long")

        self.assertTrue(sm.on_break_completed())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_break_warning_only_from_active(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE 不响应 break_warning
        self.assertFalse(sm.on_break_warning())
        sm.start_protection()
        sm.on_system_lock()
        # LOCKED 不响应
        self.assertFalse(sm.on_break_warning())

    def test_break_triggered_only_from_warning(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE 不响应
        self.assertFalse(sm.on_break_triggered())
        sm.start_protection()
        # ACTIVE 不直接响应
        self.assertFalse(sm.on_break_triggered())

    def test_break_completed_only_from_break(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE 不响应
        self.assertFalse(sm.on_break_completed())
        sm.start_protection()
        # ACTIVE 不响应
        self.assertFalse(sm.on_break_completed())

    def test_default_break_type_is_short(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        # 默认 short
        self.assertTrue(sm.on_break_triggered())
        self.assertEqual(sm.get_state(), AppState.SHORT_BREAK)
        self.assertEqual(sm.break_type, "short")


# ---------------------------------------------------------------------------
# 活动状态变化
# ---------------------------------------------------------------------------
class TestActivityChanged(unittest.TestCase):
    """活动状态变化处理测试。"""

    def test_active_to_idle(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        self.assertTrue(sm.on_activity_changed(ActivityState.IDLE))
        self.assertEqual(sm.get_state(), AppState.IDLE)

    def test_idle_to_active(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_activity_changed(ActivityState.IDLE)
        self.assertTrue(sm.on_activity_changed(ActivityState.ACTIVE))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)

    def test_activity_ignored_when_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.on_activity_changed(ActivityState.IDLE))
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_activity_idle_no_op_when_not_active(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        # LOCKED 收到 IDLE 活动事件不应转换
        self.assertTrue(sm.on_activity_changed(ActivityState.IDLE))
        self.assertEqual(sm.get_state(), AppState.LOCKED)

    def test_activity_active_no_op_when_not_idle(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        # ACTIVE 收到 ACTIVE 事件，已在目标状态
        self.assertTrue(sm.on_activity_changed(ActivityState.ACTIVE))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)


# ---------------------------------------------------------------------------
# 自然休息
# ---------------------------------------------------------------------------
class TestNaturalRest(unittest.TestCase):
    """自然休息测试（idle ≥ 120秒触发重置）。"""

    def test_natural_rest_from_active_resets_timer(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(20):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 20.0)

        # 自然休息
        self.assertTrue(sm.on_natural_rest())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)
        self.assertEqual(timer.reset_calls, 2)  # start + natural_rest

    def test_natural_rest_from_idle_resets_timer(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(15):
            timer.tick()
        sm.on_activity_changed(ActivityState.IDLE)

        self.assertTrue(sm.on_natural_rest())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_natural_rest_via_activity_changed(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()

        # 通过 on_activity_changed 触发
        self.assertTrue(sm.on_activity_changed(ActivityState.NATURAL_REST))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_natural_rest_rejected_when_inactive(self) -> None:
        sm, timer, _, _ = make_sm()
        self.assertFalse(sm.on_natural_rest())
        self.assertEqual(timer.reset_calls, 0)

    def test_natural_rest_rejected_when_paused(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.pause()
        reset_before = timer.reset_calls
        self.assertFalse(sm.on_natural_rest())
        self.assertEqual(timer.reset_calls, reset_before)

    def test_natural_rest_rejected_when_locked(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        self.assertFalse(sm.on_natural_rest())

    def test_natural_rest_rejected_when_sleep(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertFalse(sm.on_natural_rest())

    def test_natural_rest_from_warning_goes_active(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        self.assertTrue(sm.on_natural_rest())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)


# ---------------------------------------------------------------------------
# 暂停 / 恢复
# ---------------------------------------------------------------------------
class TestPauseResume(unittest.TestCase):
    """暂停 / 恢复测试。"""

    def test_pause_resume_flow(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()

        self.assertTrue(sm.pause())
        self.assertEqual(sm.get_state(), AppState.PAUSED)
        self.assertTrue(timer.is_paused())

        self.assertTrue(sm.resume())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertFalse(timer.is_paused())

    def test_pause_from_idle(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_activity_changed(ActivityState.IDLE)
        self.assertTrue(sm.pause())
        self.assertEqual(sm.get_state(), AppState.PAUSED)

    def test_pause_from_warning(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        self.assertTrue(sm.pause())
        self.assertEqual(sm.get_state(), AppState.PAUSED)

    def test_pause_rejected_during_break(self) -> None:
        """休息中不可暂停（PRD 规则）。"""
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered()
        # SHORT_BREAK 不能暂停
        self.assertFalse(sm.pause())
        self.assertEqual(sm.get_state(), AppState.SHORT_BREAK)
        self.assertFalse(timer.is_paused())

        # LONG_BREAK 也不能暂停
        sm.on_break_completed()
        sm.on_break_warning()
        sm.on_break_triggered("long")
        self.assertFalse(sm.pause())
        self.assertEqual(sm.get_state(), AppState.LONG_BREAK)

    def test_pause_rejected_when_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.pause())
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_pause_rejected_when_paused(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.pause()
        self.assertFalse(sm.pause())
        self.assertEqual(sm.get_state(), AppState.PAUSED)

    def test_resume_rejected_when_not_paused(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.resume())
        sm.start_protection()
        self.assertFalse(sm.resume())

    def test_pause_with_duration_minutes(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()
        sm.pause(duration_minutes=15)
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["duration_minutes"], 15)

    def test_pause_timer_not_resumed_without_resume(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.pause()
        # 暂停期间 tick 不累加
        for _ in range(5):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 0.0)


# ---------------------------------------------------------------------------
# 锁屏 / 解锁
# ---------------------------------------------------------------------------
class TestLockUnlock(unittest.TestCase):
    """系统锁屏 / 解锁测试。"""

    def test_lock_unlock_flow(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()

        self.assertTrue(sm.on_system_lock())
        self.assertEqual(sm.get_state(), AppState.LOCKED)
        self.assertTrue(timer.is_paused())

        self.assertTrue(sm.on_system_unlock(idle_seconds=30.0))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertFalse(timer.is_paused())

    def test_lock_from_idle(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_activity_changed(ActivityState.IDLE)
        self.assertTrue(sm.on_system_lock())
        self.assertEqual(sm.get_state(), AppState.LOCKED)

    def test_lock_rejected_when_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.on_system_lock())

    def test_lock_rejected_when_already_locked(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        self.assertFalse(sm.on_system_lock())

    def test_lock_from_sleep_allowed(self) -> None:
        """SLEEP → LOCKED 允许（唤醒后可能先检测到锁屏）。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertTrue(sm.on_system_lock())
        self.assertEqual(sm.get_state(), AppState.LOCKED)

    def test_unlock_rejected_when_not_locked(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.on_system_unlock())
        sm.start_protection()
        self.assertFalse(sm.on_system_unlock())

    def test_unlock_short_idle_no_reset(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        self.assertEqual(timer.get_active_seconds(), 10.0)

        sm.on_system_lock()
        # 解锁时空闲 < 120s，不重置
        self.assertTrue(sm.on_system_unlock(idle_seconds=30.0))
        self.assertEqual(timer.get_active_seconds(), 10.0)

    def test_unlock_long_idle_resets(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()

        sm.on_system_lock()
        # 解锁时空闲 >= 120s，重置
        self.assertTrue(
            sm.on_system_unlock(idle_seconds=defaults.NATURAL_REST_THRESHOLD)
        )
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_unlock_uses_activity_monitor_when_no_idle(self) -> None:
        # 320s 超过新的自然休息阈值 300s（旧值 120s 已不足以区分「看 PDF」与「真离开」）
        monitor = ControllableActivityMonitor(320.0)
        sm, timer, _, _ = make_sm(activity_monitor=monitor)
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_system_lock()

        # 不传 idle_seconds，从 monitor 获取
        self.assertTrue(sm.on_system_unlock())
        # idle=320 >= 300，应重置
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_unlock_no_reset_when_no_idle_info(self) -> None:
        """无 activity_monitor 且不显式传 idle_seconds 时不重置（保守策略）。"""
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_system_lock()

        self.assertTrue(sm.on_system_unlock())
        self.assertEqual(timer.get_active_seconds(), 10.0)


# ---------------------------------------------------------------------------
# 睡眠 / 唤醒
# ---------------------------------------------------------------------------
class TestSleepWake(unittest.TestCase):
    """系统睡眠 / 唤醒测试。"""

    def test_sleep_wake_flow(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()

        self.assertTrue(sm.on_system_sleep())
        self.assertEqual(sm.get_state(), AppState.SLEEP)
        self.assertTrue(timer.is_paused())

        self.assertTrue(sm.on_system_wake(idle_seconds=30.0))
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertFalse(timer.is_paused())

    def test_sleep_rejected_when_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.on_system_sleep())

    def test_sleep_from_locked_allowed(self) -> None:
        """LOCKED → SLEEP 允许（锁屏后系统进入睡眠）。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        self.assertTrue(sm.on_system_sleep())
        self.assertEqual(sm.get_state(), AppState.SLEEP)

    def test_sleep_rejected_when_already_sleep(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertFalse(sm.on_system_sleep())

    def test_wake_rejected_when_not_sleep(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.on_system_wake())
        sm.start_protection()
        self.assertFalse(sm.on_system_wake())

    def test_wake_short_idle_no_reset(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_system_sleep()

        self.assertTrue(sm.on_system_wake(idle_seconds=60.0))
        self.assertEqual(timer.get_active_seconds(), 10.0)

    def test_wake_long_idle_resets(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_system_sleep()

        self.assertTrue(
            sm.on_system_wake(idle_seconds=defaults.NATURAL_REST_THRESHOLD + 10)
        )
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_lock_to_sleep_transition(self) -> None:
        """LOCKED → SLEEP 允许。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        self.assertTrue(sm.on_system_sleep())
        self.assertEqual(sm.get_state(), AppState.SLEEP)

    def test_sleep_to_lock_transition(self) -> None:
        """SLEEP → LOCKED 允许。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertTrue(sm.on_system_lock())
        self.assertEqual(sm.get_state(), AppState.LOCKED)


# ---------------------------------------------------------------------------
# 跳过休息
# ---------------------------------------------------------------------------
class TestBreakSkip(unittest.TestCase):
    """跳过休息测试。"""

    def test_skip_from_warning(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_break_warning()

        self.assertTrue(sm.on_break_skipped())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_skip_from_short_break(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("short")

        self.assertTrue(sm.on_break_skipped())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.reset_calls, 2)

    def test_skip_from_long_break(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("long")

        self.assertTrue(sm.on_break_skipped())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)

    def test_skip_rejected_when_not_in_break(self) -> None:
        sm, _, _, _ = make_sm()
        # INACTIVE
        self.assertFalse(sm.on_break_skipped())
        sm.start_protection()
        # ACTIVE
        self.assertFalse(sm.on_break_skipped())
        sm.pause()
        # PAUSED
        self.assertFalse(sm.on_break_skipped())


# ---------------------------------------------------------------------------
# 保护生命周期
# ---------------------------------------------------------------------------
class TestProtectionLifecycle(unittest.TestCase):
    """开始/停止保护测试。"""

    def test_start_protection(self) -> None:
        sm, timer, _, _ = make_sm()
        self.assertTrue(sm.start_protection())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.reset_calls, 1)

    def test_stop_protection_from_active(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        self.assertTrue(sm.stop_protection())
        self.assertEqual(sm.get_state(), AppState.INACTIVE)
        self.assertTrue(timer.is_paused())

    def test_stop_protection_from_any_state(self) -> None:
        """任意状态 → INACTIVE。"""
        states_to_test = [
            AppState.ACTIVE,
            AppState.IDLE,
            AppState.BREAK_WARNING,
            AppState.SHORT_BREAK,
            AppState.LONG_BREAK,
            AppState.PAUSED,
            AppState.LOCKED,
            AppState.SLEEP,
        ]
        for target in states_to_test:
            sm, _, _, _ = make_sm()
            sm.start_protection()
            # 用 transition_to 直接设置到目标状态（通过合法路径）
            if target == AppState.IDLE:
                sm.on_activity_changed(ActivityState.IDLE)
            elif target == AppState.BREAK_WARNING:
                sm.on_break_warning()
            elif target == AppState.SHORT_BREAK:
                sm.on_break_warning()
                sm.on_break_triggered("short")
            elif target == AppState.LONG_BREAK:
                sm.on_break_warning()
                sm.on_break_triggered("long")
            elif target == AppState.PAUSED:
                sm.pause()
            elif target == AppState.LOCKED:
                sm.on_system_lock()
            elif target == AppState.SLEEP:
                sm.on_system_sleep()
            self.assertEqual(sm.get_state(), target)
            # 停止保护
            self.assertTrue(sm.stop_protection(), f"stop from {target} failed")
            self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_stop_protection_from_inactive(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertTrue(sm.stop_protection())
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_restart_after_stop(self) -> None:
        sm, timer, _, _ = make_sm()
        sm.start_protection()
        sm.stop_protection()
        self.assertTrue(sm.start_protection())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        self.assertEqual(timer.reset_calls, 2)


# ---------------------------------------------------------------------------
# 非法转换全面覆盖
# ---------------------------------------------------------------------------
class TestIllegalTransitions(unittest.TestCase):
    """非法转换被拒绝测试。"""

    def test_inactive_to_break_warning_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.can_transition(AppState.BREAK_WARNING))
        self.assertFalse(sm.transition_to(AppState.BREAK_WARNING))

    def test_inactive_to_short_break_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.can_transition(AppState.SHORT_BREAK))

    def test_inactive_to_paused_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertFalse(sm.can_transition(AppState.PAUSED))

    def test_idle_to_break_warning_illegal(self) -> None:
        """IDLE 不累加 active，不触发 break warning。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_activity_changed(ActivityState.IDLE)
        self.assertFalse(sm.can_transition(AppState.BREAK_WARNING))

    def test_active_to_short_break_illegal(self) -> None:
        """ACTIVE 不能直接到 SHORT_BREAK，必须经过 BREAK_WARNING。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        self.assertFalse(sm.can_transition(AppState.SHORT_BREAK))

    def test_active_to_long_break_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        self.assertFalse(sm.can_transition(AppState.LONG_BREAK))

    def test_short_break_to_paused_illegal(self) -> None:
        """休息中不暂停。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered()
        self.assertFalse(sm.can_transition(AppState.PAUSED))

    def test_long_break_to_paused_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("long")
        self.assertFalse(sm.can_transition(AppState.PAUSED))

    def test_locked_to_paused_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        self.assertFalse(sm.can_transition(AppState.PAUSED))

    def test_sleep_to_paused_illegal(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_system_sleep()
        self.assertFalse(sm.can_transition(AppState.PAUSED))

    def test_all_legal_transitions_allowed(self) -> None:
        """验证转换表中所有合法转换确实被允许。"""
        from app.core.state_machine import _ALLOWED_TRANSITIONS

        for source, targets in _ALLOWED_TRANSITIONS.items():
            sm, _, _, _ = make_sm()
            # 用 transition_to 设置到 source（通过合法路径或直接设置）
            if source == AppState.INACTIVE:
                pass
            elif source == AppState.ACTIVE:
                sm.start_protection()
            elif source == AppState.IDLE:
                sm.start_protection()
                sm.on_activity_changed(ActivityState.IDLE)
            elif source == AppState.BREAK_WARNING:
                sm.start_protection()
                sm.on_break_warning()
            elif source == AppState.SHORT_BREAK:
                sm.start_protection()
                sm.on_break_warning()
                sm.on_break_triggered("short")
            elif source == AppState.LONG_BREAK:
                sm.start_protection()
                sm.on_break_warning()
                sm.on_break_triggered("long")
            elif source == AppState.PAUSED:
                sm.start_protection()
                sm.pause()
            elif source == AppState.LOCKED:
                sm.start_protection()
                sm.on_system_lock()
            elif source == AppState.SLEEP:
                sm.start_protection()
                sm.on_system_sleep()

            for target in targets:
                self.assertTrue(
                    sm.can_transition(target),
                    f"合法转换 {source.name} → {target.name} 被拒绝",
                )


# ---------------------------------------------------------------------------
# 事件发布
# ---------------------------------------------------------------------------
class TestStateChangedEvents(unittest.TestCase):
    """STATE_CHANGED 事件发布测试。"""

    def test_start_protection_publishes_event(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["old_state"], "INACTIVE")
        self.assertEqual(events[0]["new_state"], "ACTIVE")
        self.assertEqual(events[0]["reason"], "start_protection")

    def test_activity_transitions_publish_events(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()

        sm.on_activity_changed(ActivityState.IDLE)
        sm.on_activity_changed(ActivityState.ACTIVE)

        events = collector.get_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["reason"], "activity_idle")
        self.assertEqual(events[1]["reason"], "activity_resumed")

    def test_break_flow_events(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()

        sm.on_break_warning()
        sm.on_break_triggered("short")
        sm.on_break_completed()

        events = collector.get_events()
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["reason"], "break_warning")
        self.assertEqual(events[1]["reason"], "break_triggered")
        self.assertEqual(events[1]["break_type"], "short")
        self.assertEqual(events[2]["reason"], "break_completed")

    def test_pause_resume_events(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()

        sm.pause(duration_minutes=5)
        sm.resume()

        events = collector.get_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["reason"], "pause")
        self.assertEqual(events[0]["duration_minutes"], 5)
        self.assertEqual(events[1]["reason"], "resume")

    def test_lock_unlock_events_with_idle(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        sm.on_system_lock()
        collector.clear()

        sm.on_system_unlock(idle_seconds=150.0)
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "system_unlock")
        self.assertEqual(events[0]["idle_seconds"], 150.0)

    def test_illegal_transition_does_not_publish(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()

        # INACTIVE → BREAK_WARNING 非法，但当前是 ACTIVE，从 ACTIVE 也不能直接到 SHORT_BREAK
        sm.stop_protection()
        collector.clear()

        self.assertFalse(sm.transition_to(AppState.BREAK_WARNING, reason="illegal"))
        self.assertEqual(len(collector.get_events()), 0)

    def test_natural_rest_publishes_event(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()

        sm.on_natural_rest()
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "natural_rest")

    def test_event_bus_none_does_not_crash(self) -> None:
        """event_bus=None 时状态机仍可工作。"""
        sm = StateMachine(timer_engine=MockTimerEngine(), event_bus=None)
        sm.start_protection()
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        sm.on_break_warning()
        self.assertEqual(sm.get_state(), AppState.BREAK_WARNING)


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------
class TestHistory(unittest.TestCase):
    """转换历史记录测试。"""

    def test_history_records_transitions(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered()
        sm.on_break_completed()

        history = sm.get_history()
        self.assertEqual(len(history), 4)
        self.assertEqual(history[0]["old_state"], "INACTIVE")
        self.assertEqual(history[0]["new_state"], "ACTIVE")
        self.assertEqual(history[0]["reason"], "start_protection")
        self.assertIn("timestamp", history[0])

    def test_history_max_10(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        # 制造超过 10 条转换
        for _ in range(15):
            sm.on_break_warning()
            sm.on_break_triggered()
            sm.on_break_completed()
        history = sm.get_history()
        self.assertEqual(len(history), 10)

    def test_history_includes_extra_fields(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("long")

        history = sm.get_history()
        last = history[-1]
        self.assertEqual(last["break_type"], "long")

    def test_history_returns_copy(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        h1 = sm.get_history()
        h1.clear()
        h2 = sm.get_history()
        self.assertEqual(len(h2), 1)


# ---------------------------------------------------------------------------
# TimerEngine 协同
# ---------------------------------------------------------------------------
class TestTimerEngineIntegration(unittest.TestCase):
    """StateMachine 与 TimerEngine 协同测试。"""

    def test_reset_on_start_protection(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        timer.tick()
        timer.tick()
        self.assertEqual(timer.get_active_seconds(), 2.0)
        sm.start_protection()
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_reset_on_natural_rest(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_natural_rest()
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_reset_on_break_completed(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_break_warning()
        sm.on_break_triggered()
        sm.on_break_completed()
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_reset_on_break_skipped(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_break_warning()
        sm.on_break_skipped()
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_pause_on_lock(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        sm.on_system_lock()
        self.assertTrue(timer.is_paused())

    def test_resume_on_unlock(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        sm.on_system_lock()
        sm.on_system_unlock(idle_seconds=30.0)
        self.assertFalse(timer.is_paused())

    def test_pause_on_sleep(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        sm.on_system_sleep()
        self.assertTrue(timer.is_paused())

    def test_resume_on_wake(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        sm.on_system_sleep()
        sm.on_system_wake(idle_seconds=30.0)
        self.assertFalse(timer.is_paused())

    def test_reset_on_unlock_long_idle(self) -> None:
        timer = MockTimerEngine()
        sm, timer, _, _ = make_sm(timer_engine=timer)
        sm.start_protection()
        for _ in range(10):
            timer.tick()
        sm.on_system_lock()
        # 新阈值 300s：离开 200s 只算短暂 AWAY，不重置
        sm.on_system_unlock(idle_seconds=320.0)
        self.assertEqual(timer.get_active_seconds(), 0.0)

    def test_timer_engine_none_no_crash(self) -> None:
        """timer_engine=None 时状态机正常工作。"""
        sm = StateMachine(timer_engine=None, event_bus=EventBus())
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered()
        sm.on_break_completed()
        sm.on_system_lock()
        sm.on_system_unlock(idle_seconds=30.0)
        sm.on_system_sleep()
        sm.on_system_wake(idle_seconds=30.0)
        sm.pause()
        sm.resume()
        sm.on_natural_rest()
        sm.stop_protection()
        self.assertEqual(sm.get_state(), AppState.INACTIVE)

    def test_timer_engine_exception_tolerated(self) -> None:
        """TimerEngine 方法抛异常时状态机不崩溃。"""

        class BrokenTimer:
            def reset(self):
                raise RuntimeError("broken")

            def pause(self):
                raise RuntimeError("broken")

            def resume(self):
                raise RuntimeError("broken")

        sm = StateMachine(
            timer_engine=BrokenTimer(), event_bus=EventBus()
        )
        sm.start_protection()  # reset 抛异常应被捕获
        self.assertEqual(sm.get_state(), AppState.ACTIVE)
        sm.on_system_lock()  # pause 抛异常应被捕获
        self.assertEqual(sm.get_state(), AppState.LOCKED)
        sm.on_system_unlock(idle_seconds=30.0)  # resume 抛异常应被捕获
        self.assertEqual(sm.get_state(), AppState.ACTIVE)


# ---------------------------------------------------------------------------
# 线程安全
# ---------------------------------------------------------------------------
class TestThreadSafety(unittest.TestCase):
    """多线程并发访问线程安全测试。"""

    def test_concurrent_transitions_no_crash(self) -> None:
        """多线程并发触发各种转换不崩溃。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()

        stop_event = threading.Event()
        errors: list[Exception] = []
        barrier = threading.Barrier(6)

        def worker(worker_id: int) -> None:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return
            while not stop_event.is_set():
                try:
                    if worker_id % 6 == 0:
                        sm.on_activity_changed(ActivityState.IDLE)
                    elif worker_id % 6 == 1:
                        sm.on_activity_changed(ActivityState.ACTIVE)
                    elif worker_id % 6 == 2:
                        sm.on_break_warning()
                    elif worker_id % 6 == 3:
                        sm.on_break_triggered("short")
                    elif worker_id % 6 == 4:
                        sm.on_break_completed()
                    elif worker_id % 6 == 5:
                        sm.on_natural_rest()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        # 运行一段时间
        import time as _time

        _time.sleep(0.5)
        stop_event.set()
        for t in threads:
            t.join(timeout=5.0)

        self.assertEqual(errors, [])
        # 最终状态应是合法状态之一
        self.assertIn(
            sm.get_state(),
            {
                AppState.ACTIVE,
                AppState.IDLE,
                AppState.BREAK_WARNING,
                AppState.SHORT_BREAK,
            },
        )

    def test_concurrent_get_state_safe(self) -> None:
        """多线程并发读取状态不崩溃。"""
        sm, _, _, _ = make_sm()
        sm.start_protection()
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(1000):
                    _ = sm.get_state()
                    _ = sm.can_transition(AppState.ACTIVE)
                    _ = sm.get_history()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(errors, [])

    def test_concurrent_event_handlers_safe(self) -> None:
        """多线程并发发布事件，多个订阅者不崩溃。"""
        bus = EventBus()
        sm = StateMachine(timer_engine=None, event_bus=bus)

        received: list[dict] = []
        received_lock = threading.Lock()

        def handler1(data: dict) -> None:
            with received_lock:
                received.append(data)

        def handler2(data: dict) -> None:
            with received_lock:
                received.append(data)

        bus.subscribe(EventType.STATE_CHANGED, handler1)
        bus.subscribe(EventType.STATE_CHANGED, handler2)

        sm.start_protection()
        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def transitioner() -> None:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return
            for _ in range(200):
                try:
                    sm.on_break_warning()
                    sm.on_break_triggered()
                    sm.on_break_completed()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=transitioner) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(errors, [])
        # 两个 handler 都应收到事件
        self.assertGreater(len(received), 0)


# ---------------------------------------------------------------------------
# 边界与容错
# ---------------------------------------------------------------------------
class TestEdgeCases(unittest.TestCase):
    """边界条件与容错测试。"""

    def test_break_type_default(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertEqual(sm.break_type, "short")

    def test_break_type_set_after_trigger(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("long")
        self.assertEqual(sm.break_type, "long")

    def test_invalid_break_type_defaults_to_short(self) -> None:
        sm, _, _, _ = make_sm()
        sm.start_protection()
        sm.on_break_warning()
        sm.on_break_triggered("invalid_type")  # type: ignore[arg-type]
        self.assertEqual(sm.get_state(), AppState.SHORT_BREAK)
        self.assertEqual(sm.break_type, "short")

    def test_activity_monitor_exception_handled(self) -> None:
        """activity_monitor.get_idle_seconds() 抛异常时解锁仍可工作（保守不重置）。"""

        class BrokenMonitor:
            def get_idle_seconds(self) -> float:
                raise RuntimeError("broken")

        sm = StateMachine(
            timer_engine=None, event_bus=EventBus(),
            activity_monitor=BrokenMonitor(),
        )
        sm.start_protection()
        sm.on_system_lock()
        # 解锁不崩溃，不重置
        self.assertTrue(sm.on_system_unlock())
        self.assertEqual(sm.get_state(), AppState.ACTIVE)

    def test_get_idle_seconds_none_when_no_monitor(self) -> None:
        sm, _, _, _ = make_sm()
        self.assertIsNone(sm._get_idle_seconds())  # noqa: SLF001

    def test_max_history_constant(self) -> None:
        self.assertEqual(StateMachine.MAX_HISTORY, 10)

    def test_stop_protection_publishes_event(self) -> None:
        sm, _, _, collector = make_sm()
        sm.start_protection()
        collector.clear()
        sm.stop_protection()
        events = collector.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "stop_protection")
        self.assertEqual(events[0]["new_state"], "INACTIVE")


if __name__ == "__main__":
    unittest.main()
