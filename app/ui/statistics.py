"""Statistics 统计页。

展示今日汇总卡片、7 天用眼/休息趋势图（QPainter 手绘柱状图）
以及详细数据表。
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.i18n import get_translator, tr
from app.services.statistics_service import StatisticsService
from app.utils.logger import get_logger

_log = get_logger(__name__)

# 详细数据表表头 i18n 键
_TABLE_HEADER_KEYS = [
    "stats.col_date",
    "stats.col_active",
    "stats.col_idle",
    "stats.col_short",
    "stats.col_long",
    "stats.col_skipped",
]


# ======================================================================
# BarChartWidget — QPainter 手绘柱状图
# ======================================================================
class BarChartWidget(QWidget):
    """简单柱状图组件，使用 QPainter 绘制。

    支持多组数据的分组柱状图，自动计算 Y 轴刻度与范围。

    Attributes:
        data: 数据列表，每组为一个序列 ``[v0, v1, ...]``。
        labels: X 轴分类标签列表。
        colors: 每组数据对应的颜色（QColor 或 hex 字符串）。
        y_label: Y 轴单位说明（如 "小时" / "次"）。
        legend_labels: 图例标签列表，与 data 一一对应。
    """

    def __init__(
        self,
        data: list[list[float]],
        labels: list[str],
        colors: list[Any],
        parent: Optional[QWidget] = None,
        y_label: str = "",
        legend_labels: Optional[list[str]] = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._labels = labels
        self._colors = [self._to_color(c) for c in colors]
        self._y_label = y_label
        self._legend_labels = legend_labels or [
            tr("stats.series", i=i) for i in range(len(data))
        ]
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    # 数据更新
    # ------------------------------------------------------------------
    def set_data(
        self,
        data: list[list[float]],
        labels: list[str],
        colors: Optional[list[Any]] = None,
        legend_labels: Optional[list[str]] = None,
    ) -> None:
        """更新图表数据并触发重绘。"""
        self._data = data
        self._labels = labels
        if colors is not None:
            self._colors = [self._to_color(c) for c in colors]
        if legend_labels is not None:
            self._legend_labels = legend_labels
        self.update()

    def set_y_label(self, label: str) -> None:
        """设置 Y 轴单位标签。"""
        self._y_label = label
        self.update()

    def set_legend_labels(self, labels: list[str]) -> None:
        """设置图例标签（语言切换时刷新）。"""
        self._legend_labels = labels
        self.update()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """使用 QPainter 绘制柱状图。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        # 边距：左留白给 Y 轴刻度，下留白给 X 轴标签，上留白给标题/图例
        margin_left = 56
        margin_right = 20
        margin_top = 24
        margin_bottom = 40

        plot_left = rect.left() + margin_left
        plot_right = rect.right() - margin_right
        plot_top = rect.top() + margin_top
        plot_bottom = rect.bottom() - margin_bottom
        plot_width = plot_right - plot_left
        plot_height = plot_bottom - plot_top

        if plot_width <= 0 or plot_height <= 0:
            painter.end()
            return

        # 计算 Y 轴最大值
        max_value = self._compute_max_value()
        # 绘制 Y 轴网格与刻度
        self._draw_y_axis(painter, plot_left, plot_right, plot_top, plot_bottom, max_value)

        # 绘制柱子
        self._draw_bars(painter, plot_left, plot_right, plot_top, plot_bottom, plot_width, plot_height, max_value)

        # 绘制 X 轴标签
        self._draw_x_labels(painter, plot_left, plot_right, plot_bottom)

        # 绘制图例
        self._draw_legend(painter, rect.left() + margin_left, rect.top() + 4)

        painter.end()

    # ------------------------------------------------------------------
    # 绘制辅助方法
    # ------------------------------------------------------------------
    def _compute_max_value(self) -> float:
        """计算数据最大值（含 10% 余量，保证柱子不顶到顶部）。"""
        max_value = 0.0
        for series in self._data:
            for v in series:
                if v > max_value:
                    max_value = v
        if max_value <= 0:
            return 1.0
        # 向上取整到合适的刻度
        return max_value * 1.15

    def _draw_y_axis(
        self,
        painter: QPainter,
        left: int,
        right: int,
        top: int,
        bottom: int,
        max_value: float,
    ) -> None:
        """绘制 Y 轴网格线与刻度标签。"""
        # 背景
        painter.fillRect(left, top, right - left, bottom - top, QColor("#fafafa"))

        grid_pen = QPen(QColor("#e0e0e0"), 1, Qt.PenStyle.DashLine)
        label_pen = QPen(QColor("#888888"))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        # 5 条网格线
        steps = 5
        for i in range(steps + 1):
            y = bottom - (bottom - top) * i / steps
            value = max_value * i / steps
            painter.setPen(grid_pen)
            painter.drawLine(left, int(y), right, int(y))
            # 刻度标签
            painter.setPen(label_pen)
            text = self._format_y_label(value)
            painter.drawText(
                0,
                int(y) - 8,
                left - 8,
                16,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                text,
            )

        # 轴线
        axis_pen = QPen(QColor("#cccccc"), 1)
        painter.setPen(axis_pen)
        painter.drawLine(left, top, left, bottom)
        painter.drawLine(left, bottom, right, bottom)

    def _draw_bars(
        self,
        painter: QPainter,
        left: int,
        right: int,
        top: int,
        bottom: int,
        plot_width: int,
        plot_height: int,
        max_value: float,
    ) -> None:
        """绘制分组柱状图。"""
        categories = len(self._labels)
        if categories == 0:
            return
        series_count = len(self._data)
        if series_count == 0:
            return

        # 每个分类占据的宽度
        category_width = plot_width / categories
        # 每个柱子的宽度
        bar_gap = 2
        bar_width = max(4, int(category_width * 0.7 / series_count) - bar_gap)

        for cat_idx in range(categories):
            # 分类中心点
            cat_center = left + category_width * (cat_idx + 0.5)
            # 每组柱子总宽度
            total_bars_width = bar_width * series_count + bar_gap * (series_count - 1)
            start_x = cat_center - total_bars_width / 2

            for series_idx in range(series_count):
                if cat_idx >= len(self._data[series_idx]):
                    continue
                value = self._data[series_idx][cat_idx]
                if value < 0:
                    value = 0.0
                bar_height = (value / max_value) * plot_height if max_value > 0 else 0
                bar_height = max(0, bar_height)

                x = int(start_x + (bar_width + bar_gap) * series_idx)
                y = int(bottom - bar_height)

                color = self._colors[series_idx] if series_idx < len(self._colors) else QColor("#888888")
                # 柱子圆角
                path = QPainterPath()
                path.addRoundedRect(x, y, bar_width, int(bar_height), 2, 2)
                painter.fillPath(path, color)

    def _draw_x_labels(
        self,
        painter: QPainter,
        left: int,
        right: int,
        bottom: int,
    ) -> None:
        """绘制 X 轴分类标签。"""
        categories = len(self._labels)
        if categories == 0:
            return
        plot_width = right - left
        category_width = plot_width / categories

        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#666666")))

        for i, label in enumerate(self._labels):
            center = left + category_width * (i + 0.5)
            painter.drawText(
                int(center - 20),
                bottom + 6,
                40,
                16,
                Qt.AlignmentFlag.AlignCenter,
                str(label),
            )

    def _draw_legend(self, painter: QPainter, x: int, y: int) -> None:
        """绘制图例。"""
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        cursor_x = x
        for i, label in enumerate(self._legend_labels):
            if i >= len(self._colors):
                break
            color = self._colors[i]
            painter.fillRect(cursor_x, y, 12, 12, color)
            painter.setPen(QPen(QColor("#555555")))
            painter.drawText(cursor_x + 16, y + 10, str(label))
            # 估算文字宽度后移动游标
            text_w = painter.fontMetrics().horizontalAdvance(str(label))
            cursor_x += 16 + text_w + 16

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @staticmethod
    def _to_color(color: Any) -> QColor:
        """将颜色值转换为 QColor。"""
        if isinstance(color, QColor):
            return color
        if isinstance(color, str):
            return QColor(color)
        if isinstance(color, tuple) and len(color) >= 3:
            return QColor(*color[:3])
        return QColor("#888888")

    def _format_y_label(self, value: float) -> str:
        """格式化 Y 轴刻度标签。"""
        if self._y_label == "hours":
            return f"{value:.1f}h"
        if self._y_label == "count":
            return f"{int(round(value))}"
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return f"{value:.1f}"


# ======================================================================
# StatisticsPage — 统计页
# ======================================================================
class StatisticsPage(QWidget):
    """Statistics 统计页。

    布局：
        1. 顶部：标题 + 刷新按钮
        2. 汇总卡片行（4 个卡片横排）
        3. 用眼趋势图（最近 7 天柱状图）
        4. 休息趋势图（最近 7 天柱状图）
        5. 详细数据表（最近 7 天）
    """

    def __init__(
        self,
        statistics_service: StatisticsService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = statistics_service
        self._today_stats: dict[str, Any] = {}
        # 刷新按钮状态：idle / ok / fail（语言切换时还原对应文案）
        self._refresh_state: str = "idle"
        self._setup_ui()
        self.refresh()
        # 语言切换时刷新静态文本
        get_translator().add_listener(self._on_language_changed)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _setup_ui(self) -> None:
        """构建 UI（内部方法，供构造函数调用）。"""
        self.setup_ui()

    def setup_ui(self) -> None:
        """构建 UI。"""
        self.setStyleSheet(
            """
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px 2px 4px 0;
            }
            QScrollBar::handle:vertical {
                background: #C8C8C8;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #A8A8A8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar:horizontal { height: 0; }
            """
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # ---- 标题行 + 刷新按钮 ----
        header_layout = QHBoxLayout()
        self._title_label = QLabel(tr("stats.title"))
        self._title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #333;")
        header_layout.addWidget(self._title_label)
        header_layout.addStretch(1)

        self._refresh_btn = QPushButton(tr("stats.refresh"))
        self._refresh_btn.setToolTip(tr("stats.refresh_tooltip"))
        self._refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(self._refresh_btn)
        root_layout.addLayout(header_layout)

        # ---- 可滚动内容区 ----
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        # 汇总卡片
        self._cards_layout = QHBoxLayout()
        self._cards_layout.setSpacing(12)
        content_layout.addLayout(self._cards_layout)
        self._build_summary_cards()

        # 区域标题缓存：(QLabel, i18n 键)，语言切换时刷新
        self._section_titles: list[tuple[QLabel, str]] = []

        # 用眼趋势图
        usage_section = self._build_section("stats.usage_trend_title")
        self._usage_chart = BarChartWidget(
            data=[[0.0] * 7, [0.0] * 7],
            labels=[""] * 7,
            colors=["#4CAF50", "#9E9E9E"],
            y_label="hours",
            legend_labels=[tr("stats.legend_active"), tr("stats.legend_idle")],
        )
        usage_section.layout().addWidget(self._usage_chart)
        content_layout.addWidget(usage_section)

        # 休息趋势图
        break_section = self._build_section("stats.break_trend_title")
        self._break_chart = BarChartWidget(
            data=[[0] * 7, [0] * 7, [0] * 7],
            labels=[""] * 7,
            colors=["#2196F3", "#9C27B0", "#F44336"],
            y_label="count",
            legend_labels=[
                tr("stats.legend_short"),
                tr("stats.legend_long"),
                tr("stats.legend_skipped"),
            ],
        )
        break_section.layout().addWidget(self._break_chart)
        content_layout.addWidget(break_section)

        # 详细数据表
        table_section = self._build_section("stats.details_title")
        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels(
            [tr(key) for key in _TABLE_HEADER_KEYS]
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table_section.layout().addWidget(self._table)
        content_layout.addWidget(table_section)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        root_layout.addWidget(scroll, 1)

    def _build_section(self, title_key: str) -> QFrame:
        """构建一个带标题的卡片区域（title_key 为 i18n 键）。"""
        frame = QFrame(self)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            """
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
            }
            """
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        title_label = QLabel(tr(title_key))
        title_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #444;")
        layout.addWidget(title_label)
        self._section_titles.append((title_label, title_key))
        return frame

    def _build_summary_cards(self) -> None:
        """构建汇总卡片（4 个横排）。"""
        # 清空已有卡片
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cards_config = [
            ("stats.card_active", "0h 0m", "#4CAF50"),
            ("stats.card_breaks", "0", "#2196F3"),
            ("stats.card_skipped", "0", "#F44336"),
            ("stats.card_avg_work", "0m", "#9C27B0"),
        ]

        self._card_values: list[QLabel] = []
        self._card_titles: list[tuple[QLabel, str]] = []
        for title_key, value, color in cards_config:
            card = self._create_card(title_key, value, color)
            self._cards_layout.addWidget(card)

    def _create_card(self, title_key: str, value: str, accent_color: str) -> QFrame:
        """创建单个汇总卡片（title_key 为 i18n 键）。"""
        card = QFrame(self)
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet(
            f"""
            QFrame {{
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-left: 4px solid {accent_color};
                border-radius: 6px;
            }}
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_label = QLabel(tr(title_key))
        title_label.setStyleSheet("font-size: 12px; color: #888; background: transparent; border: none;")
        layout.addWidget(title_label)

        value_label = QLabel(value)
        value_label.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {accent_color}; background: transparent; border: none;"
        )
        layout.addWidget(value_label)

        self._card_values.append(value_label)
        self._card_titles.append((title_label, title_key))
        return card

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def _refresh_btn_text(self) -> str:
        """按当前刷新状态返回本地化按钮文案。"""
        if self._refresh_state == "ok":
            return tr("stats.refreshed")
        if self._refresh_state == "fail":
            return tr("stats.refresh_failed")
        return tr("stats.refresh")

    def refresh(self) -> None:
        """刷新统计数据。"""
        try:
            # 先聚合今天的统计（确保数据最新）
            self._service.aggregate_daily_stat()
            today = self._service.get_daily_stats()
            self._today_stats = today
            self._update_summary_cards(today)
            self._update_usage_chart()
            self._update_break_chart()
            self._update_table()
            self._refresh_state = "ok"
            self._refresh_btn.setText(self._refresh_btn_text())
            _log.debug("StatisticsPage 数据已刷新")
        except Exception:
            _log.exception("刷新统计数据失败")
            self._refresh_state = "fail"
            self._refresh_btn.setText(self._refresh_btn_text())

    def _update_summary_cards(self, stats: dict[str, Any]) -> None:
        """更新汇总卡片数值。"""
        active_seconds = stats.get("total_active_seconds", 0.0)
        active_hours = active_seconds / 3600.0
        active_str = f"{int(active_hours)}h {int((active_hours % 1) * 60)}m"

        total_breaks = stats.get("total_breaks", 0)
        skipped = stats.get("total_skipped_breaks", 0)
        avg_work = stats.get("avg_work_duration", 0.0)
        avg_work_str = f"{int(avg_work / 60)}m"

        values = [active_str, str(total_breaks), str(skipped), avg_work_str]
        for label, value in zip(self._card_values, values):
            label.setText(value)

    def _update_usage_chart(self) -> None:
        """更新用眼趋势图。"""
        trend = self._service.get_usage_trend(days=7)
        labels = trend["dates"]
        # 转换为小时
        active_hours = [s / 3600.0 for s in trend["active_seconds"]]
        idle_hours = [s / 3600.0 for s in trend["idle_seconds"]]
        self._usage_chart.set_data(
            data=[active_hours, idle_hours],
            labels=labels,
            colors=["#4CAF50", "#9E9E9E"],
            legend_labels=[tr("stats.legend_active"), tr("stats.legend_idle")],
        )

    def _update_break_chart(self) -> None:
        """更新休息趋势图。"""
        trend = self._service.get_break_trend(days=7)
        labels = trend["dates"]
        short = trend["short_breaks"]
        long_ = trend["long_breaks"]
        skipped = trend["skipped"]
        self._break_chart.set_data(
            data=[short, long_, skipped],
            labels=labels,
            colors=["#2196F3", "#9C27B0", "#F44336"],
            legend_labels=[
                tr("stats.legend_short"),
                tr("stats.legend_long"),
                tr("stats.legend_skipped"),
            ],
        )

    def _update_table(self) -> None:
        """更新详细数据表。"""
        stats = self._service.get_weekly_stats()
        self._table.setRowCount(len(stats))
        for row, stat in enumerate(stats):
            date_str = stat.get("date", "")
            # 格式化为 MM-DD
            try:
                from datetime import datetime
                date_display = datetime.strptime(date_str, "%Y-%m-%d").strftime("%m-%d")
            except (ValueError, TypeError):
                date_display = date_str

            active_hours = stat.get("total_active_seconds", 0.0) / 3600.0
            idle_hours = stat.get("total_idle_seconds", 0.0) / 3600.0

            values = [
                date_display,
                f"{active_hours:.2f}h",
                f"{idle_hours:.2f}h",
                str(stat.get("total_short_breaks", 0)),
                str(stat.get("total_long_breaks", 0)),
                str(stat.get("total_skipped_breaks", 0)),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """语言变化时重新设置所有静态文本。"""
        self._title_label.setText(tr("stats.title"))
        self._refresh_btn.setText(self._refresh_btn_text())
        self._refresh_btn.setToolTip(tr("stats.refresh_tooltip"))
        for label, key in getattr(self, "_section_titles", []):
            label.setText(tr(key))
        for label, key in getattr(self, "_card_titles", []):
            label.setText(tr(key))
        self._table.setHorizontalHeaderLabels(
            [tr(key) for key in _TABLE_HEADER_KEYS]
        )
        self._usage_chart.set_legend_labels(
            [tr("stats.legend_active"), tr("stats.legend_idle")]
        )
        self._break_chart.set_legend_labels(
            [
                tr("stats.legend_short"),
                tr("stats.legend_long"),
                tr("stats.legend_skipped"),
            ]
        )

    def _on_language_changed(self, lang: str) -> None:
        """语言变化监听回调。"""
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """移除语言监听（幂等，可重复调用）。"""
        get_translator().remove_listener(self._on_language_changed)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """页面关闭时移除语言监听。"""
        self.cleanup()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 公共辅助
    # ------------------------------------------------------------------
    def get_today_stats(self) -> dict[str, Any]:
        """返回当前缓存的今日统计数据（供测试使用）。"""
        return dict(self._today_stats)
