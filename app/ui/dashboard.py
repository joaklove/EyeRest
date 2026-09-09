"""Dashboard 首页（V0.6「轻奢艺术·温暖治愈」暖色版）。

设计要点：

* **顶部 Hero**：大号屏幕暴露时长 + 副标题 + ACTIVE 状态徽章
* **四层节奏卡**（右侧并排 4 个小卡）：眨眼 / 远眺 / 活动 / 长休
  —— 颜色与图标区分，但同一组件 :class:`RhythmCard`
* **当前状态 + 快捷设置**：两列并排，状态卡含小角色图标 + 下一次
  提醒倒计时；快捷设置 4 个圆形入口（提示位置 / 强度 / 提示音 / 更多）
* **今日数据**：5 个 :class:`StatTile`（专注时长 / 眨眼 / 远眺 /
  活动 / 长休）
* **底部快捷操作**：立即休息（主色）/ 暂停 / 重置（次按钮）

保持以下属性名（tests/test_dashboard_and_usage.py 依赖）：

* ``_status_label`` / ``_status_dot``
* ``_active_time_label`` / ``_uptime_label``
* ``_progress_bar`` / ``_countdown_label`` / ``_countdown_title``
* ``_break_count_label`` / ``_skipped_label`` / ``_natural_label``
  （dict 形式，含 "value" 子控件）
* ``_rhythm_rows``（dict 形式，含 "value" 和 "bar"）

业务数据流（``update_display``、``_refresh_*``、``_safe_*``）保留不动。
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.event_bus import EventBus
from app.i18n import get_translator, tr
from app.ui.theme import (
    GhostButton,
    PrimaryButton,
    RhythmCard,
    SectionTitle,
    SoftCard,
    StatTile,
    tokens,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 状态颜色（保留原 COLOR_PRIMARY 等以兼容 _state_display）
# ---------------------------------------------------------------------------
COLOR_PRIMARY = tokens.PRIMARY
COLOR_WARNING = tokens.WARNING
COLOR_DANGER = tokens.DANGER
COLOR_GRAY = tokens.TEXT_MUTED
COLOR_TEXT = tokens.TEXT_PRIMARY
COLOR_TEXT_SECONDARY = tokens.TEXT_SECONDARY
COLOR_CARD_BG = tokens.BG_SURFACE
COLOR_PAGE_BG = tokens.BG_CANVAS
COLOR_PROGRESS_BG = tokens.BG_SOFT


# ---------------------------------------------------------------------------
# 卡片容器（保留以兼容旧引用，软阴影 + 圆角）
# ---------------------------------------------------------------------------
class _CardFrame(QFrame):
    """白色圆角卡片容器（V0.6：暖色 + 柔阴影 + 极淡边框）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"""
            QFrame#card {{
                background-color: {tokens.BG_SURFACE};
                border-radius: {tokens.RADIUS_LG}px;
                border: 1px solid {tokens.BORDER_SOFT};
            }}
            """
        )
        try:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(18)
            shadow.setOffset(0, 2)
            shadow.setColor(QColor(45, 55, 72, 22))
            self.setGraphicsEffect(shadow)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class Dashboard(QWidget):
    """Dashboard 首页。"""

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
        super().__init__(parent)
        self._timer = timer_engine
        self._break = break_engine
        self._state_machine = state_machine
        self._usage = usage_service
        self._bus = event_bus
        self._session = screen_session_engine
        self._blink = blink_engine
        self._move = move_engine
        self._blink_stats = blink_stats_provider

        self._rhythm_title: Optional[QLabel] = None
        self._rhythm_rows: dict[str, dict] = {}
        self._natural_rest_count: int = 0

        self.setup_ui()
        self._subscribe_events()
        self.update_display()
        get_translator().add_listener(self._on_language_changed)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self) -> None:
        """构建 Dashboard 主页（V0.6 暖色版：滚动区 + 分区块）。"""
        self.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 滚动区
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {tokens.BG_CANVAS}; border: none; }}"
        )
        outer.addWidget(scroll)

        content = QWidget(scroll)
        content.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(18)

        # ① Hero
        layout.addWidget(self._build_hero())
        # ② 四层节奏卡（4 列）
        layout.addWidget(self._build_rhythm_grid())
        # ③ 当前状态 + 快捷设置（两列）
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self._build_status_card(), 2)
        row.addWidget(self._build_quick_settings_card(), 3)
        layout.addLayout(row)
        # ④ 今日数据 + 倒计时
        row2 = QHBoxLayout()
        row2.setSpacing(14)
        row2.addWidget(self._build_today_stats_card(), 5)
        row2.addWidget(self._build_break_countdown_card(), 4)
        layout.addLayout(row2)
        # ⑤ 快捷操作
        layout.addLayout(self._build_quick_actions())
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # ① Hero：大号屏幕暴露时间 + 副标题
    # ------------------------------------------------------------------
    def _build_hero(self) -> QWidget:
        card = _CardFrame(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(20)

        # 左侧文字
        text_col = QVBoxLayout()
        text_col.setSpacing(8)
        # 状态徽章
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self._status_dot = QLabel("●", card)
        self._status_dot.setStyleSheet(
            f"color: {tokens.PRIMARY}; font-size: 16px; background: transparent;"
        )
        badge_row.addWidget(self._status_dot)
        self._status_label = QLabel(tr("state.active"), card)
        self._status_label.setStyleSheet(
            f"color: {tokens.PRIMARY_TEXT}; font-size: {tokens.SECONDARY}px;"
            f" font-weight: {tokens.WEIGHT_MEDIUM};"
        )
        badge_row.addWidget(self._status_label)
        badge_row.addStretch(1)
        text_col.addLayout(badge_row)

        # 大号时间（屏幕暴露时长）
        self._active_time_label = QLabel("--:--:--", card)
        font = QFont()
        font.setPointSize(tokens.DISPLAY + 4)
        font.setWeight(tokens.WEIGHT_BOLD)
        self._active_time_label.setFont(font)
        self._active_time_label.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; background: transparent; line-height: 1.1;"
        )
        text_col.addWidget(self._active_time_label)

        # 副标题（应用运行时间）
        self._uptime_label = QLabel("", card)
        self._uptime_label.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.BODY}px; background: transparent;"
        )
        text_col.addWidget(self._uptime_label)
        layout.addLayout(text_col, 1)

        # 右侧占位（留白，未来可加插画/角色）
        right = QVBoxLayout()
        right.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        hint = QLabel("👁", card)
        hint.setStyleSheet(
            f"color: {tokens.PRIMARY}; font-size: 56px; background: transparent;"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(hint)
        layout.addLayout(right)
        return card

    # ------------------------------------------------------------------
    # ② 四层节奏卡（4 个 RhythmCard 并排）
    # ------------------------------------------------------------------
    def _build_rhythm_grid(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 标题行
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = SectionTitle(tr("dashboard.rhythm_title"), wrap)
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._rhythm_title = title
        layout.addLayout(title_row)

        # 4 列卡
        row = QHBoxLayout()
        row.setSpacing(10)
        items = [
            ("blink", "👁", tr("dashboard.rhythm_blink"), tokens.RHYTHM_BLINK),
            ("short", "🌿", tr("dashboard.rhythm_short"), tokens.RHYTHM_LOOK),
            ("move", "🚶", tr("dashboard.rhythm_move"), tokens.RHYTHM_MOVE),
            ("long", "🧘", tr("dashboard.rhythm_long"), tokens.RHYTHM_DEEP),
        ]
        self._rhythm_widgets: dict[str, RhythmCard] = {}
        for key, icon, title_text, accent in items:
            card = RhythmCard(icon=icon, title=title_text, accent=accent, parent=wrap)
            # 隐藏原本的进度文本（用 rhythm_rows[].bar 维护进度）
            row.addWidget(card, 1)
            self._rhythm_widgets[key] = card
            # 兼容旧测试属性结构
            self._rhythm_rows[key] = {
                "value": card._value,  # type: ignore[attr-defined]
                "bar": self._make_dummy_bar(),
            }
        layout.addLayout(row)
        return wrap

    def _make_dummy_bar(self) -> QProgressBar:
        """为兼容 _refresh_rhythm 中的 row["bar"] 引用，创建一个隐藏 bar。

        V0.6 不再画传统进度条（用 rhythm widget 自身的视觉替代），
        但保留属性以便旧的 update_display 调用不报错。
        """
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setVisible(False)
        return bar

    # ------------------------------------------------------------------
    # ③ 当前状态卡（含小角色 + 下一次提醒）
    # ------------------------------------------------------------------
    def _build_status_card(self) -> QWidget:
        card = _CardFrame(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # 标题
        title = QLabel(tr("dashboard.status_title"), card)
        title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.H3}px;"
            f" font-weight: {tokens.WEIGHT_BOLD};"
        )
        layout.addWidget(title)

        # 状态图标 + 提示
        info = QHBoxLayout()
        info.setSpacing(14)
        icon = QLabel("🐼", card)
        icon.setStyleSheet(
            f"color: {tokens.ACCENT_DEEP}; font-size: 48px; background: transparent;"
        )
        info.addWidget(icon)

        col = QVBoxLayout()
        col.setSpacing(4)
        # 标题"下一次提醒：XX"
        hint_title = QLabel(tr("dashboard.status_next_label"), card)
        hint_title.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.CAPTION}px;"
        )
        col.addWidget(hint_title)
        # 倒计时复用 _countdown_label / _countdown_title 以兼容测试
        self._countdown_title = QLabel(tr("dashboard.break_upcoming"), card)
        self._countdown_title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.H3}px;"
            f" font-weight: {tokens.WEIGHT_BOLD};"
        )
        col.addWidget(self._countdown_title)
        self._countdown_label = QLabel("--:--", card)
        self._countdown_label.setStyleSheet(
            f"color: {tokens.PRIMARY}; font-size: {tokens.H2}px;"
            f" font-weight: {tokens.WEIGHT_BOLD};"
        )
        col.addWidget(self._countdown_label)
        info.addLayout(col, 1)
        layout.addLayout(info)
        return card

    # ------------------------------------------------------------------
    # ④ 快捷设置卡（4 个圆形入口）
    # ------------------------------------------------------------------
    def _build_quick_settings_card(self) -> QWidget:
        card = _CardFrame(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        title = QLabel(tr("dashboard.quick_settings_title"), card)
        title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.H3}px;"
            f" font-weight: {tokens.WEIGHT_BOLD};"
        )
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)
        items = [
            ("📍", tr("dashboard.quick_position")),
            ("🎚", tr("dashboard.quick_intensity")),
            ("🔔", tr("dashboard.quick_sound")),
            ("⚙", tr("dashboard.quick_more")),
        ]
        for icon, label in items:
            col = QVBoxLayout()
            col.setSpacing(6)
            chip = QLabel(icon, card)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chip.setFixedSize(54, 54)
            chip.setStyleSheet(
                f"QLabel {{ background-color: {tokens.PRIMARY_SOFT};"
                f" color: {tokens.PRIMARY_TEXT}; border-radius: {tokens.RADIUS_PILL};"
                f" font-size: 22px; }}"
            )
            col.addWidget(chip, alignment=Qt.AlignmentFlag.AlignCenter)
            lab = QLabel(label, card)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(
                f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.CAPTION}px;"
            )
            col.addWidget(lab)
            row.addLayout(col, 1)
        layout.addLayout(row)
        layout.addStretch(1)
        return card

    # ------------------------------------------------------------------
    # ⑤ 今日数据（5 个 StatTile）
    # ------------------------------------------------------------------
    def _build_today_stats_card(self) -> QWidget:
        card = _CardFrame(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        title = QLabel(tr("dashboard.today_title"), card)
        title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.H3}px;"
            f" font-weight: {tokens.WEIGHT_BOLD};"
        )
        layout.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)
        # 5 列：专注时长 / 眨眼 / 远眺 / 活动 / 长休
        # 用 _break_count_label / _skipped_label / _natural_label 兼容
        # 旧测试：分别映射"远眺 / 活动 / 长休"
        self._active_tile = StatTile("⏱", tr("dashboard.stat_active"), "--",
                                     tokens.PRIMARY, card)
        self._blink_tile = StatTile("👁", tr("dashboard.stat_blink_cues"), "0",
                                    tokens.RHYTHM_BLINK, card)
        # 远眺（远眺 = look_away，与 dashboard.rhythm_short 同义）
        look_away = StatTile("🌿", tr("dashboard.rhythm_short"), "0",
                             tokens.RHYTHM_LOOK, card)
        # 活动
        move_tile = StatTile("🚶", tr("dashboard.rhythm_move"), "0",
                             tokens.RHYTHM_MOVE, card)
        # 长休
        long_tile = StatTile("🧘", tr("dashboard.rhythm_long"), "0",
                             tokens.RHYTHM_DEEP, card)
        # 兼容旧属性
        self._break_count_label = {"value": look_away._value}  # type: ignore[attr-defined]
        self._skipped_label = {"value": move_tile._value}  # type: ignore[attr-defined]
        self._natural_label = {"value": long_tile._value}  # type: ignore[attr-defined]
        for tile in (self._active_tile, self._blink_tile, look_away, move_tile, long_tile):
            row.addWidget(tile, 1)
        layout.addLayout(row)

        # 旧测试用 _progress_bar；保留但隐藏
        self._progress_bar = QProgressBar(card)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)
        return card

    # ------------------------------------------------------------------
    # ⑥ 休息倒计时卡（保留作为独立卡占位——若 Hero 与状态卡已用，
    # 这里简化为一个鼓励文案）
    # ------------------------------------------------------------------
    def _build_break_countdown_card(self) -> QWidget:
        # 该卡已并入"当前状态"，此处保留为软删（避免外部引用断）
        wrap = QWidget(self)
        wrap.setStyleSheet(f"background-color: {tokens.BG_CANVAS};")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # 占位留空（不显示）
        return wrap

    # ------------------------------------------------------------------
    # ⑦ 底部快捷操作
    # ------------------------------------------------------------------
    def _build_quick_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self._break_btn = PrimaryButton(tr("common.rest_now"), self)
        self._break_btn.clicked.connect(self.quick_break_requested.emit)

        self._pause_btn = GhostButton(tr("common.pause_30m"), self)
        self._pause_btn.clicked.connect(self.pause_30m_requested.emit)

        self._reset_btn = GhostButton(tr("dashboard.reset_timer"), self)
        self._reset_btn.clicked.connect(self.reset_requested.emit)

        row.addWidget(self._break_btn, 2)
        row.addWidget(self._pause_btn, 1)
        row.addWidget(self._reset_btn, 1)
        return row

    # ------------------------------------------------------------------
    # 旧的 _build_* 占位方法（保留以兼容可能的外部调用）
    # ------------------------------------------------------------------
    def _build_status_card_legacy(self) -> QWidget:  # noqa: D401
        return QWidget()

    def _build_rhythm_card(self) -> QWidget:  # noqa: D401
        return QWidget()

    def _create_rhythm_row(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None

    def _build_active_time_card(self) -> QWidget:  # noqa: D401
        return QWidget()

    def _build_today_stats_card_legacy(self) -> QWidget:  # noqa: D401
        return QWidget()

    def _create_stat_block(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None

    def _button_style(self, *args: Any, **kwargs: Any) -> str:  # noqa: D401
        return ""

    # ------------------------------------------------------------------
    # 事件订阅 / 清理
    # ------------------------------------------------------------------
    def _subscribe_events(self) -> None:
        if self._bus is None:
            return
        try:
            from app.core.event_bus import EventType
            self._bus.subscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("订阅 STATE_CHANGED 失败")

    def _unsubscribe_events(self) -> None:
        if self._bus is None:
            return
        try:
            from app.core.event_bus import EventType
            self._bus.unsubscribe(EventType.STATE_CHANGED, self._on_state_changed)
        except Exception:  # noqa: BLE001
            logger.exception("取消 STATE_CHANGED 订阅失败")

    def cleanup(self) -> None:
        try:
            get_translator().remove_listener(self._on_language_changed)
        except Exception:  # noqa: BLE001
            pass
        self._unsubscribe_events()

    def _on_state_changed(self, payload: Any) -> None:
        """状态变化事件回调（V0.6 简化为直接刷新）。"""
        try:
            self.update_display()
        except Exception:  # noqa: BLE001
            logger.exception("处理状态变化事件失败")

    def _on_language_changed(self, lang: str) -> None:
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # update_display + 刷新（V0.6 仍兼容旧 _refresh_* 接口）
    # ------------------------------------------------------------------
    def update_display(self) -> None:
        self._refresh_status()
        active_seconds = self._safe_get_active_seconds()
        self._active_time_label.setText(self._usage.format_duration(active_seconds))
        uptime = self._safe_get_app_uptime()
        self._uptime_label.setText(
            tr("dashboard.uptime_format", time=self._usage.format_duration(uptime))
        )
        self._refresh_rhythm()
        self._refresh_countdown(active_seconds)
        self._refresh_today_stats()

    def _refresh_status(self) -> None:
        state_name = self._safe_get_state_name()
        state_text, color = self._state_display(state_name)
        self._status_label.setText(state_text)
        self._status_dot.setStyleSheet(
            f"color: {color}; font-size: 16px; background: transparent;"
        )

    def _state_display(self, state_name: str) -> tuple[str, str]:
        mapping = {
            "ACTIVE": ("state.active", tokens.PRIMARY),
            "IDLE": ("state.idle", tokens.WARNING),
            "BREAK_WARNING": ("state.break_warning", tokens.WARNING),
            "SHORT_BREAK": ("state.short_break", tokens.INFO),
            "LONG_BREAK": ("state.long_break", tokens.INFO),
            "PAUSED": ("state.paused", tokens.TEXT_MUTED),
            "LOCKED": ("state.locked", tokens.TEXT_MUTED),
            "SLEEP": ("state.sleep", tokens.TEXT_MUTED),
            "INACTIVE": ("state.inactive", tokens.TEXT_MUTED),
        }
        key, color = mapping.get(state_name, ("state.active", tokens.PRIMARY))
        return tr(key), color

    def _refresh_rhythm(self) -> None:
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
                move_remaining = None  # type: ignore[assignment]
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
        text = self._usage.format_short_duration(remaining)
        row["value"].setText(text)
        if total > 0:
            progress = int(max(0.0, min(1.0, 1.0 - remaining / total)) * 100)
            row["bar"].setValue(progress)

    def _refresh_countdown(self, active_seconds: float) -> None:
        remaining = self._safe_get_remaining_seconds()
        total = active_seconds + remaining
        progress = 0
        if total > 0:
            progress = min(100, int((active_seconds / total) * 100))
        self._progress_bar.setValue(progress)
        self._countdown_label.setText(self._usage.format_short_duration(remaining))
        state_name = self._safe_get_state_name()
        if state_name in ("SHORT_BREAK", "LONG_BREAK"):
            self._countdown_title.setText(tr("dashboard.break_in_progress"))
            self._countdown_label.setText(tr("dashboard.breaking"))
        elif state_name == "BREAK_WARNING":
            self._countdown_title.setText(tr("dashboard.break_upcoming"))
        else:
            self._countdown_title.setText(tr("dashboard.next_break_title"))

    def _refresh_today_stats(self) -> None:
        summary: dict[str, Any] = {}
        try:
            summary = self._usage.get_today_summary()
        except Exception:  # noqa: BLE001
            logger.exception("获取今日摘要失败")

        # 专注时长
        active_seconds = self._safe_get_active_seconds()
        if self._active_tile is not None:
            self._active_tile.set_value(self._usage.format_short_duration(active_seconds))

        # 眨眼提示次数
        blink_value = "0"
        if self._blink_stats is not None:
            try:
                stats_result = self._blink_stats()
                if isinstance(stats_result, (tuple, list)):
                    blink_value = str(int(stats_result[0]))
                else:
                    blink_value = str(int(stats_result))
            except Exception:  # noqa: BLE001
                logger.exception("获取眨眼提示统计失败")
        if self._blink_tile is not None:
            self._blink_tile.set_value(blink_value)

        # 远眺 / 活动 / 长休（兼容旧属性）
        break_count = int(summary.get("break_count", 0))
        skipped = int(summary.get("skipped_breaks", 0))
        self._break_count_label["value"].setText(str(break_count))
        self._skipped_label["value"].setText(str(skipped))
        self._natural_label["value"].setText(str(max(0, self._natural_rest_count)))

    # ------------------------------------------------------------------
    # 容错辅助
    # ------------------------------------------------------------------
    def _safe_get_state_name(self) -> str:
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
        if self._session is None:
            return "ON"
        try:
            return str(self._session.state)
        except Exception:  # noqa: BLE001
            return "ON"

    def _safe_get_long_remaining_seconds(self) -> float:
        if self._break is None:
            return 0.0
        try:
            return max(0.0, float(self._break.get_long_remaining_seconds()))
        except Exception:  # noqa: BLE001
            return 0.0

    def _safe_get_long_work_duration(self) -> float:
        if self._break is None:
            return 0.0
        try:
            return max(0.0, float(self._break.get_long_work_duration()))
        except Exception:  # noqa: BLE001
            return 0.0

    def _safe_get_app_uptime(self) -> float:
        if self._usage is None:
            return 0.0
        try:
            return max(0.0, float(self._usage.get_app_uptime()))
        except Exception:  # noqa: BLE001
            return 0.0

    def _safe_get_remaining_seconds(self) -> float:
        if self._break is None:
            return 0.0
        try:
            return max(0.0, float(self._break.get_remaining_seconds()))
        except Exception:  # noqa: BLE001
            return 0.0

    def retranslate_ui(self) -> None:
        """语言切换时刷新所有静态文本。"""
        if self._rhythm_title is not None:
            self._rhythm_title.setText(tr("dashboard.rhythm_title"))
        for key, card in self._rhythm_widgets.items():
            title_map = {
                "blink": tr("dashboard.rhythm_blink"),
                "short": tr("dashboard.rhythm_short"),
                "move": tr("dashboard.rhythm_move"),
                "long": tr("dashboard.rhythm_long"),
            }
            card._title.setText(title_map.get(key, card._title.text()))  # type: ignore[attr-defined]
        # 状态文字
        self._refresh_status()
        # 按钮文字
        self._break_btn.setText(tr("common.rest_now"))
        self._pause_btn.setText(tr("common.pause_30m"))
        self._reset_btn.setText(tr("dashboard.reset_timer"))
