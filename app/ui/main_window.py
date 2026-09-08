"""主窗口。

集成 Dashboard 首页、顶部导航、每秒自动刷新定时器与状态栏。
默认行为：关闭窗口时隐藏到系统托盘（最小化），不退出应用。
只有调用 ``force_close()`` 后，closeEvent 才会真正接受关闭事件。
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
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
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 导航按钮样式
# ---------------------------------------------------------------------------
_NAV_BTN_STYLE = """
QPushButton {
    background-color: transparent;
    color: #666;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #E8F5E9;
    color: #4CAF50;
}
QPushButton:checked {
    background-color: #4CAF50;
    color: white;
}
"""


class MainWindow(QMainWindow):
    """EyeRest 主窗口。

    集成 Dashboard 首页，提供顶部导航（Dashboard / Statistics / Settings），
    每秒自动刷新 Dashboard 数据，状态栏实时显示当前保护状态。

    默认行为：关闭窗口时隐藏到系统托盘（最小化），不退出应用。
    只有调用 ``force_close()`` 后，closeEvent 才会真正接受关闭事件。
    """

    # 每秒刷新间隔（毫秒）
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
        """初始化主窗口。

        Args:
            timer_engine: 计时引擎实例。
            break_engine: 休息引擎实例。
            state_machine: 状态机实例。
            usage_service: 使用统计服务实例。
            statistics_service: 统计服务实例。
            break_service: 休息配置服务实例。
            event_bus: 事件总线。
            parent: 父窗口部件。
            screen_session_engine: V1.0 屏幕暴露会话引擎（可空，传给 Dashboard）。
            blink_engine: V1.0 眨眼节奏引擎（可空，传给 Dashboard）。
            move_engine: V1.0 活动提醒引擎（可空，传给 Dashboard）。
            blink_stats_provider: 今日眨眼提示统计回调（可空）。
            settings_hooks: 设置页回调字典（可空），支持：
                ``open_position_editor`` / ``apply_preset_position`` /
                ``preview_sound`` / ``notify``。
        """
        super().__init__(parent)
        self.setWindowTitle(f"{defaults.APP_NAME} v{defaults.APP_VERSION}")
        self.resize(480, 720)
        self.setMinimumSize(380, 560)

        # 核心依赖
        self._timer_engine = timer_engine
        self._break_engine = break_engine
        self._state_machine = state_machine
        self._usage_service = usage_service
        self._statistics_service = statistics_service
        self._break_service = break_service
        self._event_bus = event_bus
        #: V1.0 屏幕暴露会话引擎
        self._screen_session_engine = screen_session_engine
        #: V1.0 眨眼提示引擎
        self._blink_engine = blink_engine
        #: V1.0 活动提醒引擎
        self._move_engine = move_engine
        #: 今日眨眼提示统计回调
        self._blink_stats_provider = blink_stats_provider
        #: 设置页回调（位置编辑器 / 预设位置 / 音效试听 / 提示）
        self._settings_hooks = settings_hooks or {}

        # 标记是否为真正退出（由托盘"退出"菜单触发）
        self._force_close: bool = False

        # 刷新定时器
        self._refresh_timer: Optional[QTimer] = None

        # 页面实例
        self._dashboard: Optional[Dashboard] = None
        self._stats_page: Optional[StatisticsPage] = None
        self._settings_page: Optional[SettingsPage] = None

        # 占位页标签 (QLabel, i18n key)，语言切换时刷新
        self._placeholders: list[tuple[QLabel, str]] = []

        self._build_central_widget()
        self._build_statusbar()
        self._start_refresh_timer()
        self._subscribe_events()
        # 语言切换时刷新导航 / 菜单 / 占位页等静态文本
        get_translator().add_listener(self._on_language_changed)

        logger.debug("MainWindow 初始化完成")

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_central_widget(self) -> None:
        """构建中心部件：顶部导航 + 页面堆叠区。"""
        central = QWidget(self)
        central.setStyleSheet("background-color: #F5F5F5;")
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # 顶部导航栏
        outer_layout.addWidget(self._build_nav_bar())

        # 页面堆叠区
        self._stack = QStackedWidget(central)
        self._stack.setStyleSheet("background-color: #F5F5F5;")

        # Dashboard 页
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
            # 无依赖时显示占位页
            self._stack.addWidget(self._build_placeholder("main.dashboard_placeholder"))

        # Statistics 页
        if self._statistics_service is not None:
            self._stats_page = StatisticsPage(
                statistics_service=self._statistics_service,
                parent=self._stack,
            )
            self._stack.addWidget(self._stats_page)
        else:
            self._stack.addWidget(self._build_placeholder("main.stats_placeholder"))

        # Settings 页
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

        outer_layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

    def _build_nav_bar(self) -> QWidget:
        """构建顶部导航栏。"""
        nav = QWidget()
        nav.setStyleSheet("background-color: #FFFFFF; border-bottom: 1px solid #E8E8E8;")
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        self._nav_btn_dashboard = QPushButton(tr("nav.dashboard"))
        self._nav_btn_dashboard.setCheckable(True)
        self._nav_btn_dashboard.setChecked(True)
        self._nav_btn_dashboard.setStyleSheet(_NAV_BTN_STYLE)
        self._nav_btn_dashboard.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_btn_dashboard.clicked.connect(lambda: self._switch_page(0))

        self._nav_btn_stats = QPushButton(tr("nav.statistics"))
        self._nav_btn_stats.setCheckable(True)
        self._nav_btn_stats.setStyleSheet(_NAV_BTN_STYLE)
        self._nav_btn_stats.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_btn_stats.clicked.connect(lambda: self._switch_page(1))

        self._nav_btn_settings = QPushButton(tr("nav.settings"))
        self._nav_btn_settings.setCheckable(True)
        self._nav_btn_settings.setStyleSheet(_NAV_BTN_STYLE)
        self._nav_btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_btn_settings.clicked.connect(lambda: self._switch_page(2))

        self._nav_group.addButton(self._nav_btn_dashboard, 0)
        self._nav_group.addButton(self._nav_btn_stats, 1)
        self._nav_group.addButton(self._nav_btn_settings, 2)

        layout.addWidget(self._nav_btn_dashboard)
        layout.addWidget(self._nav_btn_stats)
        layout.addWidget(self._nav_btn_settings)
        layout.addStretch(1)

        return nav

    def _build_placeholder(self, text_key: str) -> QWidget:
        """构建占位页面（text_key 为 i18n 键）。"""
        page = QWidget()
        page.setStyleSheet("background-color: #F5F5F5;")
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(tr(text_key))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 16px; color: #999;")
        layout.addWidget(label)
        self._placeholders.append((label, text_key))
        return page

    def _build_statusbar(self) -> None:
        """构建状态栏。

        只使用一个永久标签显示状态；不要与 ``showMessage`` 混用——
        临时消息会直接叠印在永久标签上方（Qt 不会自动隐藏后者），
        造成文字重影。
        """
        self._status_label = QLabel(tr("main.ready"))
        self._status_label.setStyleSheet("padding: 2px 8px; color: #666;")
        self.statusBar().addWidget(self._status_label)

    # ------------------------------------------------------------------
    # 页面切换
    # ------------------------------------------------------------------
    def _switch_page(self, index: int) -> None:
        """切换到指定页面索引。"""
        if hasattr(self, "_stack"):
            self._stack.setCurrentIndex(index)
        # 同步导航按钮选中态
        btn = self._nav_group.button(index)
        if btn is not None:
            btn.setChecked(True)
        # 切换到统计页时刷新数据
        if index == 1 and self._stats_page is not None:
            try:
                self._stats_page.refresh()
            except Exception:  # noqa: BLE001
                logger.exception("刷新统计页失败")
        # 切换到设置页时重新加载设置
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
        """启动每秒刷新定时器。"""
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start()
        logger.debug("Dashboard 刷新定时器已启动 (%dms)", self._REFRESH_INTERVAL_MS)

    def _on_refresh(self) -> None:
        """每秒刷新回调。"""
        if self._dashboard is not None:
            self._dashboard.update_display()
        self._refresh_statusbar()

    def _refresh_statusbar(self) -> None:
        """刷新状态栏显示当前状态。"""
        state_text = tr("main.ready")
        if self._state_machine is not None:
            try:
                state = self._state_machine.get_state()
                if hasattr(state, "name"):
                    state_text = tr("main.state_format", state=state.name)
            except Exception:  # noqa: BLE001
                logger.exception("获取状态失败")
        if hasattr(self, "_status_label"):
            self._status_label.setText(state_text)

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------
    def _subscribe_events(self) -> None:
        """订阅状态变化事件以更新状态栏。"""
        if self._event_bus is None:
            return
        try:
            self._event_bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("订阅 STATE_CHANGED 事件失败")

    def _unsubscribe_events(self) -> None:
        """取消事件订阅。"""
        if self._event_bus is None:
            return
        try:
            self._event_bus.unsubscribe(
                EventType.STATE_CHANGED, self._on_state_changed
            )
        except Exception:  # noqa: BLE001
            logger.exception("取消 STATE_CHANGED 订阅失败")

    def _on_state_changed(self, payload: Any) -> None:
        """状态变化事件回调。"""
        try:
            self._refresh_statusbar()
        except Exception:  # noqa: BLE001
            logger.exception("处理状态变化事件失败")

    # ------------------------------------------------------------------
    # 快捷操作处理
    # ------------------------------------------------------------------
    def connect_dashboard_actions(self) -> None:
        """连接 Dashboard 快捷操作信号到引擎处理方法。

        应在所有引擎就绪后调用（或在 __init__ 中依赖均已传入时自动调用）。
        """
        if self._dashboard is None:
            return
        self._dashboard.quick_break_requested.connect(self._on_quick_break)
        self._dashboard.pause_30m_requested.connect(self._on_pause_30m)
        self._dashboard.reset_requested.connect(self._on_reset)

    def _on_quick_break(self) -> None:
        """立即休息：触发 BreakEngine 开始短休息。"""
        logger.info("用户触发立即休息")
        if self._break_engine is not None:
            try:
                self._break_engine.on_break_start("short")
            except Exception:  # noqa: BLE001
                logger.exception("触发立即休息失败")
        if hasattr(self, "_status_label"):
            self._flash_status(tr("main.break_triggered"))

    def _flash_status(self, text: str, ms: int = 3000) -> None:
        """在状态栏闪现一条提示，ms 毫秒后自动恢复状态文本。

        替代 ``QStatusBar.showMessage``：后者与永久标签叠加渲染
        会产生文字重影。
        """
        if not hasattr(self, "_status_label"):
            return
        self._status_label.setText(text)
        QTimer.singleShot(ms, self._refresh_statusbar)

    def _on_pause_30m(self) -> None:
        """暂停 30 分钟。"""
        logger.info("用户触发暂停 30 分钟")
        if self._state_machine is not None:
            try:
                self._state_machine.pause(duration_minutes=30)
            except Exception:  # noqa: BLE001
                logger.exception("暂停保护失败")
        if hasattr(self, "_status_label"):
            self._flash_status(tr("main.paused_30m"))

    def _on_reset(self) -> None:
        """重置计时。

        V1.0：屏幕暴露会话存在时优先重置它（这才是休息计时的真正来源），
        同时重置眨眼提示计时，再回退重置 TimerEngine。
        """
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
        if hasattr(self, "_status_label"):
            self._flash_status(tr("main.timer_reset"))

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """语言变化时重新设置导航按钮与占位页等静态文本。"""
        self._nav_btn_dashboard.setText(tr("nav.dashboard"))
        self._nav_btn_stats.setText(tr("nav.statistics"))
        self._nav_btn_settings.setText(tr("nav.settings"))
        for label, key in self._placeholders:
            label.setText(tr(key))
        self._refresh_statusbar()

    def _on_language_changed(self, lang: str) -> None:
        """语言变化监听回调。"""
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # 窗口事件
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt 命名)
        """窗口关闭时触发。

        默认隐藏到托盘（忽略关闭事件）；只有 force_close() 后才真正退出。
        """
        if self._force_close:
            logger.info("主窗口真正关闭")
            # 停止刷新定时器
            if self._refresh_timer is not None:
                self._refresh_timer.stop()
            # 清理各页面订阅与语言监听
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
        """从托盘恢复并显示主窗口。"""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        logger.debug("主窗口已从托盘恢复显示")

    def force_close(self) -> None:
        """标记为真正退出，并触发关闭。"""
        self._force_close = True
        self.close()

    def _connect_settings_hooks(self) -> None:
        """把设置页的交互信号转发给 main 提供的回调。"""
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
        """在界面上显示一条轻量反馈（状态栏 + 设置页提示区）。"""
        try:
            self.statusBar().showMessage(text, 3000)
        except Exception:  # noqa: BLE001
            pass
        if self._settings_page is not None and hasattr(self._settings_page, "show_message"):
            self._settings_page.show_message(text)

    def show_settings(self) -> None:
        """切换到设置页。"""
        self._switch_page(2)
        logger.info("打开设置页")
