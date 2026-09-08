"""活动提醒引擎（MoveEngine）单元测试。

V1.0 第三层节奏：45 分钟（默认）暴露时间 → 活动提醒；
活动完成 / 深度休息 / 自然休息后重置；远眺不影响 Move 计时。
"""

from __future__ import annotations

import pytest

from app.core.blink_engine import BlinkEngine  # noqa: F401  (保持导入一致性)
from app.core.clock import FakeClock
from app.core.event_bus import EventBus, EventType
from app.core.move_engine import MoveEngine
from app.core.screen_session import ScreenSessionEngine, ScreenSessionState


class _Idle:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _make(clock, idle=None, bus=None, **kw):
    idle = idle if idle is not None else _Idle(0)
    session = ScreenSessionEngine(clock=clock, idle_provider=idle)
    engine = MoveEngine(clock=clock, session_engine=session, event_bus=bus, **kw)
    return session, engine


def _run(session, engine, clock, seconds, idle=None):
    for _ in range(seconds):
        clock.advance(1)
        if idle is not None:
            idle.value += 1
        session.tick()
        engine.tick()


class TestMoveEngine:
    def test_cue_after_interval(self):
        """暴露累计达到间隔（默认 45 分钟）时发布 MOVE_CUE。"""
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.MOVE_CUE, lambda d: cues.append(d))
        session, move = _make(clock, bus=bus, interval=60, duration=180)

        _run(session, move, clock, 59)
        assert cues == []

        clock.advance(1)
        session.tick()
        move.tick()
        assert len(cues) == 1
        assert cues[0]["kind"] == "move"
        assert cues[0]["duration"] == 180

    def test_re_cue_every_60s_while_unanswered(self):
        """提醒未响应时每 60 秒温和重复，不刷屏也不遗忘。"""
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.MOVE_CUE, lambda d: cues.append(d))
        session, move = _make(clock, bus=bus, interval=30)

        _run(session, move, clock, 30)
        assert len(cues) == 1
        _run(session, move, clock, 59)
        assert len(cues) == 1  # 60 秒内不重复
        clock.advance(1)
        session.tick()
        move.tick()
        assert len(cues) == 2  # 60 秒后再次提醒
        assert move.is_cue_active()

    def test_complete_activity_resets(self):
        """完成活动后计时重置，重新累计一个完整周期。"""
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.MOVE_CUE, lambda d: cues.append(d))
        session, move = _make(clock, bus=bus, interval=30)

        _run(session, move, clock, 30)
        assert len(cues) == 1
        move.complete_activity()
        assert not move.is_cue_active()
        assert move.get_remaining_seconds() == pytest.approx(30.0)

        _run(session, move, clock, 29)
        assert len(cues) == 1
        clock.advance(1)
        session.tick()
        move.tick()
        assert len(cues) == 2

    def test_natural_rest_resets(self):
        """自然休息（人已离开屏幕）重置 Move 计时。"""
        clock = FakeClock()
        idle = _Idle(0)
        session, move = _make(clock, idle=idle, interval=30)
        _run(session, move, clock, 25, idle)
        assert move.get_remaining_seconds() == pytest.approx(5.0)

        for _ in range(310):
            clock.advance(1)
            idle.value += 1
            session.tick()
            move.tick()

        assert session.state is ScreenSessionState.OFF
        assert move.get_remaining_seconds() == pytest.approx(30.0)
        assert not move.is_cue_active()

    def test_away_freezes_accumulation(self):
        """AWAY 期间暴露不增长，Move 计时冻结。"""
        clock = FakeClock()
        idle = _Idle(0)
        session, move = _make(
            clock, idle=idle, interval=30,
        )
        # 直接构造一个 away_threshold 很小的会话来测冻结
        session2 = ScreenSessionEngine(
            clock=clock, idle_provider=idle, away_threshold=10,
            natural_rest_threshold=99999,
        )
        move2 = MoveEngine(clock=clock, session_engine=session2, interval=30)
        for _ in range(5):
            clock.advance(1)
            session2.tick()
            move2.tick()
        assert move2.get_remaining_seconds() == pytest.approx(25.0)

        idle.value = 200  # 进入 AWAY
        for _ in range(60):
            clock.advance(1)
            idle.value += 1
            session2.tick()
            move2.tick()
        assert session2.state is ScreenSessionState.AWAY
        assert move2.get_remaining_seconds() == pytest.approx(25.0)  # 冻结

    def test_disabled_never_cues(self):
        clock = FakeClock()
        bus = EventBus()
        cues = []
        bus.subscribe(EventType.MOVE_CUE, lambda d: cues.append(d))
        session, move = _make(clock, bus=bus, interval=5, enabled=False)
        _run(session, move, clock, 30)
        assert cues == []

    def test_update_settings(self):
        clock = FakeClock()
        session, move = _make(clock, interval=2700, duration=180)
        move.update_settings(interval=3600, duration=300, enabled=False)
        assert move.interval == 3600.0
        assert move.duration == 300.0
        assert move.is_enabled is False

    def test_no_crash_without_session(self):
        move = MoveEngine(clock=FakeClock())
        move.tick()
        assert move.get_remaining_seconds() > 0
