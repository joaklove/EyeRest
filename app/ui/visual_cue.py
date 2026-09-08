"""统一视觉提醒组件（VisualCuePopup）—— 四层节奏共用。

设计原则（V1.0 正式产品规格）：

* **提醒你，而不是控制你**：非模态、不抢焦点、不强迫任何操作
* **四种条件反射**：四层节奏各有专属图标与文案，绝不都叫"休息一下"
* **视觉形象可换**：三种 Skin（极简眼睛 / 卡通双眼 / 小眼睛角色），
  是同一个组件的不同 Skin，不是三个窗口
* **位置可定制**：默认底部中央；预设四角；自由拖动（带边缘保护与
  最小边距）；可锁定；记住显示器与坐标
* **强度三档**：安静 / 标准 / 明显——体现在角色尺寸、停留时长与
  文字显著程度，**绝不变成强制通知**
* **文字必须存在**：对抗视觉习惯化；眨眼提示在周期内与"仅动画"
  交替出现（文字传递意义，动画维持节奏）

四种 Cue：

=========  ====  ================  ==========
层级        图标  默认文案           用户动作
=========  ====  ================  ==========
Blink      👁    眨眨眼             完整眨眼
Look Away  🌿    看远处 20 秒        看远处
Move       🚶    起来动一动          起身活动
Deep Break 🧘    离开屏幕休息一下     离开设备
=========  ====  ================  ==========

用法::

    cue = VisualCuePopup()
    cue.cue_acted.connect(on_user_acted)
    cue.cue_finished.connect(on_auto_finished)
    cue.show_cue(kind="blink", with_text=True, duration=3.0)
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    Qt,
    QTimer,
    Signal,
    QPropertyAnimation,
    QPoint,
)
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QProgressBar,
)

from app.config import defaults
from app.i18n.translator import tr
from app.utils.logger import get_logger

logger = get_logger(__name__)

#: 四层节奏的专属图标（minimal / cartoon 皮肤按 kind 区分，character 用吉祥物）
_ICONS_BY_SKIN = {
    "minimal": {"blink": "👁", "look_away": "🌿", "move": "🚶", "deep": "🧘"},
    "cartoon": {"blink": "👀", "look_away": "🌿", "move": "🚶", "deep": "🧘"},
    "character": {"blink": "🐼", "look_away": "🐼", "move": "🐼", "deep": "🐼"},
}

#: 强度三档：角色尺寸倍率 / 时长倍率
_INTENSITY_SCALE = {
    "quiet": (0.85, 1.0),
    "standard": (1.0, 1.0),
    "prominent": (1.3, 1.35),
}

#: 各 Skin 的基础图标字号（px）
_ICON_SIZE_BY_SKIN = {"minimal": 22, "cartoon": 22, "character": 28}

#: 呼吸动画周期（毫秒）
_BREATH_MS = 1200


class VisualCuePopup(QWidget):
    """非模态视觉提醒气泡（四层节奏共用）。

    信号:
        cue_acted(str): 用户点击了提示（参数为 kind；视为"已知晓/已完成"）。
        cue_finished(str): 展示时长到自动消失（参数为 kind）。
            对 look_away 而言自动消失即"完成了一次远眺"。
        cue_position_changed(int, int, int): 用户拖动结束（x, y, monitor_index）。
    """

    cue_acted = Signal(str)
    cue_finished = Signal(str)
    cue_position_changed = Signal(int, int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._kind: str = "blink"
        self._visible: bool = False
        self._skin: str = defaults.CUE_SKIN
        self._intensity: str = defaults.CUE_INTENSITY
        self._position_locked: bool = bool(defaults.CUE_POSITION_LOCKED)
        self._countdown_total: int = 0
        #: 位置编辑模式（静止、不自动消失、拖动不自动保存）
        self._preview: bool = False

        # 位置状态（由 apply_position 设置）
        self._pos_mode: str = defaults.CUE_POSITION
        self._pos_x: Optional[int] = None
        self._pos_y: Optional[int] = None
        self._pos_monitor: int = 0

        # 拖动状态
        self._press_global: Optional[QPoint] = None
        self._press_local: QPoint = QPoint()
        self._dragged: bool = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 显示但不激活（不抢焦点），且不在任务栏出现
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._build_ui()

        # 自动隐藏定时器
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_timeout)

        # 远眺倒计时定时器
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        # 呼吸动画（作用在**整个窗口**上——卡片自身已挂阴影效果，
        # 一个 widget 只能有一个 GraphicsEffect，后者会覆盖前者）
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._breath = QPropertyAnimation(self._opacity, b"opacity", self)
        self._breath.setDuration(_BREATH_MS)
        self._breath.setStartValue(0.55)
        self._breath.setKeyValueAt(0.5, 1.0)
        self._breath.setEndValue(0.55)
        self._breath.setLoopCount(-1)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("cueCard")
        self._card.setStyleSheet(
            """
            QFrame#cueCard {
                background-color: rgba(28, 32, 44, 235);
                border-radius: 22px;
                border: 1px solid rgba(120, 200, 255, 90);
            }
            QLabel { color: #eaf4ff; background: transparent; }
            """
        )
        outer.addWidget(self._card)

        card_layout = QHBoxLayout(self._card)
        card_layout.setContentsMargins(18, 10, 20, 10)
        card_layout.setSpacing(10)

        self._icon = QLabel("👁", self._card)
        card_layout.addWidget(self._icon)

        text_column = QVBoxLayout()
        text_column.setSpacing(4)
        self._text = QLabel("", self._card)
        text_column.addWidget(self._text)

        # 远眺倒计时进度条（仅 look_away 显示）
        self._progress = QProgressBar(self._card)
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.setStyleSheet(
            """
            QProgressBar {
                background-color: rgba(255, 255, 255, 40);
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #7BD389;
                border-radius: 2px;
            }
            """
        )
        self._progress.setVisible(False)
        text_column.addWidget(self._progress)

        card_layout.addLayout(text_column)

        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 150))
        self._card.setGraphicsEffect(shadow)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def apply_appearance(
        self,
        skin: Optional[str] = None,
        intensity: Optional[str] = None,
        position_locked: Optional[bool] = None,
    ) -> None:
        """应用视觉设置（皮肤 / 强度 / 位置锁定），热更新时调用。"""
        if skin is not None and skin in _ICONS_BY_SKIN:
            self._skin = skin
        if intensity is not None and intensity in _INTENSITY_SCALE:
            self._intensity = intensity
        if position_locked is not None:
            self._position_locked = bool(position_locked)

    def apply_position(
        self,
        position: str = "default",
        x: Optional[int] = None,
        y: Optional[int] = None,
        monitor: int = 0,
    ) -> None:
        """按设置定位提示（预设 / 自定义坐标 / 默认底部中央）。"""
        self._pos_mode = position if position in (
            "default", "bottom_left", "bottom_right", "top_left", "top_right", "custom"
        ) else "default"
        self._pos_x = int(x) if x is not None and x >= 0 else None
        self._pos_y = int(y) if y is not None and y >= 0 else None
        self._pos_monitor = max(0, int(monitor))
        if self._visible:
            self._place()

    # ------------------------------------------------------------------
    # 显示
    # ------------------------------------------------------------------
    def show_cue(
        self,
        kind: str = "blink",
        *,
        with_text: bool = True,
        duration: float = 3.0,
        countdown_seconds: int = 0,
    ) -> None:
        """显示一次提示。

        Args:
            kind: ``blink`` / ``look_away`` / ``move`` / ``deep``。
            with_text: 是否显示文字（眨眼周期内与仅动画交替）。
                look_away / move / deep 始终显示文字。
            duration: 展示时长（秒），超时自动消失并发出 cue_finished。
            countdown_seconds: >0 时显示倒计时进度条（远眺 20 秒）。
        """
        kind = kind if kind in _ICONS_BY_SKIN["minimal"] else "blink"
        self._kind = kind

        size_scale, duration_scale = _INTENSITY_SCALE.get(
            self._intensity, _INTENSITY_SCALE["standard"]
        )

        # 图标
        base_size = _ICON_SIZE_BY_SKIN.get(self._skin, 22)
        icon = _ICONS_BY_SKIN[self._skin].get(kind, "👁")
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"font-size: {int(base_size * size_scale)}px;")

        # 文案
        text_key = f"cue.{kind}"
        show_text = with_text or kind in ("look_away", "move", "deep")
        self._text.setText(tr(text_key) if show_text else "")
        self._text.setVisible(show_text)
        self._text.setStyleSheet(
            f"font-size: {int(13 * max(1.0, size_scale))}px; background: transparent;"
        )

        # 倒计时进度条
        self._countdown_total = max(0, int(countdown_seconds))
        self._progress.setVisible(self._countdown_total > 0)
        if self._countdown_total > 0:
            self._progress.setRange(0, self._countdown_total)
            self._progress.setValue(self._countdown_total)

        self.adjustSize()
        self._place()

        self._visible = True
        self.show()
        self.raise_()

        self._breath.start()
        if self._countdown_total > 0:
            self._countdown_timer.start()
            effective = float(self._countdown_total)
        else:
            self._countdown_timer.stop()
            effective = duration
        self._hide_timer.start(max(500, int(effective * duration_scale * 1000)))
        logger.debug(
            "视觉提示已显示: kind=%s skin=%s intensity=%s duration=%.1fs",
            kind, self._skin, self._intensity, effective,
        )

    def hide_cue(self) -> None:
        """立即隐藏提示（不发出完成/忽略信号）。"""
        self._hide_timer.stop()
        self._countdown_timer.stop()
        self._breath.stop()
        self._visible = False
        self.hide()

    def is_cue_visible(self) -> bool:
        """提示当前是否正在显示。"""
        return self._visible

    def current_kind(self) -> str:
        """当前正在显示的提示类型。"""
        return self._kind

    # ------------------------------------------------------------------
    # 定位
    # ------------------------------------------------------------------
    def _place(self) -> None:
        """按位置模式计算并应用窗口位置（含边缘保护）。"""
        screens = QGuiApplication.screens()
        if not screens:
            return
        screen = screens[min(self._pos_monitor, len(screens) - 1)]
        geo = screen.geometry()
        margin = defaults.CUE_EDGE_MARGIN

        mode = getattr(self, "_pos_mode", "default")
        if mode == "custom" and self._pos_x is not None and self._pos_y is not None:
            # 自定义坐标是相对所选显示器的局部坐标
            origin = geo.topLeft()
            pos = QPoint(origin.x() + self._pos_x, origin.y() + self._pos_y)
        elif mode == "bottom_left":
            pos = QPoint(geo.left() + margin, geo.bottom() - self.height() - margin)
        elif mode == "bottom_right":
            pos = QPoint(geo.right() - self.width() - margin, geo.bottom() - self.height() - margin)
        elif mode == "top_left":
            pos = QPoint(geo.left() + margin, geo.top() + margin)
        elif mode == "top_right":
            pos = QPoint(geo.right() - self.width() - margin, geo.top() + margin)
        else:
            # 默认：主屏幕底部居中（任务栏上方）
            primary = QGuiApplication.primaryScreen()
            pgeo = primary.availableGeometry() if primary else geo
            pos = QPoint(
                pgeo.center().x() - self.width() // 2,
                pgeo.bottom() - self.height() - margin,
            )

        self.move(self._clamp(self._to_parent(pos)))

    def _to_parent(self, pos: QPoint) -> QPoint:
        """把全局屏幕坐标换算为当前坐标系（独立窗口=全局；子控件=父坐标）。"""
        parent = self.parentWidget()
        if parent is None:
            return pos
        return parent.mapFromGlobal(pos)

    def _clamp(self, pos: QPoint) -> QPoint:
        """边缘保护：限制在虚拟桌面（子控件模式为编辑器区域）内。"""
        parent = self.parentWidget()
        if parent is not None:
            margin = defaults.CUE_EDGE_MARGIN
            bounds = parent.rect()
            pos.setX(max(bounds.left() + margin, min(pos.x(), bounds.right() - self.width() - margin)))
            pos.setY(max(bounds.top() + margin, min(pos.y(), bounds.bottom() - self.height() - margin)))
            return pos
        screens = QGuiApplication.screens()
        if not screens:
            return pos
        margin = defaults.CUE_EDGE_MARGIN
        union = screens[0].virtualGeometry()
        pos.setX(max(union.left() + margin, min(pos.x(), union.right() - self.width() - margin)))
        pos.setY(max(union.top() + margin, min(pos.y(), union.bottom() - self.height() - margin)))
        return pos

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _finish(self, acted: bool) -> None:
        """结束一次提示并发出对应信号。"""
        if not self._visible:
            return
        kind = self._kind
        self._visible = False
        self._hide_timer.stop()
        self._countdown_timer.stop()
        self._breath.stop()
        self.hide()
        if acted:
            logger.debug("提示被用户确认: kind=%s", kind)
            self.cue_acted.emit(kind)
        else:
            logger.debug("提示自动结束: kind=%s", kind)
            self.cue_finished.emit(kind)

    def _on_timeout(self) -> None:
        """展示时长到，未被点击 → 自动结束。"""
        self._finish(acted=False)

    def _on_countdown_tick(self) -> None:
        """远眺倒计时：每秒推进进度条。"""
        value = self._progress.value() - 1
        self._progress.setValue(max(0, value))

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    _DRAG_THRESHOLD = 8  # 移动超过该像素视为拖动，否则视为点击确认

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """记录按下位置（拖动与点击共用的起点）。"""
        if event.button() == Qt.MouseButton.LeftButton and not self._position_locked:
            self._press_global = event.globalPosition().toPoint()
            self._press_local = self.frameGeometry().topLeft()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """按住拖动提示（位置锁定时无效；边缘保护在 _place/save 中统一处理）。"""
        press = getattr(self, "_press_global", None)
        if press is None or self._position_locked:
            super().mouseMoveEvent(event)
            return
        current = event.globalPosition().toPoint()
        if (current - press).manhattanLength() > self._DRAG_THRESHOLD:
            self._dragged = True
            offset = current - press
            self.move(self._clamp(self._press_local + offset))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """松开：发生了拖动 → 保存位置；纯点击 → 视为确认（已完成）。"""
        was_dragged = getattr(self, "_dragged", False)
        self._press_global = None
        self._dragged = False
        if was_dragged:
            self._save_position()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._finish(acted=True)
        super().mouseReleaseEvent(event)

    def current_position(self) -> tuple[int, int, int]:
        """返回当前位置（相对所在显示器的局部坐标 + 显示器序号）。"""
        screens = QGuiApplication.screens()
        if not screens:
            return (0, 0, 0)
        center = self.frameGeometry().center()
        monitor = 0
        for i, s in enumerate(screens):
            if s.geometry().contains(center):
                monitor = i
                break
        origin = screens[min(monitor, len(screens) - 1)].geometry().topLeft()
        return (self.x() - origin.x(), self.y() - origin.y(), monitor)

    def _save_position(self) -> None:
        """把当前位置换算为相对显示器的局部坐标并通知上层保存。"""
        x, y, monitor = self._compute_position()
        self.cue_position_changed.emit(x, y, monitor)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口被关闭（如应用退出）时静默处理，不触发统计信号。"""
        self._hide_timer.stop()
        self._countdown_timer.stop()
        self._breath.stop()
        self._visible = False
        super().closeEvent(event)
