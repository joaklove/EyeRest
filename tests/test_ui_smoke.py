"""UI smoke test（V0.6 Sidebar 暖色版）。

验证：
* MainWindow 可创建（含 Sidebar 4 项导航 + 4 页 stack）
* Sidebar 导航点击可切换页
* SettingsPage 可加载/保存（保留所有业务接口）
* Dashboard update_display() 正常
* StatisticsPage refresh 正常
* Design tokens 完整可用
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import defaults  # noqa: E402
from app.core.event_bus import EventBus  # noqa: E402
from app.core.state_machine import StateMachine  # noqa: E402
from app.services.break_service import BreakService  # noqa: E402
from app.services.statistics_service import StatisticsService  # noqa: E402
from app.services.usage_service import UsageService  # noqa: E402
from app.config.settings_store import JsonSettingsStore  # noqa: E402
from app.services.stats_store import StatsStore  # noqa: E402
from app.ui.dashboard import Dashboard  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import tokens  # noqa: E402


def _build_window() -> MainWindow:
    """构造一个最小可用的 MainWindow（用 MagicMock 引擎，避免复杂接线）。"""
    tmp = Path(mkdtemp())
    settings_store = JsonSettingsStore(tmp / "config.json")
    stats_store = StatsStore(tmp / "stats.json")
    bus = EventBus()
    state_machine = StateMachine(event_bus=bus)
    timer = MagicMock()
    breaker = MagicMock()
    break_service = BreakService(store=settings_store, event_bus=bus)
    usage = UsageService(store=stats_store)
    statistics = StatisticsService(store=stats_store)
    return MainWindow(
        timer_engine=timer,
        break_engine=breaker,
        state_machine=state_machine,
        usage_service=usage,
        statistics_service=statistics,
        break_service=break_service,
        event_bus=bus,
    )


class TestDesignTokens(unittest.TestCase):
    def test_tokens_present(self) -> None:
        self.assertTrue(tokens.PRIMARY.startswith("#"))
        self.assertTrue(tokens.BG_CANVAS.startswith("#"))
        self.assertTrue(tokens.RADIUS_LG > 0)
        self.assertEqual(tokens.WINDOW_DEFAULT_W, 900)


class TestCueReadability(unittest.TestCase):
    """提示卡文字在卡片底色上必须可读。

    回归背景：V0.6 暖色改版（f487020）把提示卡底从深色
    ``rgba(28, 32, 44, 235)`` 改成暖白 ``rgba(255, 250, 242, 240)``，
    但 ``QLabel`` 的文字色仍留在 ``#eaf4ff``（近白）—— 近白压暖白，
    提示文案实质不可见。此处读样式表解析后的**有效**文字色做守门。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_popup_text_is_dark_on_warm_white_card(self) -> None:
        from PySide6.QtGui import QPalette

        from app.ui.visual_cue import VisualCuePopup

        popup = VisualCuePopup()
        popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        popup.show_cue(kind="blink", with_text=True)
        self.app.processEvents()

        color = popup._text.palette().color(QPalette.ColorRole.WindowText)
        luminance = (
            0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
        )
        self.assertLess(
            luminance,
            0.5,
            f"提示文字 {color.name()} 亮度过高，在暖白卡 #FFFAF2 上不可读",
        )
        popup.hide()


class TestCuePreviewMatchesPopup(unittest.TestCase):
    """位置编辑预览与真实提示卡必须**同一份样式**。

    回归背景：V0.6 暖色改版只改了真卡，编辑预览留在改版前的深色底
    ``rgba(28, 32, 44, 235)`` + 蓝边 —— 用户在编辑器里摆的位置，和平时
    弹出的样子不是同一样东西。2026-09-13 裁定：预览就用暖白 + 绿边。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_shares_card_stylesheet_with_popup(self) -> None:
        from app.ui.visual_cue import (
            CUE_CARD_OBJECT_NAME,
            VisualCuePopup,
            cue_card_stylesheet,
        )
        from app.ui.visual_cue_preview import VisualCuePreview

        popup = VisualCuePopup()
        popup.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        preview = VisualCuePreview()

        self.assertEqual(preview._card.objectName(), CUE_CARD_OBJECT_NAME)
        self.assertEqual(preview._card.styleSheet(), cue_card_stylesheet())
        self.assertEqual(
            preview._card.styleSheet(),
            popup._card.styleSheet(),
            "预览卡与真实提示卡样式不一致（不允许各写一份）",
        )

    def test_preview_text_is_dark_on_warm_white_card(self) -> None:
        from PySide6.QtGui import QPalette

        from app.ui.visual_cue_preview import VisualCuePreview

        preview = VisualCuePreview()
        preview.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        preview.show()
        self.app.processEvents()

        color = preview._text.palette().color(QPalette.ColorRole.WindowText)
        luminance = (
            0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
        )
        self.assertLess(
            luminance,
            0.5,
            f"预览文字 {color.name()} 亮度过高，在暖白卡上不可读",
        )
        preview.hide()


class TestMainWindowSidebar(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = _build_window()
        self.window.show()

    def tearDown(self) -> None:
        self.window.force_close()
        self.window.deleteLater()

    def test_window_size(self) -> None:
        self.assertEqual(self.window.width(), tokens.WINDOW_DEFAULT_W)
        self.assertEqual(self.window.height(), tokens.WINDOW_DEFAULT_H)
        self.assertEqual(self.window.minimumWidth(), tokens.WINDOW_MIN_W)
        self.assertEqual(self.window.minimumHeight(), tokens.WINDOW_MIN_H)

    def test_sidebar_has_four_items(self) -> None:
        self.assertEqual(len(self.window._nav_buttons), 4)

    def test_default_active_is_dashboard(self) -> None:
        self.assertTrue(self.window._nav_buttons[0].isChecked())
        self.assertEqual(self.window._stack.currentIndex(), 0)

    def test_page_switch_via_nav(self) -> None:
        for idx in range(4):
            self.window._nav_buttons[idx].click()
            QApplication.processEvents()
            self.assertEqual(self.window._stack.currentIndex(),
                             self.window._nav_group.id(self.window._nav_buttons[idx]))
            self.assertTrue(self.window._nav_buttons[idx].isChecked())

    def test_status_badge_visible(self) -> None:
        self.assertTrue(self.window._status_badge.isVisibleTo(self.window))

    def test_about_page_present(self) -> None:
        self.window._switch_page(3)
        QApplication.processEvents()
        self.assertEqual(self.window._stack.currentIndex(), 3)


class TestDashboardV06(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        tmp = Path(mkdtemp())
        self.store = JsonSettingsStore(tmp / "config.json")
        self.stats = StatsStore(tmp / "stats.json")
        self.bus = EventBus()
        self.sm = StateMachine(event_bus=self.bus)
        self.timer = MagicMock()
        self.usage = UsageService(store=self.stats)
        self.dashboard = Dashboard(
            timer_engine=self.timer,
            break_engine=None,
            state_machine=self.sm,
            usage_service=self.usage,
            event_bus=self.bus,
            parent=None,
        )

    def tearDown(self) -> None:
        self.dashboard.cleanup()
        self.dashboard.deleteLater()

    def test_update_display_runs(self) -> None:
        self.dashboard.update_display()
        text = self.dashboard._active_time_label.text()
        self.assertTrue(text)

    def test_signals_exist(self) -> None:
        self.assertTrue(hasattr(self.dashboard, "quick_break_requested"))
        self.assertTrue(hasattr(self.dashboard, "pause_30m_requested"))
        self.assertTrue(hasattr(self.dashboard, "reset_requested"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
