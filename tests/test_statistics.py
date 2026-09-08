"""Statistics 统计服务与统计页单元测试（V0.5：JSON 统计存储）。

覆盖：
    - StatisticsService 的日/周/月统计读取
    - 趋势数据生成
    - StatisticsPage 在 offscreen 模式下的创建与数据刷新
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# 设置 offscreen 模式，避免测试弹出窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.statistics_service import StatisticsService  # noqa: E402
from app.services.stats_store import StatsStore  # noqa: E402
from app.ui.statistics import BarChartWidget, StatisticsPage  # noqa: E402


class TestStatisticsService(unittest.TestCase):
    """StatisticsService 单元测试（JSON 版）。"""

    def setUp(self) -> None:
        """每个测试创建独立的临时统计文件。"""
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_stats_"))
        self.store = StatsStore(self._tmpdir / "stats.json")
        self.service = StatisticsService(store=self.store)

        now_local = datetime.now()
        self.today_str = now_local.date().isoformat()
        self.yesterday_str = (now_local - timedelta(days=1)).date().isoformat()

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 测试数据辅助
    # ------------------------------------------------------------------
    def _seed_day(
        self,
        day_str: str,
        screen_seconds: float = 0.0,
        blink_cue: int = 0,
        look_away: int = 0,
        move: int = 0,
        deep_break: int = 0,
        skipped: int = 0,
    ) -> None:
        """直接向统计存储写入某一天的计数（等价于当天发生的提醒）。"""
        self.store._days[day_str] = {
            "screen_seconds": float(screen_seconds),
            "blink_cue": blink_cue,
            "look_away": look_away,
            "move": move,
            "deep_break": deep_break,
            "skipped_break": skipped,
        }
        self.store.flush()

    # ------------------------------------------------------------------
    # 单日统计
    # ------------------------------------------------------------------
    def test_get_daily_stats_empty(self) -> None:
        """无数据返回全零统计。"""
        stats = self.service.get_daily_stats(self.today_str)
        self.assertEqual(stats["date"], self.today_str)
        self.assertEqual(stats["total_active_seconds"], 0.0)
        self.assertEqual(stats["total_breaks"], 0)
        self.assertEqual(stats["total_skipped_breaks"], 0)

    def test_get_daily_stats_with_data(self) -> None:
        """有数据时正确映射为旧字段结构。"""
        self._seed_day(
            self.today_str,
            screen_seconds=1800.0,
            blink_cue=72,
            look_away=2,
            move=1,
            deep_break=1,
            skipped=1,
        )

        stats = self.service.get_daily_stats(self.today_str)

        self.assertEqual(stats["total_active_seconds"], 1800.0)
        # 远眺 2 + 活动 1 + 深度 1
        self.assertEqual(stats["total_breaks"], 4)
        self.assertEqual(stats["total_short_breaks"], 2)
        self.assertEqual(stats["total_long_breaks"], 1)
        self.assertEqual(stats["total_skipped_breaks"], 1)
        self.assertEqual(stats["blink_cues"], 72)
        self.assertEqual(stats["move_count"], 1)

    def test_get_daily_stats_default_today(self) -> None:
        """不传 date_str 时默认查询今天。"""
        self._seed_day(self.today_str, screen_seconds=100.0)
        stats = self.service.get_daily_stats()
        self.assertEqual(stats["date"], self.today_str)
        self.assertEqual(stats["total_active_seconds"], 100.0)

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------
    def test_aggregate_daily_stat_returns_current(self) -> None:
        """JSON 版数据在触发时已累计，聚合即读取。"""
        self._seed_day(self.today_str, screen_seconds=500.0, look_away=1, skipped=1)

        result = self.service.aggregate_daily_stat(self.today_str)

        self.assertEqual(result["total_active_seconds"], 500.0)
        self.assertEqual(result["total_skipped_breaks"], 1)
        self.assertEqual(result["total_short_breaks"], 1)

    # ------------------------------------------------------------------
    # 周/月统计
    # ------------------------------------------------------------------
    def test_get_weekly_stats_returns_7_days(self) -> None:
        """get_weekly_stats 返回 7 天数据。"""
        weekly = self.service.get_weekly_stats()
        self.assertEqual(len(weekly), 7)
        for i in range(1, len(weekly)):
            self.assertGreaterEqual(weekly[i]["date"], weekly[i - 1]["date"])

    def test_get_monthly_stats_returns_30_days(self) -> None:
        """get_monthly_stats 返回 30 天数据。"""
        self.assertEqual(len(self.service.get_monthly_stats()), 30)

    def test_weekly_stats_includes_data(self) -> None:
        """周统计包含今天与昨天的数据。"""
        self._seed_day(self.today_str, screen_seconds=3600.0)
        self._seed_day(self.yesterday_str, screen_seconds=1800.0)

        weekly = self.service.get_weekly_stats()
        today_stat = next(s for s in weekly if s["date"] == self.today_str)
        yesterday_stat = next(s for s in weekly if s["date"] == self.yesterday_str)

        self.assertEqual(today_stat["total_active_seconds"], 3600.0)
        self.assertEqual(yesterday_stat["total_active_seconds"], 1800.0)

    # ------------------------------------------------------------------
    # 趋势数据
    # ------------------------------------------------------------------
    def test_get_usage_trend(self) -> None:
        """用眼趋势数据结构正确。"""
        self._seed_day(self.today_str, screen_seconds=7200.0)
        trend = self.service.get_usage_trend(days=7)

        self.assertIn("dates", trend)
        self.assertIn("active_seconds", trend)
        self.assertEqual(len(trend["dates"]), 7)

        today_idx = trend["dates"].index(
            datetime.strptime(self.today_str, "%Y-%m-%d").strftime("%m-%d")
        )
        self.assertEqual(trend["active_seconds"][today_idx], 7200.0)

    def test_get_break_trend(self) -> None:
        """休息趋势数据结构正确。"""
        self._seed_day(self.today_str, look_away=2, deep_break=1, skipped=1)
        trend = self.service.get_break_trend(days=7)

        self.assertIn("short_breaks", trend)
        self.assertIn("long_breaks", trend)
        self.assertIn("skipped", trend)

        today_idx = trend["dates"].index(
            datetime.strptime(self.today_str, "%Y-%m-%d").strftime("%m-%d")
        )
        self.assertEqual(trend["short_breaks"][today_idx], 2)
        self.assertEqual(trend["long_breaks"][today_idx], 1)
        self.assertEqual(trend["skipped"][today_idx], 1)

    # ------------------------------------------------------------------
    # 统计存储本身
    # ------------------------------------------------------------------
    def test_record_and_flush(self) -> None:
        """record 累加计数，flush 后落盘且可重新读出。"""
        self.store.record("blink_cue")
        self.store.record("blink_cue")
        self.store.record("look_away")
        self.store.flush()

        reloaded = StatsStore(self._tmpdir / "stats.json")
        today = reloaded.today()
        self.assertEqual(today["blink_cue"], 2)
        self.assertEqual(today["look_away"], 1)

    def test_screen_seconds_throttled_write(self) -> None:
        """屏幕秒数按阈值节流落盘，flush 后完整保留。"""
        for _ in range(10):
            self.store.add_screen_seconds(1.0)
        self.store.flush()
        reloaded = StatsStore(self._tmpdir / "stats.json")
        self.assertAlmostEqual(reloaded.today()["screen_seconds"], 10.0, places=1)

    def test_unknown_kind_ignored(self) -> None:
        """未知统计类型不应污染数据。"""
        self.store.record("not_a_kind")
        self.assertEqual(self.store.today()["blink_cue"], 0)


class TestBarChartWidget(unittest.TestCase):
    """BarChartWidget 单元测试。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_create_chart(self) -> None:
        """图表组件能正常创建。"""
        chart = BarChartWidget(
            data=[[1.0, 2.0, 3.0], [0.5, 1.0, 1.5]],
            labels=["Mon", "Tue", "Wed"],
            colors=["#4CAF50", "#9E9E9E"],
            y_label="hours",
            legend_labels=["A", "B"],
        )
        self.assertEqual(len(chart._data), 2)
        self.assertEqual(len(chart._labels), 3)

    def test_set_data_updates(self) -> None:
        """set_data 更新数据。"""
        chart = BarChartWidget(
            data=[[0.0, 0.0]],
            labels=["X"],
            colors=["#000"],
        )
        chart.set_data(
            data=[[10.0, 20.0]],
            labels=["Y", "Z"],
            colors=["#fff"],
            legend_labels=["Test"],
        )
        self.assertEqual(chart._data, [[10.0, 20.0]])
        self.assertEqual(chart._labels, ["Y", "Z"])
        self.assertEqual(chart._legend_labels, ["Test"])

    def test_paint_event_no_crash(self) -> None:
        """paintEvent 不崩溃（offscreen 模式）。"""
        chart = BarChartWidget(
            data=[[1.0, 2.0, 3.0], [0.5, 1.0, 1.5]],
            labels=["Mon", "Tue", "Wed"],
            colors=["#4CAF50", "#9E9E9E"],
            y_label="hours",
        )
        chart.resize(400, 220)
        chart.show()
        self.app.processEvents()
        # 不抛异常即通过

    def test_empty_data_no_crash(self) -> None:
        """空数据不崩溃。"""
        chart = BarChartWidget(
            data=[],
            labels=[],
            colors=[],
        )
        chart.resize(400, 220)
        chart.show()
        self.app.processEvents()

    def test_all_zero_data(self) -> None:
        """全零数据不崩溃。"""
        chart = BarChartWidget(
            data=[[0.0, 0.0], [0.0, 0.0]],
            labels=["A", "B"],
            colors=["#000", "#fff"],
        )
        chart.resize(400, 220)
        chart.show()
        self.app.processEvents()


class TestStatisticsPage(unittest.TestCase):
    """StatisticsPage 单元测试（offscreen 模式）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_page_"))
        self.store = StatsStore(self._tmpdir / "stats.json")
        self.service = StatisticsService(store=self.store)
        self.page = None

    def tearDown(self) -> None:
        try:
            if self.page is not None:
                self.page.close()
        except Exception:
            pass
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_page_creation(self) -> None:
        """页面能正常创建且包含必要组件。"""
        self.page = StatisticsPage(self.service)
        self.page.show()
        self.app.processEvents()

        # 验证关键组件存在
        self.assertTrue(hasattr(self.page, "_refresh_btn"))
        self.assertTrue(hasattr(self.page, "_usage_chart"))
        self.assertTrue(hasattr(self.page, "_break_chart"))
        self.assertTrue(hasattr(self.page, "_table"))
        self.assertTrue(hasattr(self.page, "_card_values"))
        self.assertEqual(len(self.page._card_values), 4)

    def test_page_refresh(self) -> None:
        """刷新按钮能正常工作。"""
        self.page = StatisticsPage(self.service)
        self.page.show()
        self.app.processEvents()

        # 初始状态（无数据）
        initial_stats = self.page.get_today_stats()
        self.assertEqual(initial_stats["total_active_seconds"], 0.0)

        # 写入今日统计（屏幕 1 小时 + 远眺 2 / 活动 1 / 跳过 1）
        self.store.record("look_away", 2)
        self.store.record("move", 1)
        self.store.record("skipped_break", 1)
        self.store.add_screen_seconds(3600.0)
        self.store.flush()

        # 点击刷新按钮
        self.page._refresh_btn.click()
        self.app.processEvents()

        refreshed = self.page.get_today_stats()
        self.assertEqual(refreshed["total_active_seconds"], 3600.0)
        self.assertEqual(refreshed["total_breaks"], 3)
        self.assertEqual(refreshed["total_skipped_breaks"], 1)

        # 验证表格有 7 行
        self.assertEqual(self.page._table.rowCount(), 7)
        self.assertEqual(self.page._table.columnCount(), 6)

    def test_page_refresh_button_text(self) -> None:
        """刷新后按钮文本更新。"""
        self.page = StatisticsPage(self.service)
        self.page.show()
        self.app.processEvents()

        # 构造时已自动刷新一次
        text = self.page._refresh_btn.text()
        self.assertIn("已刷新", text)

    def test_table_headers(self) -> None:
        """表头正确。"""
        self.page = StatisticsPage(self.service)
        self.page.show()
        self.app.processEvents()

        expected_headers = ["日期", "有效用眼", "空闲", "短休息", "长休息", "跳过"]
        headers = [
            self.page._table.horizontalHeaderItem(i).text()
            for i in range(self.page._table.columnCount())
        ]
        self.assertEqual(headers, expected_headers)

    def test_charts_have_data(self) -> None:
        """趋势图在刷新后有数据。"""
        self.page = StatisticsPage(self.service)
        self.page.show()
        self.app.processEvents()

        # 写入今天的数据（屏幕 2 小时 + 4 次远眺 + 2 次跳过）
        self.store.record("look_away", 4)
        self.store.record("skipped_break", 2)
        self.store.add_screen_seconds(7200.0)
        self.store.flush()

        self.page._refresh_btn.click()
        self.app.processEvents()

        # 用眼趋势图数据
        self.assertEqual(len(self.page._usage_chart._data[0]), 7)
        # 休息趋势图数据
        self.assertEqual(len(self.page._break_chart._data[0]), 7)


if __name__ == "__main__":
    unittest.main()
