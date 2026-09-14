"""全屏内容消费豁免：``SHQueryUserNotificationState`` + ScreenSessionEngine。

## 背景（这是一个真实修复的回归防线）

旧实现有个自相矛盾的地方：``screen_session.py`` 一边写着「看视频 / 会议：
完全不碰键鼠，视觉负荷最高」，一边又用同一套阈值（180s）把「看视频」
判成 AWAY —— 而 AWAY 会让 BlinkEngine 停发眨眼提示。

实测日志坐实了后果：一个上午 83 分钟里，有 6 段静默、合计约 26 分钟
完全没有眨眼提示，且每一段都与 idle 超过 180s 严格对应。

修正方式：检测到「全屏内容消费」时改用放宽阈值。本文件守住该行为。
"""

from __future__ import annotations

import pytest

from app.config import defaults
from app.core.blink_engine import BlinkEngine
from app.core.clock import FakeClock
from app.core.event_bus import EventBus, EventType
from app.core.screen_session import ScreenSessionEngine, ScreenSessionState
from app.windows.fullscreen_detector import (
    NotificationState,
    is_fullscreen_content_state,
)


class _Idle:
    """可控的 idle_provider。"""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Flag:
    """可控的 fullscreen_provider，支持模拟抛异常。"""

    def __init__(self, value: bool = False, *, raises: bool = False) -> None:
        self.value = value
        self.raises = raises

    def __call__(self) -> bool:
        if self.raises:
            raise RuntimeError("fullscreen provider boom")
        return self.value


def _advance(
    engine: ScreenSessionEngine, clock: FakeClock, seconds: int, idle: _Idle
) -> None:
    """推进 N 秒：时钟前进、idle 同步增长、引擎每秒 tick。"""
    for _ in range(seconds):
        clock.advance(1)
        idle.value += 1
        engine.tick()


# ---------------------------------------------------------------------------
# 通知状态映射（纯函数）
# ---------------------------------------------------------------------------
class TestNotificationStateMapping:
    @pytest.mark.parametrize(
        "code",
        [
            NotificationState.BUSY,
            NotificationState.RUNNING_D3D_FULL_SCREEN,
            NotificationState.PRESENTATION_MODE,
            NotificationState.APP,
            2,
            3,
            4,
            7,
        ],
    )
    def test_fullscreen_content_states(self, code):
        assert is_fullscreen_content_state(code) is True

    @pytest.mark.parametrize(
        "code",
        [
            NotificationState.NOT_PRESENT,
            NotificationState.ACCEPTS_NOTIFICATIONS,
            NotificationState.QUIET_TIME,
            1,
            5,
            6,
            None,
            0,
            99,
        ],
    )
    def test_non_fullscreen_states(self, code):
        assert is_fullscreen_content_state(code) is False

    def test_not_present_is_not_content_consumption(self):
        """用户不在（屏保）与「在看全屏内容」是相反的两件事，不能混为一谈。"""
        assert is_fullscreen_content_state(NotificationState.NOT_PRESENT) is False

    def test_defaults_are_relaxed(self):
        """放宽阈值必须显著大于常规阈值，否则豁免没有意义。"""
        assert defaults.FULLSCREEN_AWAY_THRESHOLD > defaults.AWAY_THRESHOLD
        assert (
            defaults.FULLSCREEN_NATURAL_REST_THRESHOLD
            > defaults.NATURAL_REST_THRESHOLD
        )


# ---------------------------------------------------------------------------
# ScreenSessionEngine 的全屏豁免
# ---------------------------------------------------------------------------
class TestFullscreenExemption:
    def test_without_provider_behaviour_unchanged(self):
        """未注入 provider 时行为与修复前一致：超 AWAY 阈值即判离开。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(clock=clock, idle_provider=idle)
        _advance(engine, clock, int(defaults.AWAY_THRESHOLD) + 2, idle)
        assert engine.state is ScreenSessionState.AWAY

    def test_fullscreen_defers_away_and_keeps_exposure(self):
        """全屏内容消费期间不判离开，暴露时间继续累计。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(
            clock=clock, idle_provider=idle, fullscreen_provider=_Flag(True)
        )
        _advance(engine, clock, int(defaults.AWAY_THRESHOLD) + 60, idle)

        assert engine.state is ScreenSessionState.ON
        assert engine.exposure_seconds > defaults.AWAY_THRESHOLD
        assert engine.get_snapshot().fullscreen is True

    def test_fullscreen_eventually_away(self):
        """人真走了（视频挂着）时放宽阈值兜底，仍会判离开。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(
            clock=clock,
            idle_provider=idle,
            fullscreen_provider=_Flag(True),
            fullscreen_away_threshold=100,
            fullscreen_natural_rest_threshold=200,
        )
        _advance(engine, clock, 120, idle)
        assert engine.state is ScreenSessionState.AWAY

    def test_fullscreen_eventually_natural_rest(self):
        clock = FakeClock()
        idle = _Idle(0)
        bus = EventBus()
        events: list[dict] = []
        bus.subscribe(EventType.NATURAL_REST_DETECTED, events.append)

        engine = ScreenSessionEngine(
            clock=clock,
            idle_provider=idle,
            event_bus=bus,
            fullscreen_provider=_Flag(True),
            fullscreen_away_threshold=100,
            fullscreen_natural_rest_threshold=200,
        )
        _advance(engine, clock, 210, idle)

        assert engine.state is ScreenSessionState.OFF
        assert len(events) == 1

    def test_entering_fullscreen_resumes_from_away(self):
        """刚读完长文档（已 AWAY）就全屏放视频 → 应立即恢复 ON。

        这是修复前后差异最明显的一步：旧实现会卡在 AWAY 里，
        因为从 AWAY 回到 ON 要求 idle ≤ resume_threshold，
        而看视频的人恰恰不会去动键鼠。
        """
        clock = FakeClock()
        idle = _Idle(0)
        fs = _Flag(False)
        engine = ScreenSessionEngine(
            clock=clock, idle_provider=idle, fullscreen_provider=fs
        )

        _advance(engine, clock, int(defaults.AWAY_THRESHOLD) + 2, idle)
        assert engine.state is ScreenSessionState.AWAY

        fs.value = True
        clock.advance(1)
        idle.value += 1
        engine.tick()
        assert engine.state is ScreenSessionState.ON

    def test_gray_zone_still_away_without_fullscreen(self):
        """对照组：非全屏时，idle 落在灰区仍保持 AWAY。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(clock=clock, idle_provider=idle)

        _advance(engine, clock, int(defaults.AWAY_THRESHOLD) + 2, idle)
        assert engine.state is ScreenSessionState.AWAY

        idle.value = defaults.SESSION_RESUME_THRESHOLD + 10
        clock.advance(1)
        engine.tick()
        assert engine.state is ScreenSessionState.AWAY

    def test_provider_exception_is_swallowed(self):
        """检测器抛异常时按「非全屏」处理，不能让引擎崩掉。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(
            clock=clock, idle_provider=idle, fullscreen_provider=_Flag(raises=True)
        )
        _advance(engine, clock, 5, idle)

        assert engine.state is ScreenSessionState.ON
        assert engine.get_snapshot().fullscreen is False

    def test_update_thresholds_hot_swap(self):
        """放宽阈值支持热更新。"""
        clock = FakeClock()
        idle = _Idle(0)
        engine = ScreenSessionEngine(
            clock=clock, idle_provider=idle, fullscreen_provider=_Flag(True)
        )
        engine.update_thresholds(fullscreen_away=50, fullscreen_natural_rest=90)
        _advance(engine, clock, 60, idle)
        assert engine.state is ScreenSessionState.AWAY


# ---------------------------------------------------------------------------
# 端到端：全屏看视频时眨眼提示必须继续发
# ---------------------------------------------------------------------------
class _CountingCues:
    """收集 BLINK_CUE 事件。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def __call__(self, data: dict) -> None:
        self.items.append(data)


def _run_video_session(
    *, fullscreen: bool, seconds: int
) -> tuple[ScreenSessionEngine, list[dict]]:
    """模拟「全程不碰键鼠地看 N 秒视频」，返回会话与发出的 Cue 列表。"""
    clock = FakeClock()
    idle = _Idle(0)
    bus = EventBus()
    cues = _CountingCues()
    bus.subscribe(EventType.BLINK_CUE, cues)

    session = ScreenSessionEngine(
        clock=clock,
        idle_provider=idle,
        event_bus=bus,
        fullscreen_provider=_Flag(fullscreen),
    )
    blink = BlinkEngine(
        clock=clock,
        session_engine=session,
        cycle_seconds=60.0,
        cue_interval=10.0,
        event_bus=bus,
    )

    for _ in range(seconds):
        clock.advance(1)
        idle.value += 1
        session.tick()
        blink.tick()

    return session, cues.items


class TestBlinkCueDuringFullscreen:
    """直接守住用户报的现象：全屏看视频时「小眼睛」不能消失。"""

    def test_cues_keep_flowing_under_fullscreen(self):
        session, cues = _run_video_session(fullscreen=True, seconds=300)

        assert session.state is ScreenSessionState.ON
        assert session.exposure_seconds == pytest.approx(300.0)
        # 每 60s 周期发 5 次 Cue（10/20/30/40/50s，周期边界会重置节奏），
        # 300 秒 = 5 个完整周期 = 25 次，一次都不能少
        assert len(cues) == 25

    def test_cues_stop_without_fullscreen(self):
        """对照：同样 5 分钟不动、但没检测到全屏 → 提示会中途停掉。

        这条对照证明修复针对的是真问题，而不是「本来就没事」。
        """
        session, cues = _run_video_session(fullscreen=False, seconds=300)

        # 第 180 秒进入 AWAY，暴露时间冻结在 179s，Cue 跟着停 → 仅剩 15 次
        assert len(cues) == 15
        assert session.exposure_seconds < 300.0
