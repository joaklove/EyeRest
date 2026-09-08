"""BreakService 单元测试（V0.5：JSON 配置存储）。

覆盖设置读写、类型转换、批量保存、重置、事件发布与时长格式化。
使用临时目录中的 config.json，避免污染真实用户数据。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app.config.settings_store import JsonSettingsStore
from app.core.event_bus import EventBus, EventType
from app.services.break_service import BreakService


class TestBreakService(unittest.TestCase):
    """BreakService 单元测试。"""

    def setUp(self) -> None:
        # 使用临时目录中的 config.json，避免污染真实用户数据
        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_cfg_"))
        self.store = JsonSettingsStore(self._tmpdir / "config.json")
        self.bus = EventBus()
        self.events: list[tuple[EventType, object]] = []
        self.bus.subscribe(EventType.SETTINGS_CHANGED, self._record)
        self.service = BreakService(store=self.store, event_bus=self.bus)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _record(self, data: object) -> None:
        self.events.append((EventType.SETTINGS_CHANGED, data))

    # ------------------------------------------------------------------
    # 默认值
    # ------------------------------------------------------------------
    def test_default_settings_match_constants(self) -> None:
        """DEFAULT_SETTINGS 应与 defaults 常量一致。"""
        from app.config import defaults as d

        expected = {
            "short_work_duration": d.SHORT_WORK_DURATION,
            "short_break_duration": d.SHORT_BREAK_DURATION,
            "long_work_duration": d.LONG_WORK_DURATION,
            "long_break_duration": d.LONG_BREAK_DURATION,
            "warning_duration": d.WARNING_DURATION,
            "idle_threshold": d.IDLE_THRESHOLD,
            "natural_rest_threshold": d.NATURAL_REST_THRESHOLD,
            "postpone_duration": d.POSTPONE_DURATION,
            "max_postpone": d.MAX_POSTPONE,
        }
        for key, value in expected.items():
            self.assertEqual(BreakService.DEFAULT_SETTINGS[key], value, f"键 {key} 默认值不符")

    def test_get_all_settings_returns_all_keys(self) -> None:
        """get_all_settings 应返回 DEFAULT_SETTINGS 中全部键。"""
        all_settings = self.service.get_all_settings()
        self.assertEqual(set(all_settings.keys()), set(BreakService.DEFAULT_SETTINGS.keys()))

    def test_get_setting_default_when_missing(self) -> None:
        """未存储的键应返回默认值。"""
        self.assertEqual(self.service.get_setting("short_work_duration"), 1200)
        self.assertEqual(self.service.get_setting("enable_sound"), False)
        self.assertEqual(self.service.get_setting("minimize_to_tray"), True)

    def test_get_setting_unknown_key_returns_fallback(self) -> None:
        """未知键返回 fallback default。"""
        self.assertIsNone(self.service.get_setting("nonexistent_key"))
        self.assertEqual(self.service.get_setting("nonexistent_key", "fallback"), "fallback")

    # ------------------------------------------------------------------
    # 单个读写与类型转换
    # ------------------------------------------------------------------
    def test_set_and_get_int(self) -> None:
        self.service.set_setting("short_work_duration", 1800)
        self.assertEqual(self.service.get_setting("short_work_duration"), 1800)
        # 验证类型为 int
        self.assertIsInstance(self.service.get_setting("short_work_duration"), int)

    def test_set_and_get_bool_true(self) -> None:
        self.service.set_setting("enable_sound", True)
        self.assertTrue(self.service.get_setting("enable_sound"))
        self.assertIsInstance(self.service.get_setting("enable_sound"), bool)

    def test_set_and_get_bool_false(self) -> None:
        self.service.set_setting("enable_notification", False)
        self.assertFalse(self.service.get_setting("enable_notification"))

    def test_type_coercion_from_string(self) -> None:
        """旧版本曾以字符串存储，读取时应正确转换类型。"""
        self.store.set("short_work_duration", "2400")
        self.store.set("enable_sound", "True")
        self.store.set("enable_notification", "False")

        self.assertEqual(self.service.get_setting("short_work_duration"), 2400)
        self.assertTrue(self.service.get_setting("enable_sound"))
        self.assertFalse(self.service.get_setting("enable_notification"))

    # ------------------------------------------------------------------
    # 批量保存
    # ------------------------------------------------------------------
    def test_save_settings_persists(self) -> None:
        to_save = {
            "short_work_duration": 1500,
            "short_break_duration": 30,
            "enable_sound": True,
        }
        changed = self.service.save_settings(to_save)
        self.assertEqual(set(changed.keys()), set(to_save.keys()))

        # 新建一个 service 实例（重新读盘）验证持久化
        reloaded = JsonSettingsStore(self._tmpdir / "config.json")
        new_service = BreakService(store=reloaded, event_bus=None)
        self.assertEqual(new_service.get_setting("short_work_duration"), 1500)
        self.assertEqual(new_service.get_setting("short_break_duration"), 30)
        self.assertTrue(new_service.get_setting("enable_sound"))

    def test_save_settings_skips_unchanged(self) -> None:
        """未变更的键不应出现在 changed 中。"""
        # 先设置一个值
        self.service.set_setting("short_work_duration", 1500)
        self.events.clear()
        # 再次保存相同值
        changed = self.service.save_settings({"short_work_duration": 1500})
        self.assertEqual(changed, {})
        self.assertEqual(len(self.events), 0)

    def test_save_settings_ignores_unknown_keys(self) -> None:
        changed = self.service.save_settings({"unknown_key": 123, "short_work_duration": 1200})
        self.assertNotIn("unknown_key", changed)

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------
    def test_reset_to_defaults(self) -> None:
        # 先修改几项
        self.service.set_setting("short_work_duration", 9999)
        self.service.set_setting("enable_sound", True)
        self.assertTrue(self.service.get_setting("enable_sound"))

        self.service.reset_to_defaults()

        # 应恢复默认值
        self.assertEqual(self.service.get_setting("short_work_duration"), 1200)
        self.assertFalse(self.service.get_setting("enable_sound"))

    def test_reset_publishes_event(self) -> None:
        self.events.clear()
        self.service.reset_to_defaults()
        self.assertEqual(len(self.events), 1)
        _, data = self.events[0]
        self.assertTrue(data.get("reset"))

    # ------------------------------------------------------------------
    # 事件发布
    # ------------------------------------------------------------------
    def test_set_setting_publishes_event(self) -> None:
        self.events.clear()
        self.service.set_setting("short_work_duration", 1800)
        self.assertEqual(len(self.events), 1)
        event_type, data = self.events[0]
        self.assertEqual(event_type, EventType.SETTINGS_CHANGED)
        self.assertEqual(data["key"], "short_work_duration")
        self.assertEqual(data["value"], 1800)

    def test_save_settings_publishes_event_with_changed(self) -> None:
        self.events.clear()
        self.service.save_settings({
            "short_work_duration": 1500,
            "enable_sound": True,
        })
        # 批量保存只发一次事件
        self.assertEqual(len(self.events), 1)
        _, data = self.events[0]
        self.assertIn("settings", data)
        self.assertEqual(data["settings"]["short_work_duration"], 1500)
        self.assertEqual(data["settings"]["enable_sound"], True)

    def test_no_event_bus_still_works(self) -> None:
        """event_bus=None 时仍能正常读写，不报错。"""
        svc = BreakService(store=self.store, event_bus=None)
        svc.set_setting("short_work_duration", 1000)
        self.assertEqual(svc.get_setting("short_work_duration"), 1000)
        svc.save_settings({"enable_sound": True})
        self.assertTrue(svc.get_setting("enable_sound"))

    # ------------------------------------------------------------------
    # 时长格式化
    # ------------------------------------------------------------------
    def test_format_seconds(self) -> None:
        self.assertEqual(self.service.format_duration_label(20), "20 秒")
        self.assertEqual(self.service.format_duration_label(0), "0 秒")
        self.assertEqual(self.service.format_duration_label(59), "59 秒")

    def test_format_minutes(self) -> None:
        self.assertEqual(self.service.format_duration_label(1200), "20 分钟")
        self.assertEqual(self.service.format_duration_label(300), "5 分钟")

    def test_format_minutes_with_seconds(self) -> None:
        self.assertEqual(self.service.format_duration_label(90), "1 分 30 秒")
        self.assertEqual(self.service.format_duration_label(125), "2 分 5 秒")

    def test_format_hours(self) -> None:
        self.assertEqual(self.service.format_duration_label(5400), "1 小时 30 分钟")
        self.assertEqual(self.service.format_duration_label(3600), "1 小时")
        self.assertEqual(self.service.format_duration_label(7200), "2 小时")

    def test_format_invalid_input(self) -> None:
        self.assertEqual(self.service.format_duration_label("abc"), "abc")


if __name__ == "__main__":
    unittest.main()
