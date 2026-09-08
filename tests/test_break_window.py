"""休息窗口（BreakWindow / BreakWarningPopup）单元测试。

使用 Qt offscreen 平台插件，无需真实显示器即可测试窗口创建、显示、
倒计时更新与信号触发。

运行::

    cd apps/EyeRest
    python -m pytest tests/test_break_window.py -v
"""

from __future__ import annotations

import os
import unittest

# 必须在导入 PySide6 之前设置 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import defaults  # noqa: E402
from app.core.break_engine import BreakEngine  # noqa: E402
from app.core.clock import FakeClock  # noqa: E402
from app.core.event_bus import EventBus, EventType  # noqa: E402
from app.i18n import get_language, set_language  # noqa: E402
from app.ui.break_window import BreakWindow, BreakWarningPopup  # noqa: E402


# 全局 QApplication（Qt 要求单例）
_app: QApplication | None = None


def _get_app() -> QApplication:
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


def _bind_fake_clock(widget: object, clock: FakeClock) -> None:
    """把假时钟绑到窗口，并让每次倒计时推进 1 秒（模拟 QTimer 1000ms）。

    V1.0 起倒计时基于「目标时刻」计算，测试必须显式推进时间。
    同时包装 ``update_countdown``（BreakWindow）与 ``_tick``（警告弹窗）。
    """
    widget._clock = clock  # type: ignore[attr-defined]
    for name in ("update_countdown", "_tick"):
        original = getattr(widget, name, None)
        if original is None or not callable(original):
            continue

        def _make_tick(fn):  # 闭包固化 fn，避免循环变量晚绑定
            def _tick() -> None:
                clock.advance(1.0)
                fn()

            return _tick

        setattr(widget, name, _make_tick(original))


class FakeTimerEngine:
    """Fake TimerEngine，供 BreakEngine 测试注入。"""

    def __init__(self) -> None:
        self.active_seconds: float = 0.0
        self.reset_calls: int = 0

    def get_active_seconds(self) -> float:
        return self.active_seconds

    def reset(self) -> None:
        self.reset_calls += 1
        self.active_seconds = 0.0

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class TestBreakWindow(unittest.TestCase):
    """BreakWindow 单元测试。"""

    def setUp(self) -> None:
        self.app = _get_app()
        # 强制不显示真实窗口（offscreen 下 show() 也不会弹窗，但保险起见）
        self.window = BreakWindow(break_type="short", duration=20)
        self.clock = FakeClock()
        _bind_fake_clock(self.window, self.clock)

    def tearDown(self) -> None:
        self.window.hide_break()
        self.window.deleteLater()
        self.app.processEvents()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def test_init_short_break_defaults(self) -> None:
        win = BreakWindow(break_type="short")
        self.assertEqual(win.break_type, "short")
        self.assertEqual(win.duration, defaults.SHORT_BREAK_DURATION)
        self.assertEqual(win.remaining, defaults.SHORT_BREAK_DURATION)
        win.deleteLater()

    def test_init_long_break_defaults(self) -> None:
        win = BreakWindow(break_type="long")
        self.assertEqual(win.break_type, "long")
        self.assertEqual(win.duration, defaults.LONG_BREAK_DURATION)
        self.assertEqual(win.remaining, defaults.LONG_BREAK_DURATION)
        win.deleteLater()

    def test_init_custom_duration(self) -> None:
        win = BreakWindow(break_type="short", duration=7)
        self.assertEqual(win.duration, 7)
        self.assertEqual(win.remaining, 7)
        win.deleteLater()

    def test_window_flags_topmost(self) -> None:
        flags = self.window.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)

    def test_window_modal(self) -> None:
        self.assertEqual(
            self.window.windowModality(),
            Qt.WindowModality.ApplicationModal,
        )

    # ------------------------------------------------------------------
    # 倒计时
    # ------------------------------------------------------------------
    def test_show_break_starts_timer(self) -> None:
        self.window.show_break()
        self.assertTrue(self.window.timer.isActive())
        self.assertEqual(self.window.remaining, self.window.duration)

    def test_update_countdown_decrements(self) -> None:
        self.window.show_break()
        self.window.update_countdown()
        self.assertEqual(self.window.remaining, self.window.duration - 1)

    def test_countdown_reaches_zero(self) -> None:
        completed = []
        self.window.break_completed.connect(lambda t: completed.append(t))
        self.window.show_break()
        # 手动驱动倒计时到 0
        for _ in range(self.window.duration):
            self.window.update_countdown()
        self.assertEqual(self.window.remaining, 0)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0], "short")
        # 完成后定时器停止
        self.assertFalse(self.window.timer.isActive())

    def test_countdown_does_not_go_negative(self) -> None:
        self.window.show_break()
        for _ in range(self.window.duration + 5):
            self.window.update_countdown()
        self.assertEqual(self.window.remaining, 0)

    def test_complete_emits_once(self) -> None:
        completed = []
        self.window.break_completed.connect(lambda t: completed.append(t))
        self.window.show_break()
        for _ in range(self.window.duration):
            self.window.update_countdown()
        # 再次驱动不应重复触发
        self.window.update_countdown()
        self.assertEqual(len(completed), 1)

    # ------------------------------------------------------------------
    # 跳过
    # ------------------------------------------------------------------
    def test_skip_button_emits_signal(self) -> None:
        skipped = []
        self.window.break_skipped.connect(lambda t: skipped.append(t))
        self.window.show_break()
        self.window._on_skip()
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0], "short")
        self.assertFalse(self.window.timer.isActive())

    def test_skip_stops_timer(self) -> None:
        self.window.show_break()
        self.assertTrue(self.window.timer.isActive())
        self.window._on_skip()
        self.assertFalse(self.window.timer.isActive())

    def test_esc_key_triggers_skip(self) -> None:
        skipped = []
        self.window.break_skipped.connect(lambda t: skipped.append(t))
        self.window.show_break()
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.window.keyPressEvent(event)
        self.assertEqual(len(skipped), 1)

    def test_close_event_triggers_skip(self) -> None:
        skipped = []
        self.window.break_skipped.connect(lambda t: skipped.append(t))
        self.window.show_break()
        # 模拟关闭事件（closeEvent 内部触发 skip）
        from PySide6.QtGui import QCloseEvent

        event = QCloseEvent()
        self.window.closeEvent(event)
        self.assertEqual(len(skipped), 1)
        self.assertFalse(event.isAccepted())  # 跳过分支 ignore 关闭

    # ------------------------------------------------------------------
    # 延迟
    # ------------------------------------------------------------------
    def test_postpone_emits_signal(self) -> None:
        postponed = []
        self.window.break_postponed.connect(lambda: postponed.append(True))
        self.window.show_break()
        self.window._on_postpone()
        self.assertEqual(len(postponed), 1)
        self.assertEqual(self.window.postpone_count, 1)

    def test_postpone_increments_count(self) -> None:
        self.window.show_break()
        self.window._on_postpone()
        self.assertEqual(self.window.postpone_count, 1)

    def test_postpone_disabled_after_max(self) -> None:
        self.window.set_postpone_count(self.window.max_postpone)
        self.assertFalse(self.window.postpone_button.isVisible())

    def test_postpone_hidden_after_max(self) -> None:
        postponed = []
        self.window.break_postponed.connect(lambda: postponed.append(True))
        self.window.set_postpone_count(self.window.max_postpone)
        self.window.show_break()
        # 手动调用延迟应被忽略
        self.window._on_postpone()
        self.assertEqual(len(postponed), 0)
        self.assertEqual(self.window.postpone_count, self.window.max_postpone)

    def test_postpone_hint_text(self) -> None:
        self.window.set_postpone_count(0)
        self.assertIn("2", self.window.postpone_hint.text())
        self.window.set_postpone_count(1)
        self.assertIn("1", self.window.postpone_hint.text())
        self.window.set_postpone_count(2)
        self.assertIn("最大", self.window.postpone_hint.text())

    # ------------------------------------------------------------------
    # 复用与配置
    # ------------------------------------------------------------------
    def test_configure_changes_type(self) -> None:
        self.window.configure("long")
        self.assertEqual(self.window.break_type, "long")
        self.assertEqual(self.window.duration, defaults.LONG_BREAK_DURATION)

    def test_configure_changes_duration(self) -> None:
        self.window.configure("short", duration=10)
        self.assertEqual(self.window.duration, 10)

    # ------------------------------------------------------------------
    # 长休息
    # ------------------------------------------------------------------
    def test_long_break_full_countdown(self) -> None:
        completed = []
        win = BreakWindow(break_type="long", duration=5)  # 测试用短时长
        win.break_completed.connect(lambda t: completed.append(t))
        _bind_fake_clock(win, FakeClock())
        win.show_break()
        for _ in range(5):
            win.update_countdown()
        self.assertEqual(win.remaining, 0)
        self.assertEqual(completed, ["long"])
        win.deleteLater()


class TestBreakWarningPopup(unittest.TestCase):
    """BreakWarningPopup 单元测试。"""

    def setUp(self) -> None:
        self.app = _get_app()
        self.popup = BreakWarningPopup(break_type="short", duration=3)
        _bind_fake_clock(self.popup, FakeClock())

    def tearDown(self) -> None:
        self.popup.hide_warning()
        self.popup.deleteLater()
        self.app.processEvents()

    def test_init(self) -> None:
        self.assertEqual(self.popup.break_type, "short")
        self.assertEqual(self.popup.duration, 3)

    def test_show_starts_timer(self) -> None:
        self.popup.show_warning()
        self.assertTrue(self.popup.timer.isActive())

    def test_accept_emits_signal(self) -> None:
        accepted = []
        self.popup.warning_accepted.connect(lambda: accepted.append(True))
        self.popup.show_warning()
        self.popup._on_accept()
        self.assertEqual(len(accepted), 1)

    def test_postpone_emits_signal(self) -> None:
        postponed = []
        self.popup.warning_postponed.connect(lambda: postponed.append(True))
        self.popup.show_warning()
        self.popup._on_postpone()
        self.assertEqual(len(postponed), 1)
        self.assertEqual(self.popup.postpone_count, 1)

    def test_countdown_auto_accepts(self) -> None:
        accepted = []
        self.popup.warning_accepted.connect(lambda: accepted.append(True))
        self.popup.show_warning()
        for _ in range(3):
            self.popup._tick()
        self.assertEqual(len(accepted), 1)

    def test_esc_triggers_accept(self) -> None:
        accepted = []
        self.popup.warning_accepted.connect(lambda: accepted.append(True))
        self.popup.show_warning()
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.popup.keyPressEvent(event)
        self.assertEqual(len(accepted), 1)

    def test_postpone_disabled_after_max(self) -> None:
        self.popup.show_warning()
        self.popup.postpone_count = self.popup.max_postpone
        self.popup._update_postpone_button()
        self.assertFalse(self.popup.postpone_button.isVisible())


class TestBreakEngineIntegration(unittest.TestCase):
    """BreakWindow 与 BreakEngine 的信号集成测试。"""

    def setUp(self) -> None:
        self.app = _get_app()
        self.timer = FakeTimerEngine()
        self.bus = EventBus()
        # 直接使用真实 BreakEngine（不依赖 StateMachine，传 None）
        self.engine = BreakEngine(
            timer_engine=self.timer,
            state_machine=None,
            event_bus=self.bus,
        )
        self.engine.start()

        self.window = BreakWindow(break_type="short", duration=2)
        _bind_fake_clock(self.window, FakeClock())

        # 连接信号到引擎（模拟 main.py 的集成）
        self.window.break_completed.connect(lambda t: self.engine.on_break_complete())
        self.window.break_skipped.connect(lambda t: self.engine.on_break_skip())
        self.window.break_postponed.connect(lambda: self.engine.on_postpone())

    def tearDown(self) -> None:
        self.window.hide_break()
        self.window.deleteLater()
        self.engine.stop()
        self.app.processEvents()

    def test_completed_signal_resets_engine(self) -> None:
        # 模拟引擎进入休息进行中
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        self.assertTrue(self.engine.is_break_in_progress())

        # 窗口倒计时完成
        self.window.show_break()
        for _ in range(2):
            self.window.update_countdown()

        self.assertFalse(self.engine.is_break_in_progress())
        self.assertEqual(self.timer.reset_calls, 1)
        self.assertEqual(self.engine.get_break_count_today(), 1)

    def test_skipped_signal_resets_engine(self) -> None:
        self.timer.active_seconds = defaults.SHORT_WORK_DURATION
        self.engine.tick()
        self.assertTrue(self.engine.is_break_in_progress())

        self.window.show_break()
        self.window._on_skip()

        self.assertFalse(self.engine.is_break_in_progress())
        self.assertEqual(self.timer.reset_calls, 1)

    def test_postpone_signal_calls_engine(self) -> None:
        self.window.show_break()
        self.window._on_postpone()
        self.assertEqual(self.engine.get_postpone_count(), 1)

    def test_postpone_max_two(self) -> None:
        self.window.show_break()
        self.window._on_postpone()
        self.assertEqual(self.engine.get_postpone_count(), 1)

        # 需要重置 window 的 _finished 才能再次触发（模拟下一次休息）
        self.window.configure("short", duration=2)
        self.window.show_break()
        self.window._on_postpone()
        self.assertEqual(self.engine.get_postpone_count(), 2)

        # 第三次：window 内部已达 max_postpone，不发出信号
        self.window.configure("short", duration=2)
        self.window.set_postpone_count(self.window.max_postpone)
        self.window.show_break()
        self.window._on_postpone()
        # 引擎侧仍为 2（未收到新的 postpone 调用）
        self.assertEqual(self.engine.get_postpone_count(), 2)


class TestBreakEventSubscription(unittest.TestCase):
    """main.py 中事件订阅逻辑的模拟测试。"""

    def setUp(self) -> None:
        self.app = _get_app()
        self.bus = EventBus()
        self.window = BreakWindow()
        self.warning = BreakWarningPopup()

        # 模拟 main.py 中的订阅处理
        self.trigger_calls: list[dict] = []
        self.warning_calls: list[dict] = []

        def _on_triggered(data: object) -> None:
            if isinstance(data, dict):
                self.trigger_calls.append(data)
                break_type = str(data.get("break_type", "short"))
                duration = int(data.get("duration", 0)) or None
                self.window.configure(break_type, duration)
                self.window.show_break()

        def _on_warning(data: object) -> None:
            if isinstance(data, dict):
                self.warning_calls.append(data)
                break_type = str(data.get("break_type", "short"))
                self.warning.break_type = "long" if break_type == "long" else "short"
                self.warning.show_warning()

        self.bus.subscribe(EventType.BREAK_TRIGGERED, _on_triggered)
        self.bus.subscribe(EventType.BREAK_WARNING, _on_warning)

    def tearDown(self) -> None:
        self.window.hide_break()
        self.warning.hide_warning()
        self.window.deleteLater()
        self.warning.deleteLater()
        self.app.processEvents()

    def test_break_triggered_shows_window(self) -> None:
        self.bus.publish(
            EventType.BREAK_TRIGGERED,
            {"break_type": "short", "duration": defaults.SHORT_BREAK_DURATION},
        )
        self.assertEqual(len(self.trigger_calls), 1)
        self.assertTrue(self.window.timer.isActive())
        self.assertEqual(self.window.duration, defaults.SHORT_BREAK_DURATION)

    def test_break_triggered_long_type(self) -> None:
        self.bus.publish(
            EventType.BREAK_TRIGGERED,
            {"break_type": "long", "duration": defaults.LONG_BREAK_DURATION},
        )
        self.assertEqual(self.window.break_type, "long")
        self.assertEqual(self.window.duration, defaults.LONG_BREAK_DURATION)

    def test_break_warning_shows_popup(self) -> None:
        self.bus.publish(
            EventType.BREAK_WARNING,
            {"break_type": "short", "remaining_seconds": 30},
        )
        self.assertEqual(len(self.warning_calls), 1)
        self.assertTrue(self.warning.timer.isActive())


class TestBreakWindowI18n(unittest.TestCase):
    """i18n 遗漏修复的回归测试：语言切换与休息类型切换应反映到文案。"""

    def setUp(self) -> None:
        self.app = _get_app()
        self._saved_lang = get_language()

    def tearDown(self) -> None:
        set_language(self._saved_lang)
        self.app.processEvents()

    def test_break_window_follows_language(self) -> None:
        set_language("zh")
        win = BreakWindow(break_type="short", duration=20)
        self.assertEqual(win.postpone_button.text(), "延迟 5 分钟")
        self.assertEqual(win.skip_button.text(), "跳过")
        self.assertEqual(win._title_label.text(), "该休息一下了")

        set_language("en")
        win.configure("short", duration=20)
        self.assertEqual(win.postpone_button.text(), "Postpone 5 min")
        self.assertEqual(win.skip_button.text(), "Skip")
        self.assertEqual(win._subtitle_label.text(), "Relax for 20 seconds, look at something far away")
        win.deleteLater()

    def test_break_window_postpone_hint_i18n(self) -> None:
        set_language("en")
        win = BreakWindow(break_type="short", duration=20)
        win.set_postpone_count(0)
        self.assertIn("postpones left", win.postpone_hint.text())
        win.set_postpone_count(win.max_postpone)
        self.assertEqual(win.postpone_hint.text(), "Max postpones reached")
        win.deleteLater()

    def test_warning_popup_subtitle_follows_break_type(self) -> None:
        # 长休息警告应显示长休息副标题（回归修复 configure 重建 UI）
        popup = BreakWarningPopup(break_type="short")
        popup.configure("long")
        self.assertEqual(popup._subtitle_label.text(), "5 分钟长休息即将开始")
        popup.deleteLater()

    def test_warning_popup_follows_language(self) -> None:
        set_language("en")
        popup = BreakWarningPopup(break_type="short")
        popup.configure("long")
        self.assertEqual(popup._subtitle_label.text(), "A 5-minute break starts soon")
        self.assertEqual(popup.accept_button.text(), "Rest Now")
        popup.deleteLater()


if __name__ == "__main__":
    unittest.main()
