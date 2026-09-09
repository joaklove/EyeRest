"""主窗口（V0.6「轻奢艺术·温暖治愈」侧边栏布局）。

设计要点：

* 窗口尺寸 900×680（最小 820×600），彻底告别旧版 480×720 拥挤感
* 取消顶部 Dashboard / Statistics / Settings 三个按钮导航，
  改为左侧 :class:`Sidebar` —— 4 项导航（首页/设置/统计/关于）
* 当前页：白底 + 左侧 2px 主色 accent（不再用整块绿色按钮）
* 状态信息（ACTIVE / PAUSED）并入 Sidebar 顶部状态徽章
* 底部版本号 v0.6.0
* 删除传统 status bar（状态信息已在 Sidebar 顶部）
* 保留所有业务接口（force_close / show_window / connect_dashboard_actions
  / show_settings / notify 等）以便 main.py 继续工作
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import defaults
from app.core.event_bus import EventBus, EventType
from app.i18n import get_translator, tr
from app.ui.dashboard import Dashboard
from app.ui.settings import SettingsPage
from app.ui.statistics import StatisticsPage
from app.ui.theme import tokens
from app.ui.theme.styles_qss import GLOBAL_QSS
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 侧边栏导航项
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    # (i18n key, icon, page index)
    ("nav.dashboard", "🏠", 0),
    ("nav.settings", "⚙", 2),   # 顺序：首页→设置→统计→关于
    ("nav.statistics", "📊", 1),
    ("nav.about", "ℹ", 3),
]


class _SidebarButton(QPushButton):
    """侧边栏导航项：左 2px accent + 白底（激活）/ 透明（默认）/ 暖灰（hover）。"""

    _STYLE_TEMPLATE = """
    QPushButton {{
        background-color: {bg};
        color: {text};
        border: none;
        border-left: 3px solid {accent};
        border-radius: 0px;
        padding: 10px 18px 10px 22px;
        text-align: left;
        font-size: {font}px;
        font-weight: {weight};
    }}
    QPushButton:hover {{
        background-color: {hover};
    }}
    """

    def __init__(self, text: str, icon: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(f"  {icon}   {text}", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setMinimumHeight(42)
        self._apply(False)

    def _apply(self, active: bool) -> None:
        if active:
            self.setStyleSheet(self._STYLE_TEMPLATE.format(
                bg=tokens.SIDEBAR_ITEM_ACTIVE_BG,
                text=tokens.SIDEBAR_ITEM_ACTIVE_TEXT,
                accent=tokens.PRIMARY,
                hover=tokens.SIDEBAR_ITEM_ACTIVE_BG,
                font=tokens.BODY,
                weight=tokens.WEIGHT_BOLD,
            ))
        else:
            self.setStyleSheet(self._STYLE_TEMPLATE.format(
                bg="transparent",
                text=tokens.TEXT_PRIMARY,
                accent="transparent",
                hover=tokens.SIDEBAR_ITEM_HOVER,
                font=tokens.BODY,
                weight=tokens.WEIGHT_REGULAR,
            ))

    def set_active(self, active: bool) -> None:
        self.setChecked(active)
        self._apply(active)


# ---------------------------------------------------------------------------
# 关于页（极简占位）
# ---------------------------------------------------------------------------

class _AboutPage(QWidget):
    """关于 EyeRest（极简静态页，无业务依赖）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel("EyeRest", self)
        title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.DISPLAY}px; font-weight: {tokens.WEIGHT_BOLD};"
        )
        layout.addWidget(title)

        sub = QLabel(tr("about.tagline"), self)
        sub.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.H3}px;"
        )
        layout.addWidget(sub)

        from app.ui.theme.components import SoftCard
        card = SoftCard(self)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(8)
        cl.addWidget(self._row("about.version", defaults.APP_VERSION))
        cl.addWidget(self._row("about.principle", tr("about.principle_value")))
        cl.addWidget(self._row("about.persisted", tr("about.persisted_value")))
        layout.addWidget(card)
        layout.addStretch(1)

    def _row(self, key: str, value: str) -> QWidget:
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        k = QLabel(tr(key), row)
        k.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.BODY}px; min-width: 110px;"
        )
        v = QLabel(value, row)
        v.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.BODY}px; font-weight: {tokens.WEIGHT_MEDIUM};"
        )
        v.setWordWrap(True)
        h.addWidget(k)
        h.addWidget(v, 1)
        return row


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """EyeRest 主窗口（V0.6 侧边栏版）。"""

    _REFRESH_INTERVAL_MS = 1000

    def __init__(
        self,
        timer_engine: Any = None,
        break_engine: Any = None,
        state_machine: Any = None,
        usage_service: Any = None,
        statistics_service: Any = None,
        break_service: Any = None,
        event_bus: Optional[EventBus] = None,
        parent: Optional[QWidget] = None,
        screen_session_engine: Any = None,
        blink_engine: Any = None,
        move_engine: Any = None,
        blink_stats_provider: Any = None,
        settings_hooks: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{defaults.APP_NAME} v{defaults.APP_VERSION}")
        self.resize(tokens.WINDOW_DEFAULT_W, tokens.WINDOW_DEFAULT_H)
        self.setMinimumSize(tokens.WINDOW_MIN_W, tokens.WINDOW_MIN_H)
        self.setStyleSheet(GLOBAL_QSS)

        self._timer_engine = timer_engine
        self._break_engine = break_engine
        self._state_machine = state_machine
        self._usage_service = usage_service
        self._statistics_service = statistics_service
        self._break_service = break_service
        self._event_bus = event_bus
        self._screen_session_engine = screen_session_engine
        self._blink_engine = blink_engine
        self._move_engine = move_engine
        self._blink_stats_provider = blink_stats_provider
        self._settings_hooks = settings_hooks or {}
        self._force_close: bool = False
        self._refresh_timer: Optional[QTimer] = None

        self._dashboard: Optional[Dashboard] = None
        self._stats_page: Optional[StatisticsPage] = None
        self._settings_page: Optional[SettingsPage] = None
        self._about_page: Optional[_AboutPage] = None
        self._placeholders: list[tuple[QLabel, str]] = []

        self._build_central_widget()
        self._start_refresh_timer()
        self._subscribe_events()
        get_translator().add_listener(self._on_language_changed)
        logger.debug("MainWindow 初始化完成（V0.6 侧边栏版）")

    # ------------------------------------------------------------------
    # 侧边栏 + 页面
    # ------------------------------------------------------------------
    def _build_central_widget(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralWidget")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 侧边栏
        outer.addWidget(self._build_sidebar())

        # 页面堆叠
        self._stack = QStackedWidget(central)
        self._stack.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        outer.addWidget(self._stack, 1)

        # 第 0 页：Dashboard
        if self._usage_service is not None:
            self._dashboard = Dashboard(
                timer_engine=self._timer_engine,
                break_engine=self._break_engine,
                state_machine=self._state_machine,
                usage_service=self._usage_service,
                event_bus=self._event_bus,
                parent=self._stack,
                screen_session_engine=self._screen_session_engine,
                blink_engine=self._blink_engine,
                move_engine=self._move_engine,
                blink_stats_provider=self._blink_stats_provider,
            )
            self._stack.addWidget(self._dashboard)
        else:
            self._stack.addWidget(self._build_placeholder("main.dashboard_placeholder"))

        # 第 1 页：Statistics
        if self._statistics_service is not None:
            self._stats_page = StatisticsPage(
                statistics_service=self._statistics_service,
                parent=self._stack,
            )
            self._stack.addWidget(self._stats_page)
        else:
            self._stack.addWidget(self._build_placeholder("main.stats_placeholder"))

        # 第 2 页：Settings
        if self._break_service is not None:
            self._settings_page = SettingsPage(
                break_service=self._break_service,
                parent=self._stack,
                notifier=self._settings_hooks.get("notify"),
            )
            self._connect_settings_hooks()
            self._stack.addWidget(self._settings_page)
        else:
            self._stack.addWidget(self._build_placeholder("main.settings_placeholder"))

        # 第 3 页：About
        self._about_page = _AboutPage(self._stack)
        self._stack.addWidget(self._about_page)

        self.setCentralWidget(central)

    def _build_sidebar(self) -> QWidget:
        """构建左侧 Sidebar。"""
        sidebar = QFrame(self)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(tokens.SIDEBAR_WIDTH)
        sidebar.setStyleSheet(
            f"QFrame#Sidebar {{"
            f"  background-color: {tokens.SIDEBAR_BG};"
            f"  border-right: 1px solid {tokens.BORDER_SOFT};"
            f"}}"
        )
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 22, 0, 14)
        layout.setSpacing(0)

        # Logo + 副标题
        logo_box = QVBoxLayout()
        logo_box.setContentsMargins(22, 0, 22, 0)
        logo_box.setSpacing(2)
        logo = QLabel(f"👁  {defaults.APP_NAME}", sidebar)
        logo.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.H1}px; font-weight: {tokens.WEIGHT_BOLD};"
        )
        logo_box.addWidget(logo)
        sub = QLabel(tr("sidebar.tagline"), sidebar)
        sub.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.CAPTION}px;"
        )
        sub.setWordWrap(True)
        logo_box.addWidget(sub)

        # 状态徽章
        self._status_badge = QLabel(f"●  {tr('main.state_active')}", sidebar)
        self._status_badge.setStyleSheet(
            f"QLabel {{"
            f"  color: {tokens.PRIMARY_TEXT};"
            f"  background-color: {tokens.PRIMARY_SOFT};"
            f"  border-radius: {tokens.RADIUS_PILL};"
            f"  padding: 4px 12px;"
            f"  font-size: {tokens.CAPTION}px;"
            f"  font-weight: {tokens.WEIGHT_MEDIUM};"
            f"}}"
        )
        logo_box.addSpacing(8)
        logo_box.addWidget(self._status_badge)
        layout.addLayout(logo_box)
        layout.addSpacing(20)

        # 导航项
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons: list[_SidebarButton] = []
        for i, (key, icon, page_idx) in enumerate(_NAV_ITEMS):
            btn = _SidebarButton(tr(key), icon, sidebar)
            btn.clicked.connect(lambda _checked=False, idx=page_idx: self._switch_page(idx))
            self._nav_group.addButton(btn, page_idx)
            self._nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        # 底部版本号
        version = QLabel(f"v{defaults.APP_VERSION}", sidebar)
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.CAPTION}px;"
        )
        layout.addWidget(version)

        # 默认选中 Dashboard
        self._nav_buttons[0].set_active(True)
        return sidebar

    def _build_placeholder(self, text_key: str) -> QWidget:
        """占位页（依赖缺失时显示）。"""
        page = QWidget()
        page.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(tr(text_key), page)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"color: {tokens.TEXT_MUTED}; font-size: {tokens.H3}px;"
        )
        layout.addWidget(label)
        self._placeholders.append((label, text_key))
        return page

    # ------------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------------
    def _switch_page(self, index: int) -> None:
        if hasattr(self, "_stack"):
            self._stack.setCurrentIndex(index)
        # 同步侧边栏选中态
        for btn in self._nav_buttons:
            btn.set_active(self._nav_group.id(btn) == index)
        if index == 1 and self._stats_page is not None:
            try:
                self._stats_page.refresh()
            except Exception:  # noqa: BLE001
                logger.exception("刷新统计页失败")
        if index == 2 and self._settings_page is not None:
            try:
                self._settings_page.load_settings()
            except Exception:  # noqa: BLE001
                logger.exception("加载设置页失败")
        logger.debug("切换到页面 %d", index)

    # ------------------------------------------------------------------
    # 刷新定时器
    # ------------------------------------------------------------------
    def _start_refresh_timer(self) -> None:
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start()
        logger.debug("Dashboard 刷新定时器已启动 (%dms)", self._REFRESH_INTERVAL_MS)

    def _on_refresh(self) -> None:
        if self._dashboard is not None:
            self._dashboard.update_display()
        self._refresh_status_badge()

    def _refresh_status_badge(self) -> None:
        """刷新 Sidebar 顶部状态徽章（取代原 status bar）。"""
        state_text = tr("main.state_active")
        is_active = True
        if self._state_machine is not None:
            try:
                state = self._state_machine.get_state()
                state_name = getattr(state, "name", str(state))
                is_active = state_name not in ("PAUSED", "OFF")
                state_text = tr("main.state_format", state=state_name)
            except Exception:  # noqa: BLE001
                logger.exception("获取状态失败")
        if not hasattr(self, "_status_badge"):
            return
        self._status_badge.setText(f"●  {state_text}")
        if is_active:
            self._status_badge.setStyleSheet(
                f"QLabel {{ color: {tokens.PRIMARY_TEXT}; background-color: {tokens.PRIMARY_SOFT};"
                f" border-radius: {tokens.RADIUS_PILL}; padding: 4px 12px;"
                f" font-size: {tokens.CAPTION}px; font-weight: {tokens.WEIGHT_MEDIUM}; }}"
            )
        else:
            self._status_badge.setStyleSheet(
                f"QLabel {{ color: {tokens.TEXT_SECONDARY}; background-color: {tokens.BG_SOFT};"
                f" border-radius: {tokens.RADIUS_PILL}; padding: 4px 12px;"
                f" font-size: {tokens.CAPTION}px; font-weight: {tokens.WEIGHT_MEDIUM}; }}"
            )

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------
    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("订阅 STATE_CHANGED 事件失败")

    def _unsubscribe_events(self) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.unsubscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("取消 STATE_CHANGED 订阅失败")

    def _on_state_changed(self, payload: Any) -> None:
        try:
            self._refresh_status_badge()
        except Exception:  # noqa: BLE001
            logger.exception("处理状态变化事件失败")

    # ------------------------------------------------------------------
    # 快捷操作处理
    # ------------------------------------------------------------------
    def connect_dashboard_actions(self) -> None:
        if self._dashboard is None:
            return
        self._dashboard.quick_break_requested.connect(self._on_quick_break)
        self._dashboard.pause_30m_requested.connect(self._on_pause_30m)
        self._dashboard.reset_requested.connect(self._on_reset)

    def _on_quick_break(self) -> None:
        logger.info("用户触发立即休息")
        if self._break_engine is not None:
            try:
                self._break_engine.on_break_start("short")
            except Exception:  # noqa: BLE001
                logger.exception("触发立即休息失败")
        if hasattr(self, "_status_badge"):
            self._flash_status(tr("main.break_triggered"))

    def _flash_status(self, text: str, ms: int = 3000) -> None:
        """在 Sidebar 状态徽章闪现一条提示，ms 毫秒后自动恢复。"""
        if not hasattr(self, "_status_badge"):
            return
        self._status_badge.setText(f"●  {text}")
        QTimer.singleShot(ms, self._refresh_status_badge)

    def _on_pause_30m(self) -> None:
        logger.info("用户触发暂停 30 分钟")
        if self._state_machine is not None:
            try:
                self._state_machine.pause(duration_minutes=30)
            except Exception:  # noqa: BLE001
                logger.exception("暂停保护失败")
        if hasattr(self, "_status_badge"):
            self._flash_status(tr("main.paused_30m"))

    def _on_reset(self) -> None:
        logger.info("用户触发重置计时")
        if self._screen_session_engine is not None:
            try:
                self._screen_session_engine.reset_session("manual")
            except Exception:  # noqa: BLE001
                logger.exception("重置屏幕暴露会话失败")
        if self._blink_engine is not None:
            try:
                self._blink_engine.reset()
            except Exception:  # noqa: BLE001
                logger.exception("重置眨眼计时失败")
        if self._move_engine is not None:
            try:
                self._move_engine.reset()
            except Exception:  # noqa: BLE001
                logger.exception("重置活动计时失败")
        if self._timer_engine is not None:
            try:
                self._timer_engine.reset()
            except Exception:  # noqa: BLE001
                logger.exception("重置计时失败")
        if self._dashboard is not None:
            self._dashboard.update_display()
        if hasattr(self, "_status_badge"):
            self._flash_status(tr("main.timer_reset"))

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """语言变化时刷新 Sidebar 导航/状态文案与占位页。"""
        for i, (key, icon, _idx) in enumerate(_NAV_ITEMS):
            self._nav_buttons[i].setText(f"  {icon}   {tr(key)}")
        # 副标题/Logo 不变（Logo 文本来自 APP_NAME）
        for label, key in self._placeholders:
            label.setText(tr(key))
        self._refresh_status_badge()

    def _on_language_changed(self, lang: str) -> None:
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # 窗口事件
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt 命名)
        if self._force_close:
            logger.info("主窗口真正关闭")
            if self._refresh_timer is not None:
                self._refresh_timer.stop()
            for page in (self._dashboard, self._stats_page, self._settings_page):
                if page is not None and hasattr(page, "cleanup"):
                    page.cleanup()
            get_translator().remove_listener(self._on_language_changed)
            self._unsubscribe_events()
            event.accept()
            super().closeEvent(event)
        else:
            logger.info("主窗口最小化到托盘")
            event.ignore()
            self.hide()

    # ------------------------------------------------------------------
    # 托盘集成
    # ------------------------------------------------------------------
    def show_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        logger.debug("主窗口已从托盘恢复显示")

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    def _connect_settings_hooks(self) -> None:
        if self._settings_page is None:
            return
        hooks = self._settings_hooks

        def _open_editor() -> None:
            callback = hooks.get("open_position_editor")
            if callable(callback):
                callback()

        def _apply_preset(value: str) -> None:
            callback = hooks.get("apply_preset_position")
            if callable(callback):
                callback(value)

        def _preview_sound(scheme: str) -> None:
            callback = hooks.get("preview_sound")
            if callable(callback):
                callback(scheme)

        self._settings_page.position_custom_requested.connect(_open_editor)
        self._settings_page.position_preset_requested.connect(_apply_preset)
        self._settings_page.sound_scheme_requested.connect(_preview_sound)
        self._settings_page.settings_changed.connect(lambda _d: None)

    def notify(self, text: str) -> None:
        """在 Sidebar 状态徽章 + 设置页提示区显示轻量反馈。"""
        if hasattr(self, "_status_badge"):
            self._flash_status(text, ms=2500)
        if self._settings_page is not None and hasattr(self._settings_page, "show_message"):
            self._settings_page.show_message(text)

    def show_settings(self) -> None:
        self._switch_page(2)
        logger.info("打开设置页")
