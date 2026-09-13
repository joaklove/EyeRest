"""位置编辑预览组件（VisualCuePreview）。

**职责与 VisualCuePopup 严格分离**（V0.5.1 教训：把一个为"透明顶层
浮层窗口"设计的组件动态 reparent 成普通子控件，在 Windows 下渲染
不可靠——Qt 逻辑状态可见但屏幕上不显示）：

* :class:`~app.ui.visual_cue.VisualCuePopup` —— 正常提醒：独立顶层
  置顶 Tool 窗口、透明背景、呼吸动画、自动消失
* :class:`VisualCuePreview` —— 位置编辑专用：**普通 QWidget 子控件**，
  静止显示、无任何窗口 flag、无 GraphicsEffect、无定时器，只负责
  展示和拖动

预览是 :class:`~app.ui.position_editor.PositionEditor` 的直接子控件，
Qt 子控件绘制顺序确定（父背景 → 子控件），"眼睛压在遮罩上"天然成立，
不依赖任何 raise / Z-order 操作。

预览内容 = 真实提示的静态快照（同皮肤、同强度尺寸 + 文字「眨眨眼」），
用户调整的就是日常看到的完整提示。**卡片配色/圆角/描边/文字色统一取自
:func:`app.ui.visual_cue.cue_card_stylesheet`**，没有第二份样式。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QFrame, QVBoxLayout, QWidget

from app.i18n import tr
from app.ui.visual_cue import (
    CUE_CARD_OBJECT_NAME,
    _ICONS_BY_SKIN,
    _ICON_SIZE_BY_SKIN,
    _INTENSITY_SCALE,
    cue_card_stylesheet,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class VisualCuePreview(QWidget):
    """位置编辑预览（普通子控件，可拖动，静止显示）。

    Args:
        skin: 视觉皮肤（minimal / cartoon / character）。
        intensity: 提醒强度（quiet / standard / prominent），决定尺寸。
        with_text: 是否显示文字（默认显示「眨眨眼」完整提示）。
        parent: 父控件（应为 PositionEditor）。
    """

    #: 与编辑器遮罩保持的最小边距（拖动边界保护）
    EDGE_MARGIN = 24

    def __init__(
        self,
        skin: str = "minimal",
        intensity: str = "standard",
        with_text: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._skin = skin if skin in _ICONS_BY_SKIN else "minimal"
        self._intensity = intensity if intensity in _INTENSITY_SCALE else "standard"
        self._with_text = bool(with_text)
        self._drag_offset: Optional[QPoint] = None

        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI（纯 QLabel/QFrame，无窗口属性、无 GraphicsEffect、无动画）
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._card = QFrame(self)
        # 对象名与样式表都取自 visual_cue —— 预览卡与真实提示卡**同源**，
        # 不允许在这里另写一份配色（历史上正是这么漂成"深色底 + 蓝边"的）。
        self._card.setObjectName(CUE_CARD_OBJECT_NAME)
        self._card.setStyleSheet(cue_card_stylesheet())
        outer.addWidget(self._card)

        card_layout = QHBoxLayout(self._card)
        card_layout.setContentsMargins(18, 10, 20, 10)
        card_layout.setSpacing(10)

        size_scale, _ = _INTENSITY_SCALE[self._intensity]
        base_size = _ICON_SIZE_BY_SKIN.get(self._skin, 22)

        self._icon = QLabel(
            _ICONS_BY_SKIN[self._skin].get("blink", "👁"), self._card
        )
        self._icon.setStyleSheet(f"font-size: {int(base_size * size_scale)}px;")
        card_layout.addWidget(self._icon)

        self._text = QLabel(tr("cue.blink") if self._with_text else "", self._card)
        self._text.setStyleSheet(
            f"font-size: {int(13 * max(1.0, size_scale))}px; background: transparent;"
        )
        self._text.setVisible(self._with_text)
        card_layout.addWidget(self._text)

        self.adjustSize()

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def configure(self, skin: str, intensity: str) -> None:
        """更新皮肤与强度（每次打开编辑器时调用，保证与设置同步）。"""
        changed = False
        if skin in _ICONS_BY_SKIN and skin != self._skin:
            self._skin = skin
            changed = True
        if intensity in _INTENSITY_SCALE and intensity != self._intensity:
            self._intensity = intensity
            changed = True
        if changed:
            self._build_ui()

    # ------------------------------------------------------------------
    # 定位（编辑器坐标系）
    # ------------------------------------------------------------------
    def place_at_global(self, global_pos: QPoint) -> None:
        """把预览放到全局屏幕坐标处（换算为编辑器坐标并做边界保护）。"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(self._clamp(parent.mapFromGlobal(global_pos)))

    def global_top_left(self) -> QPoint:
        """预览左上角的真实全局屏幕坐标（保存位置时使用）。"""
        return self.mapToGlobal(QPoint(0, 0))

    def _clamp(self, pos: QPoint) -> QPoint:
        """边缘保护：限制在编辑器区域内并保持最小边距。"""
        parent = self.parentWidget()
        if parent is None:
            return pos
        margin = self.EDGE_MARGIN
        pos.setX(max(margin, min(pos.x(), parent.width() - self.width() - margin)))
        pos.setY(max(margin, min(pos.y(), parent.height() - self.height() - margin)))
        return pos

    # ------------------------------------------------------------------
    # 拖动
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt 命名)
        """左键按下：记录拖动起点（按下点相对预览左上角的偏移）。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.mapToGlobal(QPoint(0, 0))
            )
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt 命名)
        """按住拖动：只移动预览自身，遮罩与编辑器不动。"""
        if self._drag_offset is not None:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            parent = self.parentWidget()
            if parent is not None:
                self.move(self._clamp(parent.mapFromGlobal(new_pos)))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt 命名)
        """松开：结束拖动（保存由编辑器显式完成）。"""
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)
