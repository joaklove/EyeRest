"""屏幕暴露会话（ScreenSessionEngine）与眨眼引擎（BlinkEngine）单元测试。

V1.0 正式版：BlinkEngine 采用 Blink Cycle 模型（60s 周期 + 周期内多次 Cue）。
"""

from __future__ import annotations

import pytest

from app.config import defaults
from app.core.blink_engine import BlinkEngine
from app.core.clock import FakeClock, MonotonicClock
from app.core.event_bus import EventBus, EventType
from app.core.screen_session import ScreenSessionEngine, ScreenSessionState


class _Idle:
    """可控的 idle_provider：返回固定值或跟随 value 变化。"""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _advance(engine: ScreenSessionEngine, clock: FakeClock, seconds: int, idle: _Idle) -> None:
    """推进 N 秒：时钟前进、idle 同步增长、引擎每秒 tick。"""
    for _ in range(seconds):
        clock.advance(1)
        idle.value += 1
        engine.tick()


# ---------------------------------------------------------------------------
# MonotonicClock
# ---------------------------------------------------------------------------
class TestMonotonicClock:
    def test_now_monotonic(self):
        clock = MonotonicClock()
        t1 = clock.now()
        t2 = clock.now()
        assert t2 >= t1

    def test_deadline_and_remaining(self):
        clock = FakeClock()
        end = clock.deadline(10)
        assert clock.remaining(end) == pytest.approx(10.0)
        clock.advance(4)
        assert clock.remaining(end) == pytest.approx(6.0)
        clock.advance(10)
        assert clock.remaining(end) == 0.0

    def test_remaining_never_negative(self):
        clock = FakeClock()
        end = clock.deadline(5)
        clock.advance(100)
        assert clock.remaining(end) == 0.0
        assert MonotonicClock().remaining(-1) == 0.0

    def test_fake_clock_advance(self):
        clock = FakeClock(start=100)
        assert clock.now() == pytest.approx(100.0)
        clock.advance(2.5)
        assert clock.now() == pytest.approx(102.5)
        clock.set(0)
        assert clock.now() == 0.0


# ---------------------------------------------------------------------------
# ScreenSessionEngine：基础行为
# ---------------------------------------------------------------------------
class TestScreenSessionBasics:
    def test_starts_on(self):
        eng = ScreenSessionEngine(clock=FakeClock(), idle_provider=_Idle(0))
        assert eng.state is ScreenSessionState.ON

    def test_accumulates_exposure_when_active(self):
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        for _ in range(120):
            clock.advance(1)
            eng.tick()
        assert eng.exposure_seconds == pytest.approx(120.0)

    def test_uses_real_delta_not_fixed_one(self):
        """一次迟到的大步进 tick 必须按真实时间差累计，而非 +1。"""
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        eng.tick()  # 建立基准
        clock.advance(30)  # 模拟 tick 迟到 30 秒
        eng.tick()
        assert eng.exposure_seconds == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# ScreenSessionEngine：控制接口
# ---------------------------------------------------------------------------
class TestScreenSessionControl:
    def test_reset_session(self):
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        for _ in range(100):
            clock.advance(1)
            eng.tick()
        assert eng.exposure_seconds == pytest.approx(100.0)

        eng.reset_session("manual")
        assert eng.exposure_seconds == 0.0
        assert eng.state is ScreenSessionState.ON

    def test_snapshot_fields(self):
        eng = ScreenSessionEngine(clock=FakeClock(), idle_provider=_Idle(3))
        eng.tick()
        snap = eng.get_snapshot()
        assert snap.idle_seconds == 3.0
        assert snap.ended_reason is None

    def test_update_thresholds(self):
        clock = FakeClock()
        idle = _Idle(0)
        eng = ScreenSessionEngine(
            clock=clock, idle_provider=idle, away_threshold=10, natural_rest_threshold=99999
        )
        for _ in range(15):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        assert eng.state is ScreenSessionState.AWAY

        eng.update_thresholds(away=600)  # 阈值放宽后应回到 ON
        idle.value = 0
        _advance(eng, clock, 1, idle)
        assert eng.state is ScreenSessionState.ON


# ---------------------------------------------------------------------------
# ScreenSessionEngine：关键场景
# ---------------------------------------------------------------------------
class TestScreenSessionKeyScenarios:
    def test_reading_pdf_3min_is_not_rest(self):
        """看 PDF 3 分钟不碰键鼠：不重置、继续累计暴露（idle < 180s）。"""
        clock = FakeClock()
        idle = _Idle(0)
        eng = ScreenSessionEngine(clock=clock, idle_provider=idle)

        for _ in range(60):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        assert eng.exposure_seconds == pytest.approx(60.0)

        # 继续无输入 120 秒（累计 idle 180s 内不触发自然休息，继续累计暴露）
        for _ in range(120):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        # idle 到达 180s 阈值时转入 AWAY，暴露停在 ~179s，远大于 60s
        assert eng.exposure_seconds >= 175.0

    def test_natural_rest_after_5min_idle(self):
        """idle ≥ 300s 判定自然休息：会话结束、发布事件。"""
        clock = FakeClock()
        idle = _Idle(0)
        bus = EventBus()
        events = []
        bus.subscribe(EventType.NATURAL_REST_DETECTED, lambda d: events.append(d))
        eng = ScreenSessionEngine(clock=clock, idle_provider=idle, event_bus=bus)

        for _ in range(60):
            clock.advance(1)
            eng.tick()
        for _ in range(300):
            clock.advance(1)
            idle.value += 1
            eng.tick()

        assert eng.state is ScreenSessionState.OFF
        assert len(events) == 1

    def test_returns_from_away_resumes_counting(self):
        """AWAY 恢复后接着累计，不清零。"""
        clock = FakeClock()
        idle = _Idle(0)
        eng = ScreenSessionEngine(
            clock=clock, idle_provider=idle, away_threshold=10, natural_rest_threshold=99999
        )
        for _ in range(30):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        before = eng.exposure_seconds

        idle.value = 0
        for _ in range(10):
            clock.advance(1)
            eng.tick()
        assert eng.exposure_seconds > before

    def test_away_does_not_emit_natural_rest(self):
        """AWAY（未达自然休息阈值）不应发布 NATURAL_REST_DETECTED。"""
        clock = FakeClock()
        idle = _Idle(0)
        bus = EventBus()
        events = []
        bus.subscribe(EventType.NATURAL_REST_DETECTED, lambda d: events.append(d))
        eng = ScreenSessionEngine(
            clock=clock, idle_provider=idle, away_threshold=10,
            natural_rest_threshold=99999, event_bus=bus,
        )
        for _ in range(30):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        assert eng.state is ScreenSessionState.AWAY
        assert events == []

    def test_lock_ends_session_immediately(self):
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        for _ in range(10):
            clock.advance(1)
            eng.tick()
        eng.on_system_lock()
        assert eng.state is ScreenSessionState.OFF
        assert eng.get_snapshot().ended_reason == "lock"
        # 锁屏期间 tick 不累计
        clock.advance(60)
        eng.tick()
        assert eng.exposure_seconds == pytest.approx(10.0)

    def test_unlock_starts_fresh_session(self):
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        for _ in range(10):
            clock.advance(1)
            eng.tick()
        eng.on_system_lock()
        eng.on_system_unlock()
        assert eng.state is ScreenSessionState.ON
        assert eng.exposure_seconds == 0.0

    def test_sleep_ends_session(self):
        clock = FakeClock()
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0))
        for _ in range(10):
            clock.advance(1)
            eng.tick()
        eng.on_system_sleep()
        assert eng.state is ScreenSessionState.OFF
        assert eng.get_snapshot().ended_reason == "sleep"
        eng.on_system_wake()
        assert eng.state is ScreenSessionState.ON
        assert eng.exposure_seconds == 0.0

    def test_session_changed_event(self):
        clock = FakeClock()
        idle = _Idle(0)
        bus = EventBus()
        events = []
        bus.subscribe(EventType.SCREEN_SESSION_CHANGED, lambda d: events.append(d))
        eng = ScreenSessionEngine(
            clock=clock, idle_provider=idle, away_threshold=10,
            natural_rest_threshold=99999, event_bus=bus,
        )
        for _ in range(15):
            clock.advance(1)
            idle.value += 1
            eng.tick()
        assert any(e.get("new_state") == "away" for e in events)

    def test_no_event_when_state_unchanged(self):
        clock = FakeClock()
        bus = EventBus()
        events = []
        bus.subscribe(EventType.SCREEN_SESSION_CHANGED, lambda d: events.append(d))
        eng = ScreenSessionEngine(clock=clock, idle_provider=_Idle(0), event_bus=bus)
        for _ in range(10):
            clock.advance(1)
            eng.tick()
        assert events == []

    def test_idle_provider_failure_treated_as_active(self):
        """idle_provider 抛异常时按活跃处理，不崩溃。"""
        clock = FakeClock()

        def bad_provider():
            raise RuntimeError("boom")

        eng = ScreenSessionEngine(clock=clock, idle_provider=bad_provider)
        clock.advance(5)
        eng.tick()
        assert eng.state is ScreenSessionState.ON
        assert eng.exposure_seconds == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# BlinkEngine（V1.0 正式版 Blink Cycle 模型）
# ---------------------------------------------------------------------------
class TestBlinkEngine:
    """60s 周期 + 周期内多次 Cue：文字与动画交替。"""

    def _make(self, clock, idle=None, bus=None, **kw):
        idle = idle if idle is not None else _Idle(0)
        session = ScreenSessionEngine(clock=clock, idle_provider=idle)
        blink = BlinkEngine(clock=clock, session_engine=session, event_bus=bus, **kw)
        return session, blink

    def test_cue_every_interval(self):
        """周期内每 10 秒一次 Cue，第 1/3/5 次带文字、第 2/4 次仅动画。"""
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.BLINK_CUE, lambda d: cues.append(d))
        session, blink = self._make(clock, bus=bus, cycle_seconds=60, cue_interval=10)

        for _ in range(35):
            clock.advance(1)
            session.tick()
            blink.tick()

        # 10/20/30s 三次 Cue
        assert len(cues) == 3
        assert [c["with_text"] for c in cues] == [True, False, True]
        assert [c["index"] for c in cues] == [0, 1, 2]
        assert all(c["kind"] == "blink" for c in cues)

    def test_cycle_boundary_resets_cue_counter(self):
        """60 秒周期结束时发布 cycle_finished，Cue 序号归零重新交替。"""
        clock = FakeClock()
        bus = EventBus()
        cues = []
        finished = []
        bus.subscribe(EventType.BLINK_CUE, lambda d: cues.append(d))
        bus.subscribe(EventType.BLINK_CYCLE_FINISHED, lambda d: finished.append(d))
        session, blink = self._make(clock, bus=bus, cycle_seconds=60, cue_interval=10)

        for _ in range(70):
            clock.advance(1)
            session.tick()
            blink.tick()

        # 第一周期：10/20/30/40/50s 共 5 次 Cue；60s 周期结束
        assert len(finished) == 1
        assert finished[0]["cue_count"] == 5
        # 第二周期：70s 处出现第 6 次 Cue（序号 0，重新带文字）
        assert len(cues) == 6
        assert cues[5]["index"] == 0
        assert cues[5]["with_text"] is True

    def test_seconds_until_cycle_end(self):
        clock = FakeClock()
        session, blink = self._make(clock, cycle_seconds=60, cue_interval=10)
        for _ in range(25):
            clock.advance(1)
            session.tick()
            blink.tick()
        assert blink.seconds_until_cycle_end() == pytest.approx(35.0)

    def test_paused_during_away(self):
        """AWAY 期间冻结眨眼计时，回来后接着算（不重置）。"""
        clock = FakeClock()
        idle = _Idle(0)
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.BLINK_CUE, lambda d: cues.append(d))
        session, blink = self._make(
            clock, idle=idle, bus=bus, cycle_seconds=3600, cue_interval=10
        )

        for _ in range(5):
            clock.advance(1)
            session.tick()
            blink.tick()
        assert blink.seconds_until_next_cue() == pytest.approx(5.0)

        # 突然离开：idle 跨过 AWAY 阈值但未达自然休息
        idle.value = 200
        for _ in range(90):
            clock.advance(1)
            idle.value += 1
            session.tick()
            blink.tick()

        assert session.state is ScreenSessionState.AWAY
        assert cues == []  # AWAY 期间不触发
        assert blink.seconds_until_next_cue() == pytest.approx(5.0)  # 冻结

        # 回来：再过 5 秒应触发
        idle.value = 0
        for _ in range(5):
            clock.advance(1)
            session.tick()
            blink.tick()
        assert len(cues) == 1

    def test_disabled_engine_never_cues(self):
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.BLINK_CUE, lambda d: cues.append(d))
        session, blink = self._make(clock, bus=bus, cue_interval=5, enabled=False)
        for _ in range(30):
            clock.advance(1)
            session.tick()
            blink.tick()
        assert cues == []

    def test_reset_after_natural_rest(self):
        clock = FakeClock()
        idle = _Idle(0)
        session, blink = self._make(clock, idle=idle, cycle_seconds=60, cue_interval=10)

        for _ in range(25):
            clock.advance(1)
            session.tick()
            blink.tick()
        # 10s/20s 两次 Cue 已发出，25s 处距下次 Cue 还剩 5s
        assert blink.seconds_until_next_cue() == pytest.approx(5.0)

        # 离开超过 5 分钟 → 自然休息 → 重置周期
        for _ in range(310):
            clock.advance(1)
            idle.value += 1
            session.tick()
            blink.tick()

        assert session.state is ScreenSessionState.OFF
        assert blink.seconds_until_next_cue() == pytest.approx(10.0)

    def test_update_settings(self):
        clock = FakeClock()
        session, blink = self._make(clock, cycle_seconds=60, cue_interval=10)
        blink.update_settings(cycle_seconds=30, cue_interval=5, cue_duration=2.0)
        assert blink.cycle_seconds == 30.0
        assert blink.cue_interval == 5.0
        assert blink.cue_duration == 2.0

    def test_update_settings_disable_resets(self):
        """禁用后周期状态被重置，重新启用时从新周期开始。"""
        clock = FakeClock()
        session, blink = self._make(clock, cycle_seconds=60, cue_interval=10)
        blink.update_settings(enabled=False)
        assert blink.is_enabled is False
        blink.update_settings(enabled=True)
        assert blink.seconds_until_next_cue() == pytest.approx(10.0)

    def test_no_crash_without_session(self):
        blink = BlinkEngine(clock=FakeClock())
        blink.tick()  # 不应抛异常
        assert blink.seconds_until_next_cue() > 0
