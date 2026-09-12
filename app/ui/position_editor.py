"""位置编辑器（V0.5 P0-2：自定义提示位置）。

用户在设置页选择「自定义」位置后：

1. **暂停所有护眼节奏**（Blink / Look Away / Move / Deep Break）——由调用方
   在打开编辑器前设置暂停标志，编辑器关闭后恢复
2. 预览角色**静止显示**：不眨眼、不倒计时、不自动消失、不重新定位
3. 用户拖动角色到满意位置，点「保存位置」（或 Enter；Esc 取消）
4. 保存成功给出 ``✓`` 反馈，退出编辑模式，恢复护眼节奏

架构（V0.5.1 第二轮重构，**禁止依赖任何 Z-order / raise 操作**）::

    PositionEditor（唯一顶层窗口：遮罩 + 提示 + 按钮，允许焦点）
        ├── 暗色遮罩（paintEvent 半透明填充）
        └── VisualCuePreview（普通子控件：静态展示 + 拖动）

正常提醒继续由 :class:`~app.ui.visual_cue.VisualCuePopup`（独立顶层
窗口）负责；编辑模式使用独立的
:class:`~app.ui.visual_cue_preview.VisualCuePreview`（普通 QWidget）。
两个组件职责分离，**不存在任何窗口属性转换**。

坐标模型：编辑器铺满虚拟桌面，即"编辑器坐标 = 虚拟桌面全局坐标"。
预览在编辑器坐标系内移动；保存时才换算为 monitor-relative (x, y)，
与既有配置格式完全兼容。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ui.theme import tokens
from app.ui.visual_cue_preview import VisualCuePreview
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PositionEditor(QWidget):
    """全屏位置编辑遮罩。

    信号:
        position_saved(int, int, int): 保存位置（x, y, monitor）。
        editor_closed(): 编辑器关闭（无论保存还是取消）。
    """

    position_saved = Signal(int, int, int)
    editor_closed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._saved = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 位置编辑是用户主动进入的明确操作，允许获取焦点——
        # 这样 Esc / Enter 键盘操作才可靠（不设 WA_ShowWithoutActivating）。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle(tr("position.title"))

        self._build_ui()
        self._preview: Optional[VisualCuePreview] = None

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
    def open_editor(
        self,
        skin: str = "minimal",
        intensity: str = "standard",
        position_mode: str = "default",
        pos_x: Optional[int] = None,
        pos_y: Optional[int] = None,
        monitor: int = 0,
    ) -> None:
        """打开编辑器并显示可拖动预览。

        Args:
            skin: 当前皮肤（与设置同步）。
            intensity: 当前提醒强度。
            position_mode: 当前位置模式；``custom`` 且有坐标时预览从
                上次保存的位置开始，否则从默认位置（主屏底部中央）开始。
            pos_x / pos_y / monitor: 上次保存的自定义坐标。
        """
        union = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(union)

        # （重）建预览：普通子控件，无任何窗口属性转换
        if self._preview is None:
            self._preview = VisualCuePreview(skin, intensity, with_text=True, parent=self)
        else:
            self._preview.configure(skin, intensity)

        self._preview.move(self._start_position(position_mode, pos_x, pos_y, monitor))

        self.show()
        self.raise_()
        self.activateWindow()
        self._preview.show()
        self._preview.raise_()
        logger.info(
            "位置编辑器已打开（护眼节奏暂停，preview=%s isWindow=%s parent=%s）",
            self._preview.isVisible(),
            self._preview.isWindow(),
            "编辑器" if self._preview.parentWidget() is self else "未知",
        )

    def _start_position(
        self,
        position_mode: str,
        pos_x: Optional[int],
        pos_y: Optional[int],
        monitor: int,
    ) -> QPoint:
        """计算预览初始位置（编辑器坐标系）。

        已有 custom 坐标 → 从上次保存的位置开始；
        否则 → 主屏底部中央（与 VisualCuePopup 默认位置一致）。
        """
        union = self.geometry()
        screens = QGuiApplication.screens()
        if position_mode == "custom" and pos_x is not None and pos_y is not None and screens:
            geo = screens[min(max(0, int(monitor)), len(screens) - 1)].geometry()
            origin = geo.topLeft()
            # 编辑器铺满虚拟桌面：编辑器坐标 ≈ 虚拟桌面全局坐标
            return QPoint(origin.x() + int(pos_x) - union.left(),
                          origin.y() + int(pos_y) - union.top())
        margin = 24
        primary = QGuiApplication.primaryScreen()
        pgeo = primary.availableGeometry() if primary else union
        return QPoint(
            pgeo.center().x() - union.left() - (self._preview.width() // 2 if self._preview else 0),
            pgeo.bottom() - union.top() - margin - (self._preview.height() if self._preview else 0),
        )

    def close_editor(self) -> None:
        """关闭编辑器并隐藏预览（护眼节奏由调用方恢复）。"""
        if self._preview is not None:
            self._preview.hide()
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
        """保存预览当前位置（换算为 monitor-relative 坐标）并退出。"""
        if self._preview is None:
            self.close_editor()
            return
        x, y, monitor = self._compute_monitor_position()
        self._saved = True
        self.position_saved.emit(x, y, monitor)
        logger.info("位置已保存: (%d, %d) monitor=%d", x, y, monitor)
        self.close_editor()

    def _on_cancel(self) -> None:
        """取消编辑（不保存位置）。"""
        logger.info("位置编辑已取消")
        self.close_editor()

    def _compute_monitor_position(self) -> tuple[int, int, int]:
        """把预览当前位置换算为相对所在显示器的局部坐标 + 显示器序号。"""
        screens = QGuiApplication.screens()
        if not screens or self._preview is None:
            return (0, 0, 0)
        top_left = self._preview.global_top_left()
        frame_center = top_left + QPoint(self._preview.width() // 2, self._preview.height() // 2)
        monitor = 0
        for i, s in enumerate(screens):
            if s.geometry().contains(frame_center):
                monitor = i
                break
        origin = screens[min(monitor, len(screens) - 1)].geometry().topLeft()
        return (top_left.x() - origin.x(), top_left.y() - origin.y(), monitor)

    @property
    def saved(self) -> bool:
        """本次编辑是否保存了位置。"""
        return self._saved

    @property
    def preview(self) -> Optional[VisualCuePreview]:
        """当前预览组件（测试与调试用）。"""
        return self._preview

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """刷新文案（语言切换时调用）。"""
        self._hint.setText(tr("position.hint"))
        self._save_button.setText(tr("position.save"))
        self._cancel_button.setText(tr("position.cancel"))


# 按钮样式全部走设计系统 token（此前是硬编码蓝 #1976d2 / 灰 rgba(...)，属 V0.6
# 之前的遗留，与「主色只有一个真源」冲突）。
_BUTTON_STYLE_PRIMARY = (
    f"QPushButton {{ background-color: {tokens.PRIMARY}; color: {tokens.TEXT_INVERTED}; "
    f"border: none; border-radius: {tokens.RADIUS_SM}px; "
    f"padding: 10px 26px; font-size: {tokens.BODY}px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {tokens.PRIMARY_HOVER}; }}"
    f"QPushButton:pressed {{ background-color: {tokens.PRIMARY_PRESSED}; }}"
)

_BUTTON_STYLE_GRAY = (
    f"QPushButton {{ background-color: {tokens.BG_SOFT}; color: {tokens.TEXT_PRIMARY}; "
    f"border: 1px solid {tokens.BORDER}; border-radius: {tokens.RADIUS_SM}px; "
    f"padding: 10px 22px; font-size: {tokens.BODY}px; }}"
    f"QPushButton:hover {{ background-color: {tokens.BG_RAISED}; "
    f"border-color: {tokens.BORDER_FOCUS}; }}"
)
