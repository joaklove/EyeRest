"""Dashboard 首页。

EyeRest 主窗口的中心页面，实时展示：

* 当前保护状态（活跃 / 暂停 / 休息中）
* 今日有效用眼时间与应用运行时间
* 下一次休息倒计时与工作进度条
* 今日休息统计（完成 / 跳过 / 自然休息）
* 底部快捷操作（立即休息 / 暂停 30 分钟 / 重置计时）

页面通过 ``update_display()`` 每秒由外部 QTimer 调用刷新数据，
并订阅 ``STATE_CHANGED`` 事件实现状态变化时的 UI 联动。
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import defaults
from app.core.event_bus import EventBus, EventType
from app.core.state_machine import AppState
from app.i18n import get_translator, tr
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 颜色常量
# ---------------------------------------------------------------------------
COLOR_PRIMARY = "#4CAF50"       # 健康绿
COLOR_WARNING = "#FF9800"       # 警告橙
COLOR_DANGER = "#F44336"        # 危险红
COLOR_GRAY = "#9E9E9E"          # 中性灰
COLOR_TEXT = "#212121"          # 主文字
COLOR_TEXT_SECONDARY = "#757575"  # 次文字
COLOR_CARD_BG = "#FFFFFF"       # 卡片背景
COLOR_PAGE_BG = "#F5F5F5"       # 页面背景
COLOR_PROGRESS_BG = "#E0E0E0"   # 进度条背景


class _CardFrame(QFrame):
    """白色圆角卡片容器。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"""
            QFrame#card {{
                background-color: {COLOR_CARD_BG};
                border-radius: 12px;
                border: 1px solid #E8E8E8;
            }}
            """
        )
        # 卡片轻微阴影（Qt 不直接支持 CSS box-shadow，使用 GraphicsDropShadowEffect）
        try:
            from PySide6.QtWidgets import QGraphicsDropShadowEffect

            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(16)
            shadow.setOffset(0, 2)
            shadow.setColor(Qt.GlobalColor.transparent)  # 占位，下方设置
            from PySide6.QtGui import QColor

            shadow.setColor(QColor(0, 0, 0, 25))
            self.setGraphicsEffect(shadow)
        except Exception:  # noqa: BLE001
            pass


class Dashboard(QWidget):
    """Dashboard 首页。

    接收核心引擎与服务实例，构建 UI 并提供每秒刷新的 ``update_display``
    方法。状态变化通过 :class:`~app.core.event_bus.EventBus` 联动。

    Signals:
        quick_break_requested: 用户点击"立即休息"时发射。
        pause_30m_requested: 用户点击"暂停 30 分钟"时发射。
        reset_requested: 用户点击"重置计时"时发射。
    """

    quick_break_requested = Signal()
    pause_30m_requested = Signal()
    reset_requested = Signal()

    def __init__(
        self,
        timer_engine: Any,
        break_engine: Any,
        state_machine: Any,
        usage_service: Any,
        event_bus: Optional[EventBus] = None,
        parent: Optional[QWidget] = None,
        screen_session_engine: Any = None,
        blink_engine: Any = None,
        move_engine: Any = None,
        blink_stats_provider: Any = None,
    ) -> None:
        """初始化 Dashboard。

        Args:
            timer_engine: 计时引擎实例。
            break_engine: 休息引擎实例。
            state_machine: 状态机实例。
            usage_service: 使用统计服务实例。
            event_bus: 事件总线；为 None 时不订阅状态变化。
            parent: 父窗口部件。
            screen_session_engine: V1.0 屏幕暴露会话引擎；提供时主数字改为
                **屏幕暴露时长**（键鼠空闲不再中断用眼计时）。
            blink_engine: V1.0 眨眼节奏引擎；提供时展示节奏卡片。
            move_engine: V1.0 活动提醒引擎；提供时节奏卡片包含活动行。
            blink_stats_provider: 可选回调，返回 (今日眨眼提示次数, 完成次数)。
        """
        super().__init__(parent)
        self._timer = timer_engine
        self._break = break_engine
        self._state_machine = state_machine
        self._usage = usage_service
        self._bus = event_bus
        #: V1.0 屏幕暴露会话引擎（可为 None，向后兼容旧装配方式）
        self._session = screen_session_engine
        #: V1.0 眨眼节奏引擎（可为 None）
        self._blink = blink_engine
        #: V1.0 活动提醒引擎（可为 None）
        self._move = move_engine
        #: 今日眨眼提示统计回调（可为 None）
        self._blink_stats = blink_stats_provider

        # 三层节奏卡片控件（未装配 V1.0 引擎时不构建，这里先置空保证安全）
        self._rhythm_title: Optional[QLabel] = None
        self._rhythm_rows: dict[str, dict] = {}

        # 休息统计缓存（自然休息次数无直接引擎接口，从摘要推导）
        self._natural_rest_count: int = 0

        self.setup_ui()
        self._subscribe_events()
        self.update_display()
        # 语言切换时刷新静态文本
        get_translator().add_listener(self._on_language_changed)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self) -> None:
        """构建整体 UI：滚动区域 + 纵向卡片列表。"""
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {COLOR_PAGE_BG};
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                color: {COLOR_TEXT};
            }}
            QScrollArea {{
                border: none;
                background-color: {COLOR_PAGE_BG};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0;
            }}
            QScrollBar::handle:vertical {{
                background: #C8C8C8;
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #A8A8A8;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                height: 0;
            }}
            """
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setStyleSheet(f"background-color: {COLOR_PAGE_BG};")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(20, 20, 20, 20)
        self._content_layout.setSpacing(16)

        # 依次构建各卡片
        self._build_status_card()
        if self._blink is not None or self._session is not None:
            self._build_rhythm_card()
        self._build_active_time_card()
        self._build_break_countdown_card()
        self._build_today_stats_card()
        self._build_quick_actions()

        # 底部留白，避免快捷操作贴底
        self._content_layout.addSpacing(8)
        self._content_layout.addStretch(1)
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

    def _build_status_card(self) -> None:
        """1. 顶部状态卡片：圆点 + 状态文字。"""
        card = _CardFrame()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 状态圆点
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color: {COLOR_PRIMARY}; font-size: 24px;")
        self._status_dot.setFixedSize(28, 28)
        self._status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 状态文字
        self._status_label = QLabel(tr("state.active"))
        self._status_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {COLOR_TEXT};"
        )

        layout.addWidget(self._status_dot)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

        self._content_layout.addWidget(card)

    def _build_rhythm_card(self) -> None:
        """1.5 V1.0 三层护眼节奏卡片：眨眼 / 远眺 / 长休息。

        三层各自独立倒计时，让用户一眼看清"下一次该做什么"，
        而不是只看到一个孤零零的 20 分钟。
        """
        card = _CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        self._rhythm_title = QLabel(tr("dashboard.rhythm_title"))
        self._rhythm_title.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {COLOR_TEXT_SECONDARY};"
        )
        layout.addWidget(self._rhythm_title)

        self._rhythm_rows: dict[str, dict] = {}
        for key, color, i18n_key in (
            ("blink", "#2196F3", "dashboard.rhythm_blink"),
            ("short", COLOR_PRIMARY, "dashboard.rhythm_short"),
            ("move", "#FF7043", "dashboard.rhythm_move"),
            ("long", "#9C27B0", "dashboard.rhythm_long"),
        ):
            self._rhythm_rows[key] = self._create_rhythm_row(
                layout, tr(i18n_key), color
            )

        self._content_layout.addWidget(card)

    def _create_rhythm_row(
        self, parent_layout: QVBoxLayout, title: str, color: str
    ) -> dict:
        """创建一行节奏项：标题 + 倒计时 + 细进度条。"""
        row = QWidget()
        row.setStyleSheet("background-color: transparent;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)

        name_label = QLabel(title)
        name_label.setFixedWidth(56)
        name_label.setStyleSheet(f"font-size: 13px; color: {COLOR_TEXT};")

        value_label = QLabel("--:--")
        value_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};"
        )
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            f"""
            QProgressBar {{
                background-color: {COLOR_PROGRESS_BG};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 3px;
            }}
            """
        )

        row_layout.addWidget(name_label)
        row_layout.addWidget(bar, 1)
        row_layout.addWidget(value_label)
        parent_layout.addWidget(row)

        return {"container": row, "value": value_label, "bar": bar, "title": name_label}

    def _build_active_time_card(self) -> None:
        """2. 有效用眼时间卡片。"""
        card = _CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # 大号数字
        self._active_time_label = QLabel("00:00:00")
        font = QFont()
        font.setPointSize(36)
        font.setBold(True)
        self._active_time_label.setFont(font)
        self._active_time_label.setStyleSheet(f"color: {COLOR_PRIMARY};")
        self._active_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 标签（V1.0 装配了会话引擎时改为屏幕暴露时长）
        self._active_time_title = QLabel(
            tr("dashboard.exposure_time_title")
            if self._session is not None
            else tr("dashboard.active_time_title")
        )
        self._active_time_title.setStyleSheet(
            f"font-size: 14px; color: {COLOR_TEXT_SECONDARY};"
        )
        self._active_time_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 下方小字：应用运行时间
        self._uptime_label = QLabel(tr("dashboard.uptime_format", time="00:00:00"))
        self._uptime_label.setStyleSheet(
            f"font-size: 12px; color: {COLOR_GRAY};"
        )
        self._uptime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._active_time_label)
        layout.addWidget(self._active_time_title)
        layout.addWidget(self._uptime_label)

        self._content_layout.addWidget(card)

    def _build_break_countdown_card(self) -> None:
        """3. 下一次休息倒计时卡片。"""
        card = _CardFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)

        # 大号倒计时
        self._countdown_label = QLabel("20:00")
        font = QFont()
        font.setPointSize(32)
        font.setBold(True)
        self._countdown_label.setFont(font)
        self._countdown_label.setStyleSheet(f"color: {COLOR_TEXT};")
        self._countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 标签
        self._countdown_title = QLabel(tr("dashboard.next_break_title"))
        self._countdown_title.setStyleSheet(
            f"font-size: 14px; color: {COLOR_TEXT_SECONDARY};"
        )
        self._countdown_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setStyleSheet(
            f"""
            QProgressBar {{
                background-color: {COLOR_PROGRESS_BG};
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {COLOR_PRIMARY};
                border-radius: 4px;
            }}
            """
        )

        layout.addWidget(self._countdown_label)
        layout.addWidget(self._countdown_title)
        layout.addSpacing(4)
        layout.addWidget(self._progress_bar)

        self._content_layout.addWidget(card)

    def _build_today_stats_card(self) -> None:
        """4. 今日休息统计卡片。"""
        card = _CardFrame()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # 已休息（远眺完成）
        self._break_count_label = self._create_stat_block(
            "dashboard.stat_completed", "0", COLOR_PRIMARY
        )
        # 跳过
        self._skipped_label = self._create_stat_block(
            "dashboard.stat_skipped", "0", COLOR_WARNING
        )
        # 眨眼提示（V1.0 数据诚实口径：提示次数，不是眨眼次数）
        self._natural_label = self._create_stat_block(
            "dashboard.stat_blink_cues" if self._blink_stats else "dashboard.stat_natural",
            "0",
            COLOR_GRAY,
        )

        layout.addWidget(self._break_count_label["container"])
        layout.addWidget(self._skipped_label["container"])
        layout.addWidget(self._natural_label["container"])

        self._content_layout.addWidget(card)

    def _create_stat_block(
        self, title_key: str, value: str, color: str
    ) -> dict:
        """创建一个统计数字块（标题通过 i18n 键翻译）。"""
        container = QWidget()
        vlay = QVBoxLayout(container)
        vlay.setContentsMargins(4, 4, 4, 4)
        vlay.setSpacing(2)

        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(22)
        font.setBold(True)
        value_label.setFont(font)
        value_label.setStyleSheet(f"color: {color};")

        title_label = QLabel(tr(title_key))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(
            f"font-size: 12px; color: {COLOR_TEXT_SECONDARY};"
        )

        vlay.addWidget(value_label)
        vlay.addWidget(title_label)

        return {
            "container": container,
            "value": value_label,
            "title": title_label,
            "key": title_key,
        }

    def _build_quick_actions(self) -> None:
        """5. 底部快捷操作按钮。"""
        # 用容器包裹，确保快捷操作有独立的可伸缩水平空间。
        # 关键：container 自身必须声明 Expanding，否则内层 stretch=1
        # 也只在容器内部平均分配——容器本身只取按钮合起来的最小宽度，
        # 看起来就挤在左下角。
        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._btn_quick_break = QPushButton(tr("common.rest_now"))
        self._btn_quick_break.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quick_break.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_quick_break.setMinimumHeight(44)
        self._btn_quick_break.setStyleSheet(self._button_style(COLOR_PRIMARY))
        self._btn_quick_break.clicked.connect(self.quick_break_requested.emit)

        self._btn_pause = QPushButton(tr("common.pause_30m"))
        self._btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_pause.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_pause.setMinimumHeight(44)
        self._btn_pause.setStyleSheet(self._button_style(COLOR_WARNING))
        self._btn_pause.clicked.connect(self.pause_30m_requested.emit)

        self._btn_reset = QPushButton(tr("dashboard.reset_timer"))
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._btn_reset.setMinimumHeight(44)
        self._btn_reset.setStyleSheet(self._button_style(COLOR_GRAY, outline=True))
        self._btn_reset.clicked.connect(self.reset_requested.emit)

        layout.addWidget(self._btn_quick_break, 1)
        layout.addWidget(self._btn_pause, 1)
        layout.addWidget(self._btn_reset, 1)

        self._content_layout.addWidget(container)

    @staticmethod
    def _button_style(color: str, outline: bool = False) -> str:
        """生成按钮样式表。"""
        if outline:
            return f"""
            QPushButton {{
                background-color: transparent;
                color: {COLOR_TEXT_SECONDARY};
                border: 1px solid #D0D0D0;
                border-radius: 8px;
                padding: 10px 16px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #F0F0F0;
                border-color: {color};
            }}
            QPushButton:pressed {{
                background-color: #E0E0E0;
            }}
            """
        return f"""
        QPushButton {{
            background-color: {color};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 10px 16px;
            font-size: 14px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background-color: {color}DD;
        }}
        QPushButton:pressed {{
            background-color: {color}BB;
        }}
        QPushButton:disabled {{
            background-color: {COLOR_GRAY};
            color: #FFFFFF;
        }}
        """

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------
    def _subscribe_events(self) -> None:
        """订阅状态变化事件。"""
        if self._bus is None:
            return
        try:
            self._bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("订阅 STATE_CHANGED 事件失败")

    def _unsubscribe_events(self) -> None:
        """取消事件订阅。"""
        if self._bus is None:
            return
        try:
            self._bus.unsubscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("取消 STATE_CHANGED 订阅失败")

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def update_display(self) -> None:
        """更新显示数据（每秒调用）。

        从各引擎与服务读取最新数据，刷新所有 UI 控件。
        """
        # 状态
        self._refresh_status()

        # 有效用眼时间（V1.0：装配了会话引擎时显示屏幕暴露时长）
        active_seconds = self._safe_get_active_seconds()
        self._active_time_label.setText(self._usage.format_duration(active_seconds))

        # 应用运行时间
        uptime = self._safe_get_app_uptime()
        self._uptime_label.setText(
            tr("dashboard.uptime_format", time=self._usage.format_duration(uptime))
        )

        # V1.0 三层护眼节奏
        self._refresh_rhythm()

        # 下一次休息倒计时
        self._refresh_countdown(active_seconds)

        # 今日统计
        self._refresh_today_stats()

    def _refresh_status(self) -> None:
        """刷新顶部状态卡片。"""
        state_name = self._safe_get_state_name()
        state_text, color = self._state_display(state_name)
        self._status_label.setText(state_text)
        self._status_dot.setStyleSheet(
            f"color: {color}; font-size: 24px;"
        )

    def _state_display(self, state_name: str) -> tuple[str, str]:
        """根据状态名返回 (显示文字, 颜色)，文字经 i18n 翻译。"""
        mapping = {
            "ACTIVE": ("state.active", COLOR_PRIMARY),
            "IDLE": ("state.idle", COLOR_WARNING),
            "BREAK_WARNING": ("state.break_warning", COLOR_WARNING),
            "SHORT_BREAK": ("state.short_break", "#2196F3"),
            "LONG_BREAK": ("state.long_break", "#2196F3"),
            "PAUSED": ("state.paused", COLOR_GRAY),
            "LOCKED": ("state.locked", COLOR_GRAY),
            "SLEEP": ("state.sleep", COLOR_GRAY),
            "INACTIVE": ("state.inactive", COLOR_GRAY),
        }
        key, color = mapping.get(state_name, ("state.active", COLOR_PRIMARY))
        return tr(key), color

    def _refresh_rhythm(self) -> None:
        """刷新四层护眼节奏（眨眼 / 远眺 / 活动 / 深度休息）。"""
        if not self._rhythm_rows:
            return

        state = self._safe_session_state()

        blink_remaining = 0.0
        blink_total = 60.0
        if self._blink is not None:
            try:
                blink_remaining = float(self._blink.seconds_until_next_cue())
                blink_total = max(1.0, float(self._blink.cue_interval))
            except Exception:  # noqa: BLE001
                logger.exception("读取眨眼倒计时失败")

        short_remaining = self._safe_get_remaining_seconds()
        long_remaining = self._safe_get_long_remaining_seconds()
        long_total = max(long_remaining, float(self._safe_get_long_work_duration()))

        move_remaining = None
        move_total = 0.0
        if self._move is not None:
            try:
                move_remaining = float(self._move.get_remaining_seconds())
                move_total = max(1.0, float(self._move.interval))
            except Exception:  # noqa: BLE001
                move_remaining = None

        self._set_rhythm_row("blink", blink_remaining, blink_total, state)
        self._set_rhythm_row(
            "short",
            short_remaining,
            short_remaining + self._safe_get_active_seconds(),
            state,
        )
        if move_remaining is not None:
            self._set_rhythm_row("move", move_remaining, move_total, state)
        self._set_rhythm_row("long", long_remaining, long_total, state)

    def _set_rhythm_row(
        self, key: str, remaining: float, total: float, state: str
    ) -> None:
        """更新一行节奏项。

        Args:
            key: 行标识 ``blink`` / ``short`` / ``long``。
            remaining: 剩余秒数。
            total: 该层一个周期的总秒数（用于进度条）。
            state: 屏幕暴露会话状态 ``ON`` / ``AWAY`` / ``OFF``。
        """
        row = self._rhythm_rows.get(key)
        if row is None:
            return

        if state == "AWAY":
            row["value"].setText(tr("dashboard.rhythm_away"))
            return
        if state == "OFF":
            row["value"].setText(tr("dashboard.rhythm_off"))
            row["bar"].setValue(0)
            return

        row["value"].setText(self._usage.format_short_duration(remaining))
        if total > 0:
            progress = int(max(0.0, min(1.0, 1.0 - remaining / total)) * 100)
            row["bar"].setValue(progress)

    def _refresh_countdown(self, active_seconds: float) -> None:
        """刷新下一次休息倒计时与进度条。"""
        remaining = self._safe_get_remaining_seconds()
        # 进度 = active / (active + remaining)，避免除零
        total = active_seconds + remaining
        progress = 0
        if total > 0:
            progress = min(100, int((active_seconds / total) * 100))

        self._progress_bar.setValue(progress)
        self._countdown_label.setText(self._usage.format_short_duration(remaining))

        # 休息进行中时，倒计时文案改为"休息中"
        state_name = self._safe_get_state_name()
        if state_name in ("SHORT_BREAK", "LONG_BREAK"):
            self._countdown_title.setText(tr("dashboard.break_in_progress"))
            self._countdown_label.setText(tr("dashboard.breaking"))
        elif state_name == "BREAK_WARNING":
            self._countdown_title.setText(tr("dashboard.break_upcoming"))
        else:
            self._countdown_title.setText(tr("dashboard.next_break_title"))

    def _refresh_today_stats(self) -> None:
        """刷新今日休息统计。"""
        summary = {}
        try:
            summary = self._usage.get_today_summary()
        except Exception:  # noqa: BLE001
            logger.exception("获取今日摘要失败")

        break_count = int(summary.get("break_count", 0))
        skipped = int(summary.get("skipped_breaks", 0))
        # 第三个统计块：装配了眨眼统计回调时显示"眨眼提示次数"，
        # 否则回退为自然休息近似值（旧装配方式）
        third_value: str
        if self._blink_stats is not None:
            try:
                stats_result = self._blink_stats()
                # 兼容两种回调：返回 int（JSON 统计）或 (总数, 完成数) 元组
                if isinstance(stats_result, (tuple, list)):
                    cue_total = stats_result[0]
                else:
                    cue_total = stats_result
                third_value = str(int(cue_total))
            except Exception:  # noqa: BLE001
                logger.exception("获取眨眼提示统计失败")
                third_value = "0"
        else:
            third_value = str(max(0, self._natural_rest_count))

        self._break_count_label["value"].setText(str(break_count))
        self._skipped_label["value"].setText(str(skipped))
        self._natural_label["value"].setText(third_value)

    # ------------------------------------------------------------------
    # 事件回调
    # ------------------------------------------------------------------
    def _on_state_changed(self, payload: Any) -> None:
        """状态变化时更新 UI。

        Args:
            payload: STATE_CHANGED 事件数据，含 new_state 字段。
        """
        try:
            state_name = "ACTIVE"
            if isinstance(payload, dict):
                state_name = payload.get("new_state", "ACTIVE")
            logger.debug("状态变化事件: %s", state_name)
            self.update_display()
        except Exception:  # noqa: BLE001
            logger.exception("处理状态变化事件失败")

    # ------------------------------------------------------------------
    # 容错辅助
    # ------------------------------------------------------------------
    def _safe_get_state_name(self) -> str:
        """安全获取当前状态名。"""
        if self._state_machine is None:
            return "ACTIVE"
        try:
            state = self._state_machine.get_state()
            if hasattr(state, "name"):
                return state.name
            return str(state)
        except Exception:  # noqa: BLE001
            logger.exception("获取状态失败")
            return "ACTIVE"

    def _safe_get_active_seconds(self) -> float:
        """已用时长。

        V1.0：装配了屏幕暴露会话引擎时优先返回 **屏幕暴露时长**
        （键鼠空闲不再中断用眼计时），否则回退 TimerEngine。
        """
        if self._session is not None:
            try:
                return max(0.0, float(self._session.exposure_seconds))
            except Exception:  # noqa: BLE001
                logger.exception("读取屏幕暴露时长失败，回退 TimerEngine")
        if self._timer is None:
            return 0.0
        try:
            return float(self._timer.get_active_seconds())
        except Exception:  # noqa: BLE001
            return 0.0

    def _safe_session_state(self) -> str:
        """屏幕暴露会话状态名（``ON`` / ``AWAY`` / ``OFF``）；无引擎时返回 ON。"""
        if self._session is None:
            return "ON"
        try:
            state = self._session.state
            return getattr(state, "name", str(state)).upper()
        except Exception:  # noqa: BLE001
            return "ON"

    def _safe_get_long_remaining_seconds(self) -> float:
        if self._break is None:
            return float(defaults.LONG_WORK_DURATION)
        try:
            return float(self._break.get_long_remaining_seconds())
        except Exception:  # noqa: BLE001
            return float(defaults.LONG_WORK_DURATION)

    def _safe_get_long_work_duration(self) -> float:
        if self._break is None:
            return float(defaults.LONG_WORK_DURATION)
        getter = getattr(self._break, "get_long_work_duration", None)
        if getter is None or not callable(getter):
            return float(defaults.LONG_WORK_DURATION)
        try:
            return max(1.0, float(getter()))
        except Exception:  # noqa: BLE001
            return float(defaults.LONG_WORK_DURATION)

    def _safe_get_app_uptime(self) -> float:
        if self._timer is None:
            return 0.0
        try:
            return float(self._timer.get_app_uptime())
        except Exception:  # noqa: BLE001
            return 0.0

    def _safe_get_remaining_seconds(self) -> float:
        if self._break is None:
            return float(defaults.SHORT_WORK_DURATION)
        try:
            return float(self._break.get_remaining_seconds())
        except Exception:  # noqa: BLE001
            return float(defaults.SHORT_WORK_DURATION)

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """语言变化时重新设置所有静态文本。"""
        self._btn_quick_break.setText(tr("common.rest_now"))
        self._btn_pause.setText(tr("common.pause_30m"))
        self._btn_reset.setText(tr("dashboard.reset_timer"))
        self._active_time_title.setText(
            tr("dashboard.exposure_time_title")
            if self._session is not None
            else tr("dashboard.active_time_title")
        )
        if self._rhythm_title is not None:
            self._rhythm_title.setText(tr("dashboard.rhythm_title"))
            for key, i18n_key in (
                ("blink", "dashboard.rhythm_blink"),
                ("short", "dashboard.rhythm_short"),
                ("move", "dashboard.rhythm_move"),
                ("long", "dashboard.rhythm_long"),
            ):
                row = self._rhythm_rows.get(key)
                if row is not None:
                    row["title"].setText(tr(i18n_key))
        for block in (self._break_count_label, self._skipped_label, self._natural_label):
            block["title"].setText(tr(block["key"]))
        # 动态文本（状态 / 倒计时 / 运行时间）随刷新重新取值
        self.update_display()

    def _on_language_changed(self, lang: str) -> None:
        """语言变化监听回调。"""
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """释放资源（取消事件订阅与语言监听）。"""
        self._unsubscribe_events()
        get_translator().remove_listener(self._on_language_changed)
