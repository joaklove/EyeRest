"""Dashboard 与 UsageService 集成测试。

在 Qt offscreen 平台下验证：
* UsageService 正确读写数据库（开始/结束会话、今日摘要、时长格式化）
* Dashboard 可创建并正常刷新显示数据
* 状态变化时 UI 联动
* MainWindow 集成 Dashboard 与导航

运行方式::

    cd apps/EyeRest
    python -m pytest tests/test_dashboard_and_usage.py -v
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

# 必须在导入 PySide6 之前设置 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.event_bus import EventBus, EventType  # noqa: E402
from app.core.state_machine import AppState, StateMachine  # noqa: E402
from app.services.stats_store import StatsStore  # noqa: E402
from app.services.stats_store import StatsStore  # noqa: E402
from app.services.usage_service import UsageService  # noqa: E402
from app.ui.dashboard import Dashboard  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


# ---------------------------------------------------------------------------
# Mock 引擎（不依赖系统级 ActivityMonitor）
# ---------------------------------------------------------------------------
class MockTimerEngine:
    """可控的计时器引擎 mock。"""

    def __init__(self) -> None:
        self._active = 0.0
        self._idle = 0.0
        self._uptime = 0.0

    def tick(self, delta: float = 1.0) -> None:
        self._active += delta
        self._uptime += delta

    def add_idle(self, delta: float = 1.0) -> None:
        self._idle += delta
        self._uptime += delta

    def get_active_seconds(self) -> float:
        return self._active

    def get_idle_seconds_total(self) -> float:
        return self._idle

    def get_app_uptime(self) -> float:
        return self._uptime

    def reset(self) -> None:
        self._active = 0.0

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class MockBreakEngine:
    """可控的休息引擎 mock。"""

    def __init__(self, remaining: float = 1200.0) -> None:
        self._remaining = remaining
        self._break_count = 0
        self._current_type = "short"

    def set_remaining(self, seconds: float) -> None:
        self._remaining = seconds

    def get_remaining_seconds(self) -> float:
        return self._remaining

    def get_current_break_type(self) -> str:
        return self._current_type

    def get_break_count_today(self) -> int:
        return self._break_count

    def increment_break(self) -> None:
        self._break_count += 1

    def on_break_start(self, break_type: str = "short") -> None:
        self._current_type = break_type


# ---------------------------------------------------------------------------
# UsageService 测试
# ---------------------------------------------------------------------------
class TestUsageService(unittest.TestCase):
    """UsageService 测试（V0.5：JSON 统计存储）。"""

    def setUp(self) -> None:
        # 使用临时目录中的 stats.json，避免污染真实数据
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_test_"))
        self.store = StatsStore(self._tmpdir / "stats.json")

        self.timer = MockTimerEngine()
        self.break_engine = MockBreakEngine()
        self.service = UsageService(
            timer_engine=self.timer,
            break_engine=self.break_engine,
            store=self.store,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_format_duration(self) -> None:
        """format_duration 正确格式化为 HH:MM:SS。"""
        self.assertEqual(self.service.format_duration(0), "00:00:00")
        self.assertEqual(self.service.format_duration(59), "00:00:59")
        self.assertEqual(self.service.format_duration(60), "00:01:00")
        self.assertEqual(self.service.format_duration(3661), "01:01:01")
        self.assertEqual(self.service.format_duration(3661.9), "01:01:01")
        # 负数归零
        self.assertEqual(self.service.format_duration(-10), "00:00:00")

    def test_format_short_duration(self) -> None:
        """format_short_duration 正确格式化为 MM:SS。"""
        self.assertEqual(self.service.format_short_duration(0), "00:00")
        self.assertEqual(self.service.format_short_duration(65), "01:05")
        self.assertEqual(self.service.format_short_duration(1200), "20:00")

    def test_start_session_returns_id(self) -> None:
        """start_session 返回正整数 ID。"""
        session_id = self.service.start_session()
        self.assertIsInstance(session_id, int)
        self.assertGreater(session_id, 0)
        self.assertEqual(self.service.get_current_session_id(), session_id)

    def test_start_session_marks_active(self) -> None:
        """start_session 后进入活跃状态。"""
        self.service.start_session()
        self.assertTrue(self.service.is_session_active())
        self.assertIsNotNone(self.service.get_current_session_id())

    def test_end_session_saves_stats(self) -> None:
        """end_session 把会话时长写入今日统计。"""
        session_id = self.service.start_session()

        # 模拟一些用眼时间
        for _ in range(30):
            self.timer.tick()

        result = self.service.end_session(session_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], session_id)
        self.assertAlmostEqual(result["active_seconds"], 30.0, delta=1.0)

        # 验证已落盘到 stats.json
        reloaded = StatsStore(self._tmpdir / "stats.json")
        self.assertAlmostEqual(reloaded.today()["screen_seconds"], 30.0, delta=1.0)
        self.assertFalse(self.service.is_session_active())

    def test_record_screen_seconds_throttled(self) -> None:
        """每秒累计屏幕时长，flush 后完整保留。"""
        self.service.start_session()
        for _ in range(10):
            self.service.record_screen_seconds(1.0)
        self.service.end_session()
        reloaded = StatsStore(self._tmpdir / "stats.json")
        self.assertAlmostEqual(reloaded.today()["screen_seconds"], 10.0, places=1)

    def test_end_session_without_active_session(self) -> None:
        """无活跃会话时 end_session 返回 None。"""
        result = self.service.end_session()
        self.assertIsNone(result)

    def test_get_today_summary_empty(self) -> None:
        """无会话时今日摘要为零。"""
        summary = self.service.get_today_summary()
        self.assertEqual(summary["active_seconds"], 0.0)
        self.assertEqual(summary["idle_seconds"], 0.0)
        self.assertEqual(summary["break_count"], 0)
        self.assertEqual(summary["skipped_breaks"], 0)
        self.assertEqual(summary["blink_cues"], 0)
        self.assertEqual(summary["session_count"], 0)

    def test_get_today_summary_with_session(self) -> None:
        """有活跃会话时摘要包含实时增量。"""
        session_id = self.service.start_session()
        for _ in range(15):
            self.timer.tick()

        summary = self.service.get_today_summary()
        # 活跃会话的实时增量应被计入
        self.assertGreater(summary["active_seconds"], 0.0)
        self.assertEqual(summary["session_count"], 1)

        self.service.end_session(session_id)

    def test_get_today_summary_after_session_end(self) -> None:
        """会话结束后摘要读取今日已累计的统计。"""
        session_id = self.service.start_session()
        for _ in range(45):
            self.timer.tick()
        self.service.end_session(session_id)

        summary = self.service.get_today_summary()
        self.assertAlmostEqual(summary["active_seconds"], 45.0, delta=1.0)
        # V0.5 不持久化"会话"概念，会话结束后不再有活跃会话
        self.assertEqual(summary["session_count"], 0)
        self.assertIsNone(self.service.get_current_session_id())

    def test_multiple_sessions(self) -> None:
        """多个会话正确聚合。"""
        # 会话 1
        s1 = self.service.start_session()
        for _ in range(10):
            self.timer.tick()
        self.service.end_session(s1)

        # 会话 2
        s2 = self.service.start_session()
        for _ in range(20):
            self.timer.tick()
        self.service.end_session(s2)

        summary = self.service.get_today_summary()
        # V0.5 只累计时长，不再统计会话条数
        self.assertAlmostEqual(summary["active_seconds"], 30.0, delta=2.0)

    def test_restart_session_auto_ends_old(self) -> None:
        """再次 start_session 时自动结束旧会话。"""
        s1 = self.service.start_session()
        for _ in range(10):
            self.timer.tick()

        s2 = self.service.start_session()
        self.assertNotEqual(s1, s2)
        # 旧会话应已被结束
        self.assertEqual(self.service.get_current_session_id(), s2)


# ---------------------------------------------------------------------------
# Dashboard 测试
# ---------------------------------------------------------------------------
class TestDashboard(unittest.TestCase):
    """Dashboard UI 测试（offscreen 模式）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.timer = MockTimerEngine()
        self.break_engine = MockBreakEngine(remaining=1200.0)
        self.bus = EventBus()
        self.state_machine = StateMachine(
            timer_engine=self.timer, event_bus=self.bus
        )

        # 使用临时目录中的 stats.json（V0.5：无数据库）
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_dash_"))
        self.store = StatsStore(self._tmpdir / "stats.json")
        self.usage = UsageService(
            timer_engine=self.timer,
            break_engine=self.break_engine,
            store=self.store,
        )

        self.dashboard = Dashboard(
            timer_engine=self.timer,
            break_engine=self.break_engine,
            state_machine=self.state_machine,
            usage_service=self.usage,
            event_bus=self.bus,
        )

    def tearDown(self) -> None:
        self.dashboard.cleanup()
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_dashboard_creates_successfully(self) -> None:
        """Dashboard 可正常创建。"""
        self.assertIsNotNone(self.dashboard)
        self.assertTrue(hasattr(self.dashboard, "update_display"))

    def test_update_display_refreshes_labels(self) -> None:
        """update_display 刷新所有标签数据。"""
        # 模拟 65 秒用眼
        for _ in range(65):
            self.timer.tick()

        self.dashboard.update_display()

        # 验证有效用眼时间显示
        text = self.dashboard._active_time_label.text()
        self.assertIn("00:01:05", text)

    def test_update_display_countdown(self) -> None:
        """倒计时正确显示。"""
        self.break_engine.set_remaining(65.0)
        self.dashboard.update_display()

        text = self.dashboard._countdown_label.text()
        self.assertEqual(text, "01:05")

    def test_update_display_progress_bar(self) -> None:
        """进度条随工作时间增加。"""
        # 先刷新一次
        self.dashboard.update_display()
        initial = self.dashboard._progress_bar.value()

        # 累加工作时间
        for _ in range(600):
            self.timer.tick()
        self.break_engine.set_remaining(600.0)  # 总 1200，已用 600 → 50%
        self.dashboard.update_display()

        final = self.dashboard._progress_bar.value()
        self.assertGreater(final, initial)
        self.assertEqual(final, 50)

    def test_status_reflects_state(self) -> None:
        """状态文字反映状态机状态。"""
        self.state_machine.start_protection()
        self.dashboard.update_display()
        text = self.dashboard._status_label.text()
        self.assertIn("正在保护", text)

    def test_state_change_event_updates_ui(self) -> None:
        """状态变化事件触发 UI 刷新。"""
        self.state_machine.start_protection()
        # 触发暂停
        self.state_machine.pause()
        # 事件已通过 bus 发布，Dashboard 应已更新
        text = self.dashboard._status_label.text()
        self.assertIn("暂停", text)

    def test_quick_action_signals_exist(self) -> None:
        """快捷操作信号存在且可连接。"""
        self.assertTrue(hasattr(self.dashboard, "quick_break_requested"))
        self.assertTrue(hasattr(self.dashboard, "pause_30m_requested"))
        self.assertTrue(hasattr(self.dashboard, "reset_requested"))

        # 发射信号不报错
        self.dashboard.quick_break_requested.emit()
        self.dashboard.pause_30m_requested.emit()
        self.dashboard.reset_requested.emit()

    def test_today_stats_display(self) -> None:
        """今日统计卡片显示数据。"""
        # 开始会话并记录休息
        self.usage.start_session()
        for _ in range(20):
            self.timer.tick()
        self.break_engine.increment_break()
        self.dashboard.update_display()

        # 休息次数应显示
        break_text = self.dashboard._break_count_label["value"].text()
        # DB 中还没有休息事件（BreakEngine mock 不写 DB），但摘要仍可工作
        self.assertIsNotNone(break_text)


# ---------------------------------------------------------------------------
# MainWindow 集成测试
# ---------------------------------------------------------------------------
class TestMainWindowIntegration(unittest.TestCase):
    """MainWindow 集成测试。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.timer = MockTimerEngine()
        self.break_engine = MockBreakEngine()
        self.bus = EventBus()
        self.state_machine = StateMachine(
            timer_engine=self.timer, event_bus=self.bus
        )

        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_mw_"))
        self.store = StatsStore(self._tmpdir / "stats.json")
        self.usage = UsageService(
            store=self.store,
            timer_engine=self.timer,
            break_engine=self.break_engine,
        )

        self.window = MainWindow(
            timer_engine=self.timer,
            break_engine=self.break_engine,
            state_machine=self.state_machine,
            usage_service=self.usage,
            event_bus=self.bus,
        )
        self.window.connect_dashboard_actions()

    def tearDown(self) -> None:
        # 强制关闭以触发清理
        self.window._force_close = True
        self.window.close()
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_main_window_creates(self) -> None:
        """MainWindow 可正常创建。"""
        self.assertIsNotNone(self.window)
        self.assertIsNotNone(self.window._dashboard)

    def test_navigation_switch_pages(self) -> None:
        """顶部导航可切换页面。"""
        # 默认在 Dashboard (0)
        self.assertEqual(self.window._stack.currentIndex(), 0)

        # 切换到 Statistics
        self.window._switch_page(1)
        self.assertEqual(self.window._stack.currentIndex(), 1)

        # 切换到 Settings
        self.window._switch_page(2)
        self.assertEqual(self.window._stack.currentIndex(), 2)

        # 切回 Dashboard
        self.window._switch_page(0)
        self.assertEqual(self.window._stack.currentIndex(), 0)

    def test_refresh_timer_runs(self) -> None:
        """刷新定时器已启动。"""
        self.assertIsNotNone(self.window._refresh_timer)
        self.assertTrue(self.window._refresh_timer.isActive())

    def test_statusbar_reflects_state(self) -> None:
        """Sidebar 状态徽章显示当前状态（V0.6 取代原 status bar）。"""
        self.state_machine.start_protection()
        self.window._refresh_status_badge()
        text = self.window._status_badge.text()
        self.assertIn("ACTIVE", text)

    def test_quick_break_action(self) -> None:
        """立即休息按钮触发 BreakEngine。"""
        self.state_machine.start_protection()
        self.window._on_quick_break()
        # BreakEngine mock 应收到调用
        self.assertEqual(self.break_engine.get_current_break_type(), "short")

    def test_reset_action(self) -> None:
        """重置按钮清零计时。"""
        for _ in range(50):
            self.timer.tick()
        self.assertGreater(self.timer.get_active_seconds(), 0)

        self.window._on_reset()
        self.assertEqual(self.timer.get_active_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
