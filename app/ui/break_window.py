"""休息窗口（BreakWindow）与休息警告弹窗（BreakWarningPopup）。

实现非侵入式的全屏半透明休息提示：

* :class:`BreakWindow`：全屏半透明遮罩 + 中央倒计时卡片，支持跳过 / 延迟，
  倒计时结束自动完成。短休息 20 秒，长休息 300 秒。
* :class:`BreakWarningPopup`：休息前 30 秒的轻量警告弹窗，支持立即休息 / 延迟。

两个窗口均为置顶（``WindowStaysOnTopHint``）且模态（``ApplicationModal``），
确保用户在休息期间无法继续交互。所有信号均在 Qt 主线程发出。

模块在 offscreen 平台插件下也可正常实例化与测试（不依赖真实显示器）。
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QFont, QColor, QPainter, QPen, QBrush, QGuiApplication
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QGraphicsDropShadowEffect,
)

from app.config import defaults
from app.core.clock import Clock, default_clock
from app.i18n.translator import tr
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 圆形进度条
# ---------------------------------------------------------------------------
class CircularProgress(QWidget):
    """自定义圆形进度条，中心显示倒计时数字。

    使用 ``QPainter`` 绘制背景圆环与进度弧线，中心绘制 mm:ss 格式数字。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value: int = 0
        self._maximum: int = 1
        self._ring_color = QColor(255, 255, 255, 60)
        self._progress_color = QColor(64, 156, 255)
        self.setMinimumSize(200, 200)

    def set_range(self, maximum: int) -> None:
        """设置最大值（总秒数）。"""
        self._maximum = max(1, int(maximum))
        self.update()

    def set_value(self, value: int) -> None:
        """设置当前剩余秒数。"""
        self._value = max(0, int(value))
        self.update()

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        """格式化为 mm:ss。"""
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        pen_width = 10
        rect = QRectF(
            (self.width() - side) / 2 + pen_width,
            (self.height() - side) / 2 + pen_width,
            side - 2 * pen_width,
            side - 2 * pen_width,
        )

        # 背景圆环
        pen = QPen(self._ring_color, pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # 进度弧线：顺时针，从 12 点方向开始
        if self._maximum > 0:
            progress_ratio = self._value / self._maximum
            # Qt 角度单位为 1/16 度；逆时针为正，故取负值实现顺时针
            start_angle = 90 * 16  # 12 点方向
            span_angle = int(-progress_ratio * 360 * 16)
            progress_pen = QPen(self._progress_color, pen_width)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            painter.drawArc(rect, start_angle, span_angle)

        # 中心倒计时数字
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setPointSize(32)
        font.setBold(True)
        painter.setFont(font)
        text = self._format_seconds(self._value)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)


# ---------------------------------------------------------------------------
# 休息窗口
# ---------------------------------------------------------------------------
class BreakWindow(QWidget):
    """休息窗口 - 全屏半透明遮罩 + 中央倒计时卡片。

    信号:
        break_completed(str): 倒计时自然结束，参数为休息类型 ``short`` / ``long``。
        break_skipped(str): 用户跳过（按钮 / ESC / 关闭窗口），参数为休息类型。
        break_postponed(): 用户点击延迟。
    """

    break_completed = Signal(str)  # break_type
    break_skipped = Signal(str)
    break_postponed = Signal()

    def __init__(
        self,
        break_type: str = "short",
        duration: Optional[int] = None,
        max_postpone: int = defaults.MAX_POSTPONE,
        postpone_count: int = 0,
        postpone_duration: int = defaults.POSTPONE_DURATION,
        parent: Optional[QWidget] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        """初始化休息窗口。

        Args:
            break_type: 休息类型 ``short`` / ``long``。
            duration: 倒计时秒数；为 None 时按 break_type 取 defaults 对应值。
            max_postpone: 最大延迟次数（默认 2）。
            postpone_count: 当前已延迟次数（默认 0）。
            parent: 父窗口。
            clock: 时钟实现；默认 :data:`~app.core.clock.default_clock`。
                测试可注入 :class:`~app.core.clock.FakeClock` 精确控制时间。
        """
        super().__init__(parent)
        self.break_type = "long" if break_type == "long" else "short"

        if duration is None:
            duration = (
                defaults.LONG_BREAK_DURATION
                if self.break_type == "long"
                else defaults.SHORT_BREAK_DURATION
            )
        self.duration = max(1, int(duration))
        self.remaining = self.duration

        #: 单调时钟；倒计时基于「目标时刻」而非递减计数，避免 tick 漂移
        self._clock: Clock = clock or default_clock
        self._deadline: float = self._clock.deadline(self.duration)

        self.max_postpone = max_postpone
        self.postpone_count = postpone_count
        self.postpone_duration = max(60, int(postpone_duration))

        # 防止倒计时结束与用户操作重复触发
        self._finished: bool = False

        # 窗口标志：无边框 + 置顶 + 全屏 + 工具窗口（不在任务栏显示）
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 模态：阻塞与其他窗口的交互
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._build_ui()

        # 倒计时定时器
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_countdown)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """构建中央卡片 UI。"""
        # 外层布局：卡片居中
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        # 中央卡片
        card = QFrame(self)
        card.setObjectName("breakCard")
        card.setStyleSheet(
            """
            QFrame#breakCard {
                background-color: rgba(30, 30, 40, 235);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 40);
            }
            QLabel { color: #ffffff; background: transparent; }
            QPushButton {
                background-color: rgba(255, 255, 255, 25);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 60);
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 40); }
            QPushButton:pressed { background-color: rgba(255, 255, 255, 15); }
            """
        )
        # 阴影效果
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 180))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(48, 40, 48, 32)
        card_layout.setSpacing(16)

        # 顶部眼睛图标
        eye_icon = QLabel("👁️", card)
        eye_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eye_icon.setStyleSheet("font-size: 48px; background: transparent;")
        card_layout.addWidget(eye_icon)

        # 标题
        title_key = (
            "break.title_short" if self.break_type == "short" else "break.title_long"
        )
        self._title_label = QLabel(tr(title_key), card)
        title = self._title_label
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title.setFont(title_font)
        card_layout.addWidget(title)

        # 副标题
        subtitle_key = (
            "break.subtitle_short"
            if self.break_type == "short"
            else "break.subtitle_long"
        )
        self._subtitle_label = QLabel(tr(subtitle_key), card)
        subtitle = self._subtitle_label
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 14px; color: #cccccc;")
        card_layout.addWidget(subtitle)

        # 圆形进度条（含倒计时数字）
        self.progress = CircularProgress(card)
        self.progress.set_range(self.duration)
        self.progress.set_value(self.remaining)
        card_layout.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignCenter)

        # 延迟次数提示
        self.postpone_hint = QLabel("", card)
        self.postpone_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.postpone_hint.setStyleSheet("font-size: 12px; color: #999999;")
        card_layout.addWidget(self.postpone_hint)

        # 底部按钮行
        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addStretch(1)

        self.postpone_button = QPushButton(
            tr("common.postpone_minutes", minutes=self.postpone_duration // 60), card
        )
        self.postpone_button.clicked.connect(self._on_postpone)
        button_row.addWidget(self.postpone_button)

        self.skip_button = QPushButton(tr("common.skip"), card)
        self.skip_button.clicked.connect(self._on_skip)
        button_row.addWidget(self.skip_button)

        button_row.addStretch(1)
        card_layout.addLayout(button_row)

        outer.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addStretch(1)

        # 按钮创建完成后再更新延迟提示
        self._update_postpone_hint()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def configure(self, break_type: str, duration: Optional[int] = None) -> None:
        """重新配置休息类型与时长（复用窗口实例时调用）。

        Args:
            break_type: 休息类型 ``short`` / ``long``。
            duration: 倒计时秒数；为 None 时按 break_type 取 defaults 对应值。
        """
        self.break_type = "long" if break_type == "long" else "short"
        if duration is None:
            duration = (
                defaults.LONG_BREAK_DURATION
                if self.break_type == "long"
                else defaults.SHORT_BREAK_DURATION
            )
        self.duration = max(1, int(duration))
        self.remaining = self.duration
        self._finished = False
        # 重建 UI 以更新标题 / 副标题 / 进度范围
        # （简单做法：清空布局重建，避免复杂的逐个控件更新）
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """重建中央卡片 UI（内部使用）。"""
        # 清除旧布局
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._build_ui()

    def show_break(self) -> None:
        """显示休息窗口，开始倒计时。

        V1.0：记录**目标时刻**（monotonic deadline），每次 tick 用当前时间
        反推剩余量；tick 迟到或系统休眠唤醒后都不会累积漂移。
        """
        self._finished = False
        self.remaining = self.duration
        self._deadline = self._clock.deadline(self.duration)
        self.progress.set_range(self.duration)
        self.progress.set_value(self.remaining)
        self._update_postpone_hint()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.timer.start()
        logger.info("休息窗口已显示: type=%s duration=%ds", self.break_type, self.duration)

    def hide_break(self) -> None:
        """隐藏休息窗口，停止倒计时。"""
        self.timer.stop()
        self.hide()

    def update_countdown(self) -> None:
        """每秒更新倒计时。

        基于目标时刻计算剩余量（``deadline - now``），而非 ``remaining -= 1``
        —— 后者在系统繁忙、tick 堆积或休眠唤醒后会持续漂移。
        """
        if self._finished:
            return
        remaining = self._clock.remaining(self._deadline)
        self.remaining = int(math.ceil(remaining))
        if remaining <= 0:
            self.remaining = 0
            self.progress.set_value(0)
            self._on_complete()
            return
        self.progress.set_value(self.remaining)

    # ------------------------------------------------------------------
    # 信号触发
    # ------------------------------------------------------------------
    def _on_complete(self) -> None:
        """倒计时结束，休息完成。"""
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        logger.info("休息完成: type=%s", self.break_type)
        self.break_completed.emit(self.break_type)
        self.hide()

    def _on_skip(self) -> None:
        """用户跳过休息（按钮 / ESC / 关闭）。"""
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        logger.info("休息被跳过: type=%s", self.break_type)
        self.break_skipped.emit(self.break_type)
        self.hide()

    def _on_postpone(self) -> None:
        """用户延迟休息。"""
        if self._finished:
            return
        if self.postpone_count >= self.max_postpone:
            logger.info("已达最大延迟次数，忽略延迟操作")
            return
        self._finished = True
        self.timer.stop()
        self.postpone_count += 1
        logger.info(
            "休息被延迟: count=%d/%d", self.postpone_count, self.max_postpone
        )
        self.break_postponed.emit()
        self.hide()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _update_postpone_hint(self) -> None:
        """更新延迟按钮可见性与次数提示。"""
        remaining = self.max_postpone - self.postpone_count
        if remaining > 0:
            self.postpone_button.setVisible(True)
            self.postpone_hint.setText(tr("break.postpone_hint", count=remaining))
        else:
            self.postpone_button.setVisible(False)
            self.postpone_hint.setText(tr("break.postpone_max"))

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """绘制半透明遮罩。"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 178))  # rgba(0,0,0,0.7)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """ESC 键跳过。"""
        if event.key() == Qt.Key.Key_Escape:
            self._on_skip()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口关闭（X / Alt+F4）视为跳过。"""
        if not self._finished:
            self._on_skip()
            # 由 _on_skip 调用 hide() 并忽略本次关闭
            event.ignore()
        else:
            event.accept()

    # ------------------------------------------------------------------
    # 外部状态同步
    # ------------------------------------------------------------------
    def set_postpone_count(self, count: int) -> None:
        """同步已延迟次数（供上层在重新显示前设置）。"""
        self.postpone_count = max(0, min(count, self.max_postpone))
        self._update_postpone_hint()


# ---------------------------------------------------------------------------
# 休息警告弹窗
# ---------------------------------------------------------------------------
class BreakWarningPopup(QWidget):
    """休息前警告弹窗（30 秒前）。

    信号:
        warning_accepted(): 用户点击"立即休息"或 30 秒倒计时结束。
        warning_postponed(): 用户点击"延迟"。
    """

    warning_accepted = Signal()
    warning_postponed = Signal()

    def __init__(
        self,
        break_type: str = "short",
        duration: int = defaults.WARNING_DURATION,
        max_postpone: int = defaults.MAX_POSTPONE,
        parent: Optional[QWidget] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        super().__init__(parent)
        self.break_type = "long" if break_type == "long" else "short"
        self.duration = max(1, int(duration))
        self.remaining = self.duration
        self.max_postpone = max_postpone
        self.postpone_count = 0
        self._finished = False
        #: 单调时钟；倒计时基于「目标时刻」而非递减计数
        self._clock: Clock = clock or default_clock
        self._deadline: float = self._clock.deadline(self.duration)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        # 半透明背景
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        card = QFrame(self)
        card.setObjectName("warningCard")
        card.setStyleSheet(
            """
            QFrame#warningCard {
                background-color: rgba(40, 40, 55, 245);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 40);
            }
            QLabel { color: #ffffff; background: transparent; }
            QPushButton {
                background-color: rgba(255, 255, 255, 25);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 60);
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 40); }
            """
        )
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(10)

        title = QLabel(tr("break.warning_title"), card)
        self._title_label = title
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        card_layout.addWidget(title)

        subtitle_key = (
            "break.warning_subtitle_short"
            if self.break_type == "short"
            else "break.warning_subtitle_long"
        )
        self._subtitle_label = QLabel(tr(subtitle_key), card)
        subtitle = self._subtitle_label
        subtitle.setStyleSheet("font-size: 13px; color: #cccccc;")
        card_layout.addWidget(subtitle)

        self.countdown_label = QLabel(tr("break.warning_countdown", seconds=self.remaining), card)
        self.countdown_label.setStyleSheet("font-size: 22px; font-weight: bold; color: #409cff;")
        self.countdown_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.countdown_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch(1)

        self.postpone_button = QPushButton(tr("common.postpone"), card)
        self.postpone_button.clicked.connect(self._on_postpone)
        button_row.addWidget(self.postpone_button)

        self.accept_button = QPushButton(tr("common.rest_now"), card)
        self.accept_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.accept_button)

        card_layout.addLayout(button_row)
        layout.addWidget(card)

        # 固定窗口尺寸并居中
        self.setFixedSize(320, 200)

    def configure(self, break_type: str, duration: Optional[int] = None) -> None:
        """重新配置休息类型与警告时长（复用实例时调用）。

        Args:
            break_type: 休息类型 ``short`` / ``long``。
            duration: 警告倒计时秒数；为 None 时保持当前值。
        """
        self.break_type = "long" if break_type == "long" else "short"
        if duration is not None:
            self.duration = max(1, int(duration))
        self.remaining = self.duration
        self._finished = False
        # 重建 UI 以更新标题 / 副标题（含语言切换）
        self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        """重建警告弹窗 UI（内部使用）。"""
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._build_ui()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    def show_warning(self, postpone_count: int = 0) -> None:
        """显示警告弹窗并开始倒计时。"""
        self._finished = False
        self.postpone_count = max(0, postpone_count)
        self.remaining = self.duration
        self._deadline = self._clock.deadline(self.duration)
        self.countdown_label.setText(tr("break.warning_countdown", seconds=self.remaining))
        self._update_postpone_button()
        # 居中显示
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
        self.show()
        self.raise_()
        self.activateWindow()
        self.timer.start()
        logger.info("休息警告弹窗已显示: type=%s", self.break_type)

    def hide_warning(self) -> None:
        """隐藏警告弹窗。"""
        self.timer.stop()
        self.hide()

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        """每秒推进警告倒计时（基于目标时刻，不递减计数）。"""
        if self._finished:
            return
        remaining = self._clock.remaining(self._deadline)
        self.remaining = int(math.ceil(remaining))
        if remaining <= 0:
            self.remaining = 0
            self.countdown_label.setText(tr("break.warning_starting"))
            self._on_accept()
            return
        self.countdown_label.setText(tr("break.warning_countdown", seconds=self.remaining))

    def _on_accept(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.timer.stop()
        logger.info("休息警告被接受: type=%s", self.break_type)
        self.warning_accepted.emit()
        self.hide()

    def _on_postpone(self) -> None:
        if self._finished:
            return
        if self.postpone_count >= self.max_postpone:
            logger.info("警告阶段已达最大延迟次数")
            self._on_accept()
            return
        self._finished = True
        self.timer.stop()
        self.postpone_count += 1
        logger.info("休息警告被延迟: count=%d/%d", self.postpone_count, self.max_postpone)
        self.warning_postponed.emit()
        self.hide()

    def _update_postpone_button(self) -> None:
        if self.postpone_count >= self.max_postpone:
            self.postpone_button.setVisible(False)
        else:
            self.postpone_button.setVisible(True)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.key() == Qt.Key.Key_Escape:
            self._on_accept()
            event.accept()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if not self._finished:
            self._on_accept()
            event.ignore()
        else:
            event.accept()
