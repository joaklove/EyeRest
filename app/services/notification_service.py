"""通知服务 - 三级通知。

统一封装系统通知逻辑：
- Level 1 (柔和提醒)：通过 TrayIcon 发送桌面通知，无打断性
- Level 2 (休息触发)：发布 ``BREAK_TRIGGERED`` 事件，由 main.py 中的 BreakWindow 处理
- Level 3 (长休息触发)：同 Level 2，但休息类型为 long

通知服务不直接操作 UI，而是通过事件或委托，
保持与具体 UI 实现的解耦。
"""

from __future__ import annotations

from typing import Any

from app.core.event_bus import EventBus, EventType, get_event_bus
from app.utils.logger import get_logger

logger = get_logger(__name__)


class NotificationService:
    """通知服务 - 三级通知。

    通过 TrayIcon 和 EventBus 实现不同级别的休息提醒通知。

    Attributes:
        LEVEL_SOFT: Level 1 - 提前柔和桌面通知
        LEVEL_BREAK: Level 2 - 短休息触发
        LEVEL_LONG_BREAK: Level 3 - 长休息触发
    """

    LEVEL_SOFT = 1  # 提前30秒桌面通知
    LEVEL_BREAK = 2  # 休息窗口
    LEVEL_LONG_BREAK = 3  # 长休息窗口

    def __init__(self, tray_icon: Any = None, event_bus: EventBus | None = None) -> None:
        """初始化通知服务。

        Args:
            tray_icon: TrayIcon 实例，用于发送桌面通知。
                为 ``None`` 时 Level 1 通知将被跳过并记录警告。
            event_bus: EventBus 实例，用于发布休息触发事件。
                为 ``None`` 时使用全局单例 ``get_event_bus()``。
        """
        self._tray_icon = tray_icon
        self._event_bus = event_bus if event_bus is not None else get_event_bus()

    # ------------------------------------------------------------------
    # Level 1: 柔和提醒
    # ------------------------------------------------------------------
    def notify_soft_warning(self, message: str = "该休息一下了", seconds: int = 30) -> None:
        """Level 1: 柔和提醒（桌面通知，无声音）。

        通过 ``TrayIcon.show_notification`` 发送桌面气泡通知，
        不打断用户当前工作。

        Args:
            message: 通知内容。
            seconds: 距离休息的剩余秒数（用于通知文案）。
        """
        title = "EyeRest 提醒"
        body = f"{message}（{seconds} 秒后开始休息）"
        if self._tray_icon is not None:
            self._tray_icon.show_notification(title, body)
            logger.info("Level 1 柔和提醒已发送: %s", body)
        else:
            logger.warning("TrayIcon 未设置，跳过 Level 1 通知: %s", body)

    # ------------------------------------------------------------------
    # Level 2: 休息触发
    # ------------------------------------------------------------------
    def notify_break(self, break_type: str = "short", duration: int = 20) -> None:
        """Level 2: 休息触发（显示休息窗口）。

        发布 ``BREAK_TRIGGERED`` 事件，由 main.py 中的 BreakWindow 处理，
        显示全屏半透明休息窗口。

        Args:
            break_type: 休息类型 ``short`` / ``long``。
            duration: 休息时长（秒）。
        """
        data = {
            "break_type": break_type,
            "duration": duration,
        }
        if self._event_bus is not None:
            self._event_bus.publish(EventType.BREAK_TRIGGERED, data)
        logger.info("Level 2 休息触发: type=%s duration=%ds", break_type, duration)

    # ------------------------------------------------------------------
    # Level 3: 长休息触发
    # ------------------------------------------------------------------
    def notify_long_break(self, duration: int = 300) -> None:
        """Level 3: 长休息触发。

        发布 ``BREAK_TRIGGERED`` 事件（break_type=long），
        由 main.py 中的 BreakWindow 处理，显示长休息窗口。

        Args:
            duration: 长休息时长（秒），默认 300（5 分钟）。
        """
        self.notify_break(break_type="long", duration=duration)
        logger.info("Level 3 长休息触发: duration=%ds", duration)

    # ------------------------------------------------------------------
    # 通用桌面通知
    # ------------------------------------------------------------------
    def notify_message(self, title: str, message: str) -> None:
        """通用桌面通知。

        通过 TrayIcon 发送桌面气泡通知。TrayIcon 不可用时记录警告日志。

        Args:
            title: 通知标题。
            message: 通知内容。
        """
        if self._tray_icon is not None:
            self._tray_icon.show_notification(title, message)
            logger.debug("通用通知: %s - %s", title, message)
        else:
            logger.warning("TrayIcon 未设置，无法显示通知: %s - %s", title, message)
