"""Design System 通用组件（V0.6）。

仅放跨页面复用的最小原子组件：

* :class:`SoftCard` — 暖色圆角卡片
* :class:`SectionTitle` — 区块标题（左侧 3px 主色 accent + 文字）
* :class:`GhostButton` — 透明边框次按钮
* :class:`PrimaryButton` — 主色实心按钮
* :class:`NoScrollSpinBox` — 禁滚轮数值框（继承自 V0.5.1）
* :class:`StatTile` — 5 列数据小方块

业务页面（Dashboard / Settings / Statistics）共用这些组件，保证视觉
一致性。组件内部颜色/字号/圆角全部从 :mod:`app.ui.theme.tokens` 取。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import assets, tokens


def rgba(hex_color: str, alpha: float) -> str:
    """``#RRGGBB`` → ``rgba(r, g, b, a)``（QSS 圆底/软背景用）。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


class SoftCard(QFrame):
    """暖色圆角卡片（白底 + 暖灰边 + 柔和阴影）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SoftCard")
        self.setStyleSheet(
            f"QFrame#SoftCard {{"
            f"  background-color: {tokens.BG_SURFACE};"
            f"  border: 1px solid {tokens.BORDER_SOFT};"
            f"  border-radius: {tokens.RADIUS_LG};"
            f"}}"
        )


class SectionTitle(QLabel):
    """区块标题：左侧 3px 主色 accent + 文字（截图风格）。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY};"
            f"font-size: {tokens.H2}px;"
            f"font-weight: {tokens.WEIGHT_BOLD};"
            f"padding-left: {tokens.SPACE_2}px;"
            f"border-left: 3px solid {tokens.PRIMARY};"
        )


class PrimaryButton(QPushButton):
    """主色实心按钮（圆角 10、高度自适应）。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {tokens.PRIMARY};"
            f"  color: {tokens.TEXT_INVERTED};"
            f"  border: none;"
            f"  border-radius: {tokens.RADIUS_MD};"
            f"  padding: 8px 22px;"
            f"  font-size: {tokens.BODY}px;"
            f"  font-weight: {tokens.WEIGHT_BOLD};"
            f"}}"
            f"QPushButton:hover {{ background-color: {tokens.PRIMARY_HOVER}; }}"
            f"QPushButton:pressed {{ background-color: {tokens.PRIMARY_PRESSED}; }}"
            f"QPushButton:disabled {{ background-color: {tokens.BORDER}; color: {tokens.TEXT_DISABLED}; }}"
        )


class GhostButton(QPushButton):
    """次按钮：透明背景 + 暖灰边框。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: transparent;"
            f"  color: {tokens.TEXT_PRIMARY};"
            f"  border: 1px solid {tokens.BORDER};"
            f"  border-radius: {tokens.RADIUS_MD};"
            f"  padding: 8px 18px;"
            f"  font-size: {tokens.BODY}px;"
            f"  font-weight: {tokens.WEIGHT_MEDIUM};"
            f"}}"
            f"QPushButton:hover {{ background-color: {tokens.BG_SOFT}; border-color: {tokens.PRIMARY}; }}"
            f"QPushButton:pressed {{ background-color: {tokens.PRIMARY_SOFT}; }}"
            f"QPushButton:disabled {{ color: {tokens.TEXT_DISABLED}; border-color: {tokens.BORDER_SOFT}; }}"
        )


class NoScrollSpinBox(QSpinBox):
    """禁滚轮数值框（设置页使用，鼠标滚轮不会改变数值）。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setMinimumHeight(34)
        self.setStyleSheet(
            f"QSpinBox {{"
            f"  background-color: {tokens.BG_SURFACE};"
            f"  color: {tokens.TEXT_PRIMARY};"
            f"  border: 1px solid {tokens.BORDER};"
            f"  border-radius: {tokens.RADIUS_MD};"
            f"  padding: 4px 12px;"
            f"  font-size: {tokens.BODY}px;"
            f"}}"
            f"QSpinBox:focus {{ border-color: {tokens.PRIMARY}; }}"
        )

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        event.ignore()


class StatTile(QFrame):
    """今日数据小方块（展示板风格：彩色圆底图标 + 数值 + 标签 竖排）。"""

    def __init__(
        self,
        icon: str,
        label: str,
        value: str = "--",
        accent: str = tokens.PRIMARY,
        parent: Optional[QWidget] = None,
        icon_asset: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatTile")
        self._accent = accent
        self._build(icon, label, icon_asset)
        self.set_value(value)

    def _build(self, icon: str, label: str, icon_asset: Optional[str] = None) -> None:
        self.setStyleSheet(
            f"QFrame#StatTile {{"
            f"  background-color: {tokens.BG_SURFACE};"
            f"  border: 1px solid {tokens.BORDER_SOFT};"
            f"  border-radius: {tokens.RADIUS_LG};"
            f"}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # 彩色圆底图标（有插画资产用插画，否则回退 emoji 文字）
        self._icon = QLabel(icon, self)
        self._icon.setFixedSize(36, 36)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            f"QLabel {{ background-color: {rgba(self._accent, 0.14)};"
            f" border-radius: 18px; font-size: 16px; }}"
        )
        if icon_asset and assets.has(icon_asset):
            self._icon.setText("")
            self._icon.setPixmap(assets.pixmap(icon_asset, 24))
        layout.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 数值
        self._value = QLabel("--", self)
        font = QFont()
        font.setPointSize(tokens.H3)
        font.setWeight(tokens.WEIGHT_BOLD)
        self._value.setFont(font)
        self._value.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; background: transparent;"
        )
        self._value.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._value)

        # 标签
        self._label = QLabel(label, self)
        self._label.setStyleSheet(
            f"color: {tokens.TEXT_SECONDARY}; font-size: {tokens.CAPTION}px; background: transparent;"
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._label)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class RhythmCard(QFrame):
    """节奏小卡（展示板风格：彩色圆底图标 + 标题 + 倒计时）。"""

    def __init__(
        self,
        icon: str,
        title: str,
        accent: str = tokens.PRIMARY,
        parent: Optional[QWidget] = None,
        icon_asset: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._accent = accent
        self.setObjectName("RhythmCard")
        self.setStyleSheet(
            f"QFrame#RhythmCard {{"
            f"  background-color: {tokens.BG_SURFACE};"
            f"  border: 1px solid {tokens.BORDER_SOFT};"
            f"  border-radius: {tokens.RADIUS_LG};"
            f"}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(8)

        # 顶部：圆底图标 + 标题
        top = QHBoxLayout()
        top.setSpacing(10)
        self._icon = QLabel(icon, self)
        self._icon.setFixedSize(34, 34)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setStyleSheet(
            f"QLabel {{ background-color: {rgba(accent, 0.14)};"
            f" border-radius: 17px; font-size: 15px; }}"
        )
        if icon_asset and assets.has(icon_asset):
            self._icon.setText("")
            self._icon.setPixmap(assets.pixmap(icon_asset, 24))
        top.addWidget(self._icon)
        self._title = QLabel(title, self)
        self._title.setStyleSheet(
            f"color: {tokens.TEXT_PRIMARY}; font-size: {tokens.SECONDARY}px;"
            f" font-weight: {tokens.WEIGHT_MEDIUM}; background: transparent;"
        )
        top.addWidget(self._title)
        top.addStretch(1)
        layout.addLayout(top)

        # 倒计时值
        self._value = QLabel("--", self)
        font = QFont()
        font.setPointSize(tokens.H2 - 2)
        font.setWeight(tokens.WEIGHT_BOLD)
        self._value.setFont(font)
        self._value.setStyleSheet(
            f"color: {self._accent}; background: transparent;"
        )
        layout.addWidget(self._value)

    def set_value(self, value: str) -> None:
        self._value.setText(value)
