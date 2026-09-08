"""NotificationService 单元测试。

覆盖三级通知触发、事件发布与 TrayIcon 委托，
以及 tray_icon=None / event_bus=None 的优雅降级。
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.core.event_bus import EventBus, EventType
from app.services.notification_service import NotificationService


class FakeTrayIcon:
    """模拟 TrayIcon，记录 show_notification 调用。"""

    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def show_notification(self, title: str, message: str, **kwargs) -> None:
        self.notifications.append((title, message))


class TestNotificationServiceLevels(unittest.TestCase):
    """三级通知触发测试。"""

    def setUp(self) -> None:
        self.tray = FakeTrayIcon()
        self.bus = EventBus()
        self.events: list[tuple[EventType, object]] = []
        self.bus.subscribe(EventType.BREAK_TRIGGERED, self._record)
        self.svc = NotificationService(tray_icon=self.tray, event_bus=self.bus)

    def _record(self, data: object) -> None:
        self.events.append((EventType.BREAK_TRIGGERED, data))

    # ------------------------------------------------------------------
    # Level 常量
    # ------------------------------------------------------------------
    def test_level_constants(self) -> None:
        """三个级别常量值正确。"""
        self.assertEqual(NotificationService.LEVEL_SOFT, 1)
        self.assertEqual(NotificationService.LEVEL_BREAK, 2)
        self.assertEqual(NotificationService.LEVEL_LONG_BREAK, 3)

    # ------------------------------------------------------------------
    # Level 1: 柔和提醒
    # ------------------------------------------------------------------
    def test_soft_warning_calls_tray_notification(self) -> None:
        """Level 1 应通过 TrayIcon.show_notification 发送通知。"""
        self.svc.notify_soft_warning(message="该休息一下了", seconds=30)

        self.assertEqual(len(self.tray.notifications), 1)
        title, body = self.tray.notifications[0]
        self.assertIn("EyeRest", title)
        self.assertIn("该休息一下了", body)
        self.assertIn("30", body)

    def test_soft_warning_default_message(self) -> None:
        """Level 1 默认消息应包含在通知中。"""
        self.svc.notify_soft_warning()

        self.assertEqual(len(self.tray.notifications), 1)
        _, body = self.tray.notifications[0]
        self.assertIn("该休息一下了", body)

    def test_soft_warning_does_not_publish_event(self) -> None:
        """Level 1 不应发布 BREAK_TRIGGERED 事件。"""
        self.svc.notify_soft_warning()
        self.assertEqual(len(self.events), 0)

    # ------------------------------------------------------------------
    # Level 2: 休息触发
    # ------------------------------------------------------------------
    def test_break_publishes_event(self) -> None:
        """Level 2 应发布 BREAK_TRIGGERED 事件。"""
        self.svc.notify_break(break_type="short", duration=20)

        self.assertEqual(len(self.events), 1)
        event_type, data = self.events[0]
        self.assertEqual(event_type, EventType.BREAK_TRIGGERED)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["break_type"], "short")
        self.assertEqual(data["duration"], 20)

    def test_break_default_values(self) -> None:
        """Level 2 默认值：short / 20 秒。"""
        self.svc.notify_break()

        self.assertEqual(len(self.events), 1)
        _, data = self.events[0]
        self.assertEqual(data["break_type"], "short")
        self.assertEqual(data["duration"], 20)

    def test_break_does_not_call_tray(self) -> None:
        """Level 2 不应通过 TrayIcon 发送通知。"""
        self.svc.notify_break()
        self.assertEqual(len(self.tray.notifications), 0)

    # ------------------------------------------------------------------
    # Level 3: 长休息触发
    # ------------------------------------------------------------------
    def test_long_break_publishes_event(self) -> None:
        """Level 3 应发布 BREAK_TRIGGERED 事件，break_type=long。"""
        self.svc.notify_long_break(duration=300)

        self.assertEqual(len(self.events), 1)
        event_type, data = self.events[0]
        self.assertEqual(event_type, EventType.BREAK_TRIGGERED)
        self.assertEqual(data["break_type"], "long")
        self.assertEqual(data["duration"], 300)

    def test_long_break_default_duration(self) -> None:
        """Level 3 默认时长 300 秒。"""
        self.svc.notify_long_break()

        self.assertEqual(len(self.events), 1)
        _, data = self.events[0]
        self.assertEqual(data["break_type"], "long")
        self.assertEqual(data["duration"], 300)

    # ------------------------------------------------------------------
    # 通用通知
    # ------------------------------------------------------------------
    def test_notify_message_calls_tray(self) -> None:
        """notify_message 应通过 TrayIcon 发送通知。"""
        self.svc.notify_message("测试标题", "测试内容")

        self.assertEqual(len(self.tray.notifications), 1)
        title, body = self.tray.notifications[0]
        self.assertEqual(title, "测试标题")
        self.assertEqual(body, "测试内容")

    def test_notify_message_does_not_publish_event(self) -> None:
        """notify_message 不应发布事件。"""
        self.svc.notify_message("标题", "内容")
        self.assertEqual(len(self.events), 0)


class TestNotificationServiceDegradation(unittest.TestCase):
    """通知服务优雅降级测试。"""

    def test_soft_warning_without_tray(self) -> None:
        """无 TrayIcon 时 Level 1 应跳过，不崩溃。"""
        bus = EventBus()
        svc = NotificationService(tray_icon=None, event_bus=bus)
        # 不应抛出异常
        svc.notify_soft_warning()

    def test_notify_message_without_tray(self) -> None:
        """无 TrayIcon 时 notify_message 应跳过，不崩溃。"""
        bus = EventBus()
        svc = NotificationService(tray_icon=None, event_bus=bus)
        svc.notify_message("标题", "内容")

    def test_break_without_event_bus_uses_global(self) -> None:
        """无 EventBus 时应使用全局单例，不崩溃。"""
        from app.core.event_bus import get_event_bus

        # 清除全局单例以测试新建逻辑
        global_bus = get_event_bus()
        svc = NotificationService(tray_icon=FakeTrayIcon(), event_bus=None)

        # 注册监听到全局总线
        received: list[object] = []
        global_bus.subscribe(EventType.BREAK_TRIGGERED, lambda d: received.append(d))

        svc.notify_break(break_type="short", duration=20)

        self.assertEqual(len(received), 1)

    def test_init_without_args(self) -> None:
        """无参初始化应使用全局单例 EventBus，不崩溃。"""
        svc = NotificationService()
        # 调用方法不应抛出异常
        svc.notify_break()
        svc.notify_long_break()


if __name__ == "__main__":
    unittest.main()
