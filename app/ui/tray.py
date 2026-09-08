"""系统托盘（Tray）。

提供 QSystemTrayIcon 与右键菜单：
- 左键单击托盘 → 显示主窗口
- 右键菜单 → 状态信息、立即休息、暂停/恢复、打开、设置、退出

通过 EventBus 订阅状态变化事件，实现托盘提示与菜单状态联动。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.config import defaults
from app.core.event_bus import EventBus, EventType
from app.i18n import get_translator, tr
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _make_fallback_icon(size: int = 64) -> QIcon:
    """绘制一个简单的蓝色圆形 + 白色眼睛轮廓作为兜底图标。

    当资源文件不存在或无法加载时使用。
    """
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))  # 透明背景

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)

    # 蓝色背景圆
    painter.setBrush(QColor("#4A90E2"))
    painter.setPen(QPen(QColor("#357ABD"), 2))
    painter.drawEllipse(2, 2, size - 4, size - 4)

    # 白色眼睛轮廓
    painter.setBrush(QColor("white"))
    painter.setPen(QPen(QColor("#357ABD"), 2))
    eye_margin = int(size * 0.18)
    eye_rect_top = int(size * 0.30)
    eye_rect_height = int(size * 0.40)
    painter.drawEllipse(
        eye_margin,
        eye_rect_top,
        size - eye_margin * 2,
        eye_rect_height,
    )

    # 深色瞳孔
    pupil_size = int(size * 0.18)
    pupil_x = (size - pupil_size) // 2
    pupil_y = (size - pupil_size) // 2
    painter.setBrush(QColor("#2C3E50"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(pupil_x, pupil_y, pupil_size, pupil_size)

    painter.end()
    return QIcon(pix)


def _load_tray_icon() -> QIcon:
    """加载托盘图标，优先使用资源文件，失败则回退到绘制图标。"""
    # 资源路径：app/assets/icons/eyerest.svg
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "icons" / "eyerest.svg"

    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
        logger.warning("SVG 图标加载失败，使用兜底图标: %s", icon_path)
    else:
        logger.warning("图标文件不存在，使用兜底图标: %s", icon_path)

    # 回退方案 1：绘制图标
    return _make_fallback_icon()


class TrayIcon(QObject):
    """EyeRest 系统托盘管理器。

    封装 QSystemTrayIcon，提供：
    - 左键单击打开主窗口
    - 右键菜单（状态、立即休息、暂停/恢复、打开、设置、退出）
    - 桌面通知
    - 与 EventBus 联动的状态更新
    """

    # 退出请求信号，main.py 连接到 QApplication.quit()
    exit_requested = Signal()
    # 打开设置页请求信号
    settings_requested = Signal()

    def __init__(self, main_window, event_bus: EventBus | None = None) -> None:
        super().__init__()
        self._main_window = main_window
        self._event_bus = event_bus

        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None

        # 状态相关
        self._is_paused: bool = False
        self._status_text: str = tr("state.active")
        self._effective_time_text: str = "00:00:00"
        self._next_break_text: str = "--:--"

        # 菜单中的状态项引用（用于动态更新）
        self._status_action: QAction | None = None
        self._effective_action: QAction | None = None
        self._next_break_action: QAction | None = None
        self._pause_action: QAction | None = None
        self._break_now_action: QAction | None = None
        self._pause_today_action: QAction | None = None
        self._pause_2h_action: QAction | None = None
        self._game_mode_action: QAction | None = None
        self._open_action: QAction | None = None
        self._settings_action: QAction | None = None
        self._quit_action: QAction | None = None

        # 订阅事件
        if self._event_bus is not None:
            self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
            self._event_bus.subscribe(EventType.APP_RESUME, self._on_app_resume)

        # 语言切换时刷新菜单
        get_translator().add_listener(self._on_language_changed)

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    def setup_tray(self) -> None:
        """创建 QSystemTrayIcon、设置图标与提示、构建菜单并显示。"""
        if self._tray is not None:
            return  # 避免重复初始化

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统不支持托盘图标，托盘功能将不可用")
            return

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_load_tray_icon())
        self._tray.setToolTip(self._build_tooltip())

        self._build_context_menu()
        self._tray.setContextMenu(self._menu)

        # 左键单击 / 双击激活
        self._tray.activated.connect(self._on_tray_activated)

        logger.debug("系统托盘初始化完成")

    def show_tray(self) -> None:
        """显示托盘图标。"""
        if self._tray is not None:
            self._tray.show()
            logger.debug("托盘已显示")

    def hide_tray(self) -> None:
        """隐藏托盘图标。"""
        if self._tray is not None:
            self._tray.hide()
            logger.debug("托盘已隐藏")

    def update_tooltip(self, status_text: str | None = None) -> None:
        """更新托盘提示文本。

        Args:
            status_text: 可选的状态文本；不传则使用内部状态。
        """
        if status_text is not None:
            self._status_text = status_text
        if self._tray is not None:
            self._tray.setToolTip(self._build_tooltip())

    def update_status(self, status_text: str, *, effective: str | None = None,
                      next_break: str | None = None) -> None:
        """更新托盘菜单中的状态信息。

        Args:
            status_text: 主状态文本（如 "● 正在保护"）
            effective: 有效使用时长文本 "HH:MM:SS"
            next_break: 下一次休息文本 "--:--"
        """
        self._status_text = status_text
        if effective is not None:
            self._effective_time_text = effective
        if next_break is not None:
            self._next_break_text = next_break

        # 更新菜单项文本
        if self._status_action is not None:
            self._status_action.setText(self._status_text)
        if self._effective_action is not None:
            self._effective_action.setText(
                tr("tray.effective_usage", time=self._effective_time_text)
            )
        if self._next_break_action is not None:
            self._next_break_action.setText(
                tr("tray.next_break", time=self._next_break_text)
            )

        # 同步更新 tooltip
        self.update_tooltip()

    def show_notification(self, title: str, message: str,
                          icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
                          msecs: int = 5000) -> None:
        """显示桌面通知（气泡消息）。

        Args:
            title: 通知标题
            message: 通知内容
            icon: 消息图标等级
            msecs: 显示时长（毫秒）
        """
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(title, message, icon, msecs)
            logger.debug("桌面通知: %s - %s", title, message)
        else:
            logger.warning("托盘不可见，无法显示通知: %s - %s", title, message)

    # ------------------------------------------------------------------
    # 菜单构建
    # ------------------------------------------------------------------
    def _build_context_menu(self) -> None:
        """构建右键菜单。"""
        self._menu = QMenu()
        self._menu.setStyleSheet(
            "QMenu { font-size: 13px; }"
            "QMenu::item { padding: 4px 20px; }"
            "QMenu::separator { height: 1px; background: #ccc; margin: 4px 8px; }"
        )

        # 标题（不可选）
        title_action = QAction(f"👁 {defaults.APP_NAME} v{defaults.APP_VERSION}", self._menu)
        title_action.setEnabled(False)
        self._menu.addAction(title_action)

        self._menu.addSeparator()

        # 状态信息（不可选）
        self._status_action = QAction(self._status_text, self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        self._effective_action = QAction(
            tr("tray.effective_usage", time=self._effective_time_text), self._menu
        )
        self._effective_action.setEnabled(False)
        self._menu.addAction(self._effective_action)

        self._next_break_action = QAction(
            tr("tray.next_break", time=self._next_break_text), self._menu
        )
        self._next_break_action.setEnabled(False)
        self._menu.addAction(self._next_break_action)

        self._menu.addSeparator()

        # 立即休息
        self._break_now_action = QAction(tr("common.rest_now"), self._menu)
        self._break_now_action.triggered.connect(self._on_break_now)
        self._menu.addAction(self._break_now_action)

        # 暂停 / 恢复
        self._pause_action = QAction(tr("common.pause_30m"), self._menu)
        self._pause_action.triggered.connect(self._on_toggle_pause_30m)
        self._menu.addAction(self._pause_action)

        # 暂停 2 小时
        self._pause_2h_action = QAction(tr("tray.pause_2h"), self._menu)
        self._pause_2h_action.triggered.connect(self._on_pause_2h)
        self._menu.addAction(self._pause_2h_action)

        # 暂停今天
        self._pause_today_action = QAction(tr("tray.pause_today"), self._menu)
        self._pause_today_action.triggered.connect(self._on_pause_today)
        self._menu.addAction(self._pause_today_action)

        # 游戏/会议模式（暂停提醒，手动恢复）
        self._game_mode_action = QAction(tr("tray.game_mode"), self._menu)
        self._game_mode_action.triggered.connect(self._on_game_mode)
        self._menu.addAction(self._game_mode_action)

        self._menu.addSeparator()

        # 打开 EyeRest
        self._open_action = QAction(tr("tray.open_app"), self._menu)
        self._open_action.triggered.connect(self._on_open_main_window)
        self._menu.addAction(self._open_action)

        # 设置
        self._settings_action = QAction(tr("nav.settings"), self._menu)
        self._settings_action.triggered.connect(self._on_settings)
        self._menu.addAction(self._settings_action)

        self._menu.addSeparator()

        # 退出
        self._quit_action = QAction(tr("tray.quit"), self._menu)
        self._quit_action.triggered.connect(self._on_quit)
        self._menu.addAction(self._quit_action)

    # ------------------------------------------------------------------
    # 槽函数
    # ------------------------------------------------------------------
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """托盘图标激活（单击/双击）。"""
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,        # 左键单击
            QSystemTrayIcon.ActivationReason.DoubleClick,    # 双击
        ):
            self._on_left_click()

    def _on_left_click(self) -> None:
        """左键点击 → 打开主窗口。"""
        self._show_main_window()
        logger.debug("托盘左键点击，显示主窗口")

    def _on_open_main_window(self) -> None:
        """菜单"打开 EyeRest"。"""
        self._show_main_window()

    def _on_break_now(self) -> None:
        """立即休息：发布 BreakTriggered 事件。"""
        logger.info("托盘菜单: 立即休息")
        if self._event_bus is not None:
            self._event_bus.publish(EventType.BREAK_TRIGGERED, {"duration": "short"})

    def _on_toggle_pause_30m(self) -> None:
        """暂停 30 分钟 / 恢复。"""
        if self._is_paused:
            logger.info("托盘菜单: 恢复保护")
            self._resume()
        else:
            logger.info("托盘菜单: 暂停 30 分钟")
            if self._event_bus is not None:
                self._event_bus.publish(
                    EventType.APP_PAUSE, {"duration_minutes": 30}
                )
            self._set_paused(True, status_text=tr("tray.paused_30m"))

    def _on_pause_2h(self) -> None:
        """暂停 2 小时。"""
        logger.info("托盘菜单: 暂停 2 小时")
        if self._event_bus is not None:
            self._event_bus.publish(
                EventType.APP_PAUSE, {"duration_minutes": 120}
            )
        self._set_paused(True, status_text=tr("tray.paused_2h"))

    def _on_game_mode(self) -> None:
        """游戏/会议模式：暂停提醒，直到用户手动恢复。"""
        logger.info("托盘菜单: 游戏/会议模式")
        if self._event_bus is not None:
            self._event_bus.publish(EventType.APP_PAUSE, {"duration_minutes": None})
        self._set_paused(True, status_text=tr("tray.game_mode_active"))

    def _on_pause_today(self) -> None:
        """暂停今天。"""
        logger.info("托盘菜单: 暂停今天")
        if self._event_bus is not None:
            self._event_bus.publish(
                EventType.APP_PAUSE, {"duration": "today"}
            )
        self._set_paused(True, status_text=tr("tray.paused_today"))

    def _on_settings(self) -> None:
        """打开设置页：优先调用主窗口方法，否则发信号。"""
        logger.info("托盘菜单: 设置")
        # 先显示主窗口
        self._show_main_window()
        # 如果主窗口有 show_settings 方法则调用
        if hasattr(self._main_window, "show_settings"):
            self._main_window.show_settings()
        else:
            self.settings_requested.emit()

    def _on_quit(self) -> None:
        """退出应用。"""
        logger.info("托盘菜单: 退出")
        self.exit_requested.emit()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _show_main_window(self) -> None:
        """显示并激活主窗口。"""
        window = self._main_window
        if window is None:
            return
        # 如果窗口被最小化，恢复
        window.showNormal() if hasattr(window, "showNormal") else window.show()
        window.show()
        window.raise_()
        window.activateWindow()

    def _set_paused(self, paused: bool, *, status_text: str | None = None) -> None:
        """更新暂停状态并同步菜单。"""
        self._is_paused = paused
        if status_text:
            self._status_text = status_text
        elif paused:
            self._status_text = tr("state.paused")
        else:
            self._status_text = tr("state.active")

        # 更新暂停按钮文本
        if self._pause_action is not None:
            self._pause_action.setText(
                tr("tray.resume") if paused else tr("common.pause_30m")
            )

        # 更新状态菜单项
        if self._status_action is not None:
            self._status_action.setText(self._status_text)

        # 同步 tooltip
        self.update_tooltip()

    def _resume(self) -> None:
        """恢复保护。"""
        if self._event_bus is not None:
            self._event_bus.publish(EventType.APP_RESUME)
        self._set_paused(False, status_text=tr("state.active"))

    def _build_tooltip(self) -> str:
        """构建托盘提示文本。"""
        return (
            f"{defaults.APP_NAME} v{defaults.APP_VERSION}\n"
            f"{self._status_text}\n"
            f"{tr('tray.effective_usage', time=self._effective_time_text)}\n"
            f"{tr('tray.next_break', time=self._next_break_text)}"
        )

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_menu(self) -> None:
        """语言变化时重新设置菜单与 tooltip 文本。"""
        if self._menu is None:
            return
        if self._break_now_action is not None:
            self._break_now_action.setText(tr("common.rest_now"))
        if self._pause_action is not None:
            self._pause_action.setText(
                tr("tray.resume") if self._is_paused else tr("common.pause_30m")
            )
        if self._pause_2h_action is not None:
            self._pause_2h_action.setText(tr("tray.pause_2h"))
        if self._pause_today_action is not None:
            self._pause_today_action.setText(tr("tray.pause_today"))
        if self._game_mode_action is not None:
            self._game_mode_action.setText(tr("tray.game_mode"))
        if self._open_action is not None:
            self._open_action.setText(tr("tray.open_app"))
        if self._settings_action is not None:
            self._settings_action.setText(tr("nav.settings"))
        if self._quit_action is not None:
            self._quit_action.setText(tr("tray.quit"))
        # 状态 / 有效使用 / 下一次休息项与 tooltip 一并刷新
        self.update_status(self._status_text)

    def _on_language_changed(self, lang: str) -> None:
        """语言变化监听回调。"""
        self.retranslate_menu()

    # ------------------------------------------------------------------
    # 事件总线回调
    # ------------------------------------------------------------------
    def _on_state_changed(self, data) -> None:
        """处理 StateChanged 事件，更新状态显示。"""
        logger.debug("收到 StateChanged: %s", data)
        if isinstance(data, dict):
            state = data.get("state") or data.get("new_state") or ""
            if state:
                state_str = str(state).lower()
                if state_str in ("paused", "pause"):
                    self._set_paused(True, status_text=tr("state.paused"))
                    return
                if state_str in ("running", "active", "resumed"):
                    self._set_paused(False, status_text=tr("state.active"))
                    return
            # 支持直接传入 status 字段
            if "status" in data:
                self.update_status(str(data["status"]))
                return
            # 支持传入各字段
            kwargs = {}
            if "status" in data:
                kwargs["status_text"] = str(data["status"])
            if "effective" in data:
                kwargs["effective"] = str(data["effective"])
            if "next_break" in data:
                kwargs["next_break"] = str(data["next_break"])
            if kwargs:
                self.update_status(**kwargs)

    def _on_app_resume(self, data) -> None:
        """处理 AppResume 事件。"""
        logger.debug("收到 AppResume: %s", data)
        self._set_paused(False, status_text=tr("state.active"))

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """清理资源，取消事件订阅与语言监听。"""
        get_translator().remove_listener(self._on_language_changed)
        if self._event_bus is not None:
            self._event_bus.unsubscribe(EventType.STATE_CHANGED, self._on_state_changed)
            self._event_bus.unsubscribe(EventType.APP_RESUME, self._on_app_resume)
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
        logger.debug("TrayIcon 资源已清理")
