"""SettingsPage UI 测试。

使用 offscreen 平台运行，无需真实显示设备。
覆盖页面创建、设置加载/保存、信号发射、变更标记与重置。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 必须在导入 PySide6 之前设置 offscreen 平台
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.config.settings_store import JsonSettingsStore  # noqa: E402
from app.core.event_bus import EventBus, EventType  # noqa: E402
from app.services.break_service import BreakService  # noqa: E402
from app.ui.settings import SettingsPage  # noqa: E402


class TestSettingsPage(unittest.TestCase):
    """SettingsPage UI 测试（offscreen 模式）。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 确保只有一个 QApplication 实例
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_settings_"))
        self.store = JsonSettingsStore(self._tmpdir / "config.json")
        self.bus = EventBus()
        self.service = BreakService(store=self.store, event_bus=self.bus)

        # 避免测试真的改动注册表 / 弹出模态确认框
        self._startup_patcher = mock.patch(
            "app.windows.startup.StartupManager.toggle", return_value=True
        )
        self._startup_patcher.start()
        self._confirm_patcher = mock.patch(
            "app.ui.settings.QMessageBox.question",
            return_value=QMessageBox.StandardButton.RestoreDefaults,
        )
        self._confirm_patcher.start()

        self.bus_events: list[tuple[EventType, object]] = []
        self.bus.subscribe(EventType.SETTINGS_CHANGED, self._record_bus)

        self.page_signals: list[dict] = []
        self.page = SettingsPage(break_service=self.service)
        self.page.settings_changed.connect(self.page_signals.append)

    def tearDown(self) -> None:
        self._confirm_patcher.stop()
        self._startup_patcher.stop()
        self.page.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _record_bus(self, data: object) -> None:
        self.bus_events.append((EventType.SETTINGS_CHANGED, data))

    # ------------------------------------------------------------------
    # 页面创建
    # ------------------------------------------------------------------
    def test_page_creates_with_all_controls(self) -> None:
        """页面应创建所有配置控件。"""
        # 13 个 SpinBox 字段（含 V1.0 Blink Cycle / Move）
        self.assertEqual(len(self.page._spinboxes), 13)
        expected_spin_keys = {
            "short_work_duration", "short_break_duration",
            "long_work_duration", "long_break_duration", "warning_duration",
            "idle_threshold", "natural_rest_threshold",
            "postpone_duration", "max_postpone",
            "blink_cycle_seconds", "blink_cue_interval",
            "move_interval", "move_duration",
        }
        self.assertEqual(set(self.page._spinboxes.keys()), expected_spin_keys)

        # 9 个 CheckBox 字段（四层节奏开关 + 通知 + 系统行为）
        self.assertEqual(len(self.page._checkboxes), 9)
        expected_check_keys = {
            "blink_enabled", "look_away_enabled", "move_enabled",
            "deep_break_enabled", "enable_sound", "enable_notification",
            "auto_start", "minimize_to_tray", "defer_on_fullscreen",
        }
        self.assertEqual(set(self.page._checkboxes.keys()), expected_check_keys)

        # 4 个 ComboBox 字段（视觉皮肤 / 位置 / 强度 / 音效方案）
        self.assertEqual(len(self.page._combos), 4)
        self.assertEqual(
            set(self.page._combos.keys()),
            {"cue_skin", "cue_intensity", "cue_position", "sound_scheme"},
        )

    def test_title_shows_settings(self) -> None:
        """初始标题为「设置」，无未保存标记。"""
        self.assertFalse(self.page.is_modified())
        self.assertEqual(self.page._title_label.text(), "设置")

    # ------------------------------------------------------------------
    # 加载设置
    # ------------------------------------------------------------------
    def test_load_settings_populates_ui(self) -> None:
        """加载默认设置后控件值正确。"""
        # short_work_duration 默认 1200 秒 = 20 分钟
        self.assertEqual(self.page._spinboxes["short_work_duration"].value(), 20)
        # short_break_duration 默认 20 秒
        self.assertEqual(self.page._spinboxes["short_break_duration"].value(), 20)
        # long_work_duration 默认 5400 秒 = 90 分钟
        self.assertEqual(self.page._spinboxes["long_work_duration"].value(), 90)
        # long_break_duration 默认 300 秒 = 5 分钟
        self.assertEqual(self.page._spinboxes["long_break_duration"].value(), 5)
        # warning_duration 默认 30 秒
        self.assertEqual(self.page._spinboxes["warning_duration"].value(), 30)
        # idle_threshold 默认 60 秒
        self.assertEqual(self.page._spinboxes["idle_threshold"].value(), 60)
        # natural_rest_threshold 默认 300 秒（V1.0：避免看 PDF/视频被误判为休息）
        self.assertEqual(self.page._spinboxes["natural_rest_threshold"].value(), 300)
        # postpone_duration 默认 300 秒 = 5 分钟
        self.assertEqual(self.page._spinboxes["postpone_duration"].value(), 5)
        # max_postpone 默认 2
        self.assertEqual(self.page._spinboxes["max_postpone"].value(), 2)

        # CheckBox 默认值
        self.assertFalse(self.page._checkboxes["enable_sound"].isChecked())
        self.assertTrue(self.page._checkboxes["enable_notification"].isChecked())
        self.assertFalse(self.page._checkboxes["auto_start"].isChecked())
        self.assertTrue(self.page._checkboxes["minimize_to_tray"].isChecked())
        # defer_on_fullscreen 默认开启（与 defaults.DEFER_ON_FULLSCREEN 一致）
        self.assertTrue(self.page._checkboxes["defer_on_fullscreen"].isChecked())

    def test_load_settings_from_store(self) -> None:
        """已保存的自定义设置应加载到 UI。"""
        self.service.set_setting("short_work_duration", 1800)  # 30 分钟
        self.service.set_setting("enable_sound", True)

        self.page.load_settings()

        self.assertEqual(self.page._spinboxes["short_work_duration"].value(), 30)
        self.assertTrue(self.page._checkboxes["enable_sound"].isChecked())

    # ------------------------------------------------------------------
    # 保存设置
    # ------------------------------------------------------------------
    def test_save_settings_persists_to_service(self) -> None:
        """UI 修改后保存应写入服务（config.json）。"""
        # 修改短工作时长为 25 分钟
        self.page._spinboxes["short_work_duration"].setValue(25)
        # 启用提示音
        self.page._checkboxes["enable_sound"].setChecked(True)

        changed = self.page.save_settings()

        self.assertIn("short_work_duration", changed)
        self.assertEqual(changed["short_work_duration"], 1500)  # 25 * 60
        self.assertIn("enable_sound", changed)
        self.assertTrue(changed["enable_sound"])

        # 验证持久化
        self.assertEqual(self.service.get_setting("short_work_duration"), 1500)
        self.assertTrue(self.service.get_setting("enable_sound"))

    def test_save_defer_on_fullscreen_persists(self) -> None:
        """关闭「全屏时延迟提醒」后保存应写入服务/数据库。"""
        self.page._checkboxes["defer_on_fullscreen"].setChecked(False)

        changed = self.page.save_settings()

        self.assertIn("defer_on_fullscreen", changed)
        self.assertFalse(changed["defer_on_fullscreen"])
        self.assertFalse(self.service.get_setting("defer_on_fullscreen"))

    def test_save_settings_minute_to_seconds_conversion(self) -> None:
        """分钟单位字段应正确换算为秒存储。"""
        self.page._spinboxes["short_work_duration"].setValue(45)
        self.page._spinboxes["long_break_duration"].setValue(10)
        self.page._spinboxes["postpone_duration"].setValue(15)

        self.page.save_settings()

        self.assertEqual(self.service.get_setting("short_work_duration"), 2700)
        self.assertEqual(self.service.get_setting("long_break_duration"), 600)
        self.assertEqual(self.service.get_setting("postpone_duration"), 900)

    def test_save_settings_reloads_after_restart(self) -> None:
        """重启应用（新建 service + page）后设置应恢复。"""
        self.page._spinboxes["short_work_duration"].setValue(40)
        self.page._checkboxes["auto_start"].setChecked(True)
        self.page.save_settings()

        # 模拟重启：新建 service 和 page（重新读盘）
        new_service = BreakService(store=JsonSettingsStore(self._tmpdir / "config.json"))
        new_page = SettingsPage(break_service=new_service)

        self.assertEqual(new_page._spinboxes["short_work_duration"].value(), 40)
        self.assertTrue(new_page._checkboxes["auto_start"].isChecked())
        new_page.close()

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------
    def test_save_emits_page_signal(self) -> None:
        """保存后应发射 settings_changed 信号。"""
        self.page._spinboxes["short_break_duration"].setValue(25)
        self.page_signals.clear()

        self.page.save_settings()

        self.assertEqual(len(self.page_signals), 1)
        self.assertEqual(self.page_signals[0]["short_break_duration"], 25)

    def test_save_publishes_bus_event(self) -> None:
        """保存后应通过 EventBus 发布 SETTINGS_CHANGED。"""
        self.page._spinboxes["warning_duration"].setValue(15)
        self.bus_events.clear()

        self.page.save_settings()

        # EventBus 应收到事件
        self.assertTrue(
            any(et == EventType.SETTINGS_CHANGED for et, _ in self.bus_events)
        )
        _, data = self.bus_events[-1]
        self.assertIn("settings", data)
        self.assertEqual(data["settings"]["warning_duration"], 15)

    # ------------------------------------------------------------------
    # 变更标记
    # ------------------------------------------------------------------
    def test_modified_mark_on_change(self) -> None:
        """修改控件后标题应显示 *。"""
        self.assertFalse(self.page.is_modified())
        self.page._spinboxes["short_work_duration"].setValue(25)
        self.assertTrue(self.page.is_modified())
        self.assertEqual(self.page._title_label.text(), "设置 *")

    def test_modified_mark_cleared_after_save(self) -> None:
        """保存后变更标记应清除。"""
        self.page._spinboxes["short_work_duration"].setValue(25)
        self.assertTrue(self.page.is_modified())
        self.page.save_settings()
        self.assertFalse(self.page.is_modified())
        self.assertEqual(self.page._title_label.text(), "设置")

    def test_modified_mark_on_checkbox_change(self) -> None:
        """CheckBox 变化也应标记修改。"""
        self.page._checkboxes["enable_sound"].setChecked(True)
        self.assertTrue(self.page.is_modified())

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------
    def test_reset_restores_defaults(self) -> None:
        """恢复默认后控件回到默认值。"""
        # 先修改
        self.page._spinboxes["short_work_duration"].setValue(50)
        self.page._checkboxes["enable_sound"].setChecked(True)
        self.page.save_settings()

        # 重置
        self.page._on_reset()

        # 应恢复默认
        self.assertEqual(self.page._spinboxes["short_work_duration"].value(), 20)
        self.assertFalse(self.page._checkboxes["enable_sound"].isChecked())
        # 标记应清除
        self.assertFalse(self.page.is_modified())

    def test_reset_publishes_event(self) -> None:
        """重置应发布 SETTINGS_CHANGED 事件。"""
        self.bus_events.clear()
        self.page._on_reset()
        self.assertTrue(
            any(et == EventType.SETTINGS_CHANGED for et, _ in self.bus_events)
        )


    # ------------------------------------------------------------------
    # V0.5 新增行为
    # ------------------------------------------------------------------
    def test_save_button_disabled_until_modified(self) -> None:
        """无改动时保存按钮禁用，改动后启用，保存后再次禁用。"""
        self.assertFalse(self.page._save_button.isEnabled())
        self.page._spinboxes["blink_cycle_seconds"].setValue(90)
        self.assertTrue(self.page._save_button.isEnabled())
        self.page.save_settings()
        self.assertFalse(self.page._save_button.isEnabled())

    def test_save_shows_feedback(self) -> None:
        """保存后应显示 ✓ 反馈文案。"""
        self.page._spinboxes["blink_cue_interval"].setValue(15)
        self.page.save_settings()
        self.assertIn("✓", self.page._status_label.text())

    def test_reset_shows_feedback_and_persists(self) -> None:
        """恢复默认后应显示 ✓ 反馈，并立即写入配置。"""
        self.page._spinboxes["blink_cycle_seconds"].setValue(120)
        self.page.save_settings()

        self.page._on_reset()

        self.assertIn("✓", self.page._status_label.text())
        self.assertEqual(self.service.get_setting("blink_cycle_seconds"), 60)
        self.assertEqual(self.page._spinboxes["blink_cycle_seconds"].value(), 60)

    def test_spinboxes_ignore_wheel(self) -> None:
        """时间控件的滚轮事件应被忽略（防误触）。"""
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QWheelEvent

        spin = self.page._spinboxes["move_interval"]
        before = spin.value()
        from PySide6.QtCore import Qt as _Qt

        event = QWheelEvent(
            QPoint(0, 0),
            QPoint(0, 0),
            QPoint(0, 0),
            QPoint(0, -120),
            _Qt.MouseButton.NoButton,
            _Qt.KeyboardModifier.NoModifier,
            _Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        spin.wheelEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(spin.value(), before)

    def test_sound_controls_disabled_when_sound_off(self) -> None:
        """关闭提示音时，音效方案与音量控件应灰掉。"""
        self.assertFalse(self.page._checkboxes["enable_sound"].isChecked())
        self.assertFalse(self.page._combos["sound_scheme"].isEnabled())
        self.assertFalse(self.page._volume_slider.isEnabled())

        self.page._checkboxes["enable_sound"].setChecked(True)
        self.assertTrue(self.page._combos["sound_scheme"].isEnabled())
        self.assertTrue(self.page._volume_slider.isEnabled())

    def test_volume_slider_saved(self) -> None:
        """音量滑块按 0~1 保存。"""
        self.page._volume_slider.setValue(70)
        self.page.save_settings()
        self.assertAlmostEqual(float(self.service.get_setting("sound_volume")), 0.7, places=2)

    def test_preset_position_applies_immediately(self) -> None:
        """选择预设位置应立即写入配置（不等保存）。"""
        self.page.position_preset_requested.connect(lambda v: None)
        combo = self.page._combos["cue_position"]
        index = combo.findData("top_right")
        combo.setCurrentIndex(index)
        self.assertEqual(self.service.get_setting("cue_position"), "top_right")

    def test_custom_position_requests_editor(self) -> None:
        """选择「自定义」应请求打开位置编辑器。"""
        requested: list[str] = []
        self.page.position_custom_requested.connect(lambda: requested.append("open"))
        combo = self.page._combos["cue_position"]
        combo.setCurrentIndex(combo.findData("custom"))
        self.assertEqual(requested, ["open"])


if __name__ == "__main__":
    unittest.main()
