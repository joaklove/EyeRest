"""位置编辑器（V0.5 P0-2：自定义提示位置）。

用户在设置页选择「自定义」位置后：

1. **暂停所有护眼节奏**（Blink / Look Away / Move / Deep Break）——由调用方
   在打开编辑器前设置暂停标志，编辑器关闭后恢复
2. 角色**静止显示**：不眨眼、不倒计时、不自动消失、不重新定位
3. 用户拖动角色到满意位置，点「保存位置」
4. 保存成功给出 ``✓`` 反馈，退出编辑模式，恢复护眼节奏

编辑器本身是一个半透明全屏遮罩，不抢焦点、不影响其它窗口，
角色由 :class:`~app.ui.visual_cue.VisualCuePopup` 以预览模式呈现，
因此预览尺寸/皮肤与真实提示完全一致。
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ui.visual_cue import VisualCuePopup
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PositionEditor(QWidget):
    """全屏位置编辑遮罩。

    信号:
        position_saved(int, int, int): 保存位置（x, y, monitor）。
        editor_closed(): 编辑器关闭（无论保存还是取消）。

    Args:
        cue: 视觉提示组件（会以预览模式显示，供拖动）。
        parent: 父窗口部件。
    """

    position_saved = Signal(int, int, int)
    editor_closed = Signal()

    def __init__(self, cue: VisualCuePopup, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cue = cue
        self._saved = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(tr("position.title"))

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._hint = QLabel(tr("position.hint"), self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet(
            """
            QLabel {
                color: #eaf4ff;
                background-color: rgba(20, 24, 34, 210);
                border-radius: 10px;
                padding: 10px 18px;
                font-size: 15px;
            }
            """
        )
        self._hint.adjustSize()

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 48, 40)
        bottom.setSpacing(12)
        bottom.addStretch(1)

        self._cancel_button = QPushButton(tr("position.cancel"), self)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.setStyleSheet(_BUTTON_STYLE_GRAY)
        self._cancel_button.clicked.connect(self._on_cancel)
        bottom.addWidget(self._cancel_button)

        self._save_button = QPushButton(tr("position.save"), self)
        self._save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_button.setStyleSheet(_BUTTON_STYLE_PRIMARY)
        self._save_button.clicked.connect(self._on_save)
        bottom.addWidget(self._save_button)

        root.addStretch(1)
        hint_row = QHBoxLayout()
        hint_row.addStretch(1)
        hint_row.addWidget(self._hint)
        hint_row.addStretch(1)
        root.addLayout(hint_row)
        root.addStretch(1)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def open_editor(self) -> None:
        """打开编辑器：铺满整个虚拟桌面并进入预览模式。

        窗口层级（V0.5.1 修复：遮罩必须压在角色下面）::

            Desktop → PositionEditor(暗色遮罩) → VisualCue(可拖动角色)

        因此先显示并提升遮罩，**再**让 VisualCue 进入预览（其
        ``start_preview`` 内部会 ``show + raise``），最后再补一次
        ``raise`` 确保角色稳压遮罩之上。
        """
        union = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(union)
        self._cue.start_preview()
        self.show()
        self.raise_()
        # 关键：遮罩 raise 之后，角色必须再 raise 一次才能露出
        self._cue.raise_()
        # 事件循环跑一轮后再兜底一次（Windows 上 Tool 窗口的 z-order
        # 可能在 show 完成后才最终确定）
        QTimer.singleShot(0, self._cue.raise_)
        logger.info("位置编辑器已打开（护眼节奏暂停）")

    def close_editor(self) -> None:
        """关闭编辑器并退出预览。"""
        self._cue.end_preview()
        self.hide()
        self.editor_closed.emit()

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """绘制半透明遮罩（让屏幕变暗，突出提示位置）。"""
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(10, 12, 18, 120))
        super().paintEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt 命名)
        """Esc 取消编辑，Enter 保存位置。"""
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_save()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """点击遮罩空白处不关闭（避免误触丢失编辑）。"""
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        """保存当前位置并退出编辑模式。"""
        x, y, monitor = self._cue.current_position()
        self._saved = True
        self.position_saved.emit(x, y, monitor)
        logger.info("位置已保存: (%d, %d) monitor=%d", x, y, monitor)
        self.close_editor()

    def _on_cancel(self) -> None:
        """取消编辑（不保存位置）。"""
        logger.info("位置编辑已取消")
        self.close_editor()

    @property
    def saved(self) -> bool:
        """本次编辑是否保存了位置。"""
        return self._saved

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """刷新文案（语言切换时调用）。"""
        self._hint.setText(tr("position.hint"))
        self._save_button.setText(tr("position.save"))
        self._cancel_button.setText(tr("position.cancel"))


_BUTTON_STYLE_PRIMARY = (
    "QPushButton { background-color: #1976d2; color: white; border: none; "
    "border-radius: 6px; padding: 10px 26px; font-size: 14px; font-weight: bold; }"
    "QPushButton:hover { background-color: #1565c0; }"
)

_BUTTON_STYLE_GRAY = (
    "QPushButton { background-color: rgba(120, 124, 134, 220); color: white; "
    "border: none; border-radius: 6px; padding: 10px 22px; font-size: 14px; }"
    "QPushButton:hover { background-color: rgba(96, 100, 110, 230); }"
)
