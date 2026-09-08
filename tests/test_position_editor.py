"""位置编辑器测试（V0.5.1 第二轮：PositionEditor + VisualCuePreview 分离架构）。

背景：旧实现两次尝试（多次 raise / reparent VisualCuePopup）在 Windows
真机上均失败。教训——Qt 的 isVisible()/isWindow() 只反映逻辑状态，
不能证明透明顶层窗口动态转换子控件后的真实渲染。因此本架构：

* 编辑模式使用独立的 :class:`VisualCuePreview`（普通 QWidget 子控件）
* Preview 无任何窗口 flag（非 Tool / 非 StaysOnTop / 非 Frameless）
* PositionEditor 是唯一顶层窗口
* Preview 静止显示，几何必须落在编辑器区域内
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.position_editor import PositionEditor  # noqa: E402
from app.ui.visual_cue_preview import VisualCuePreview  # noqa: E402


class TestPositionEditor(unittest.TestCase):
    """PositionEditor + VisualCuePreview 分离架构行为。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.saved_payloads: list[tuple[int, int, int]] = []
        self.closed_count = 0
        self.editor = PositionEditor()
        self.editor.position_saved.connect(lambda x, y, m: self.saved_payloads.append((x, y, m)))
        self.editor.editor_closed.connect(lambda: setattr(self, "closed_count", self.closed_count + 1))

    def tearDown(self) -> None:
        self.editor.hide()
        self.editor.deleteLater()

    # ------------------------------------------------------------------
    # 架构约束（防止回退到旧的顶层窗口方案）
    # ------------------------------------------------------------------
    def test_preview_is_plain_child_widget(self) -> None:
        """Preview 必须是编辑器的普通子控件，绝非顶层窗口。"""
        self.editor.open_editor()
        preview = self.editor.preview
        self.assertIsInstance(preview, VisualCuePreview)
        self.assertIsNotNone(preview)
        self.assertIs(preview.parentWidget(), self.editor)
        self.assertFalse(preview.isWindow())

    def test_preview_has_no_window_flags(self) -> None:
        """Preview 禁止携带 Tool / StaysOnTop / Frameless 窗口属性。"""
        self.editor.open_editor()
        flags = self.editor.preview.windowFlags()
        self.assertFalse(bool(flags & Qt.WindowType.Tool))
        self.assertFalse(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.assertFalse(bool(flags & Qt.WindowType.FramelessWindowHint))

    def test_editor_is_single_top_level_window(self) -> None:
        """编辑器自身是允许焦点的顶层窗口（不设 WA_ShowWithoutActivating）。"""
        self.assertFalse(self.editor.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.editor.open_editor()
        self.assertTrue(self.editor.isWindow())

    def test_preview_visible_within_editor_bounds(self) -> None:
        """打开后 Preview 可见且几何完全落在编辑器区域内。"""
        self.editor.setGeometry(0, 0, 1920, 1080)
        self.editor.open_editor()
        preview = self.editor.preview
        self.assertTrue(preview.isVisible())
        self.assertGreater(preview.width(), 0)
        self.assertGreater(preview.height(), 0)
        geo = preview.geometry()
        self.assertGreaterEqual(geo.left(), 0)
        self.assertGreaterEqual(geo.top(), 0)
        self.assertLessEqual(geo.right(), self.editor.width() - 1)
        self.assertLessEqual(geo.bottom(), self.editor.height() - 1)

    # ------------------------------------------------------------------
    # 位置语义
    # ------------------------------------------------------------------
    def test_preview_starts_from_custom_position(self) -> None:
        """已有 custom 坐标时，预览从上次保存的位置开始（编辑器坐标系）。"""
        self.editor.setGeometry(0, 0, 1920, 1080)
        # custom (400, 300) monitor 0：编辑器在虚拟桌面原点 → 编辑器坐标同值
        self.editor.open_editor(
            position_mode="custom", pos_x=400, pos_y=300, monitor=0
        )
        pos = self.editor.preview.pos()
        self.assertLess(abs(pos.x() - 400), 64)
        self.assertLess(abs(pos.y() - 300), 64)

    def test_save_emits_monitor_relative_coords(self) -> None:
        """保存时把预览位置换算为 monitor-relative 坐标并发出信号。"""
        self.editor.setGeometry(0, 0, 1920, 1080)
        self.editor.open_editor(position_mode="custom", pos_x=500, pos_y=400, monitor=0)
        self.editor._on_save()
        self.assertEqual(len(self.saved_payloads), 1)
        x, y, monitor = self.saved_payloads[0]
        self.assertEqual(monitor, 0)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertTrue(self.editor.saved)
        self.assertEqual(self.closed_count, 1)
        # 保存后预览应隐藏
        self.assertFalse(self.editor.preview.isVisible())

    def test_cancel_does_not_save(self) -> None:
        """取消：不发出 position_saved，但发出 editor_closed。"""
        self.editor.open_editor()
        self.editor._on_cancel()
        self.assertEqual(self.saved_payloads, [])
        self.assertFalse(self.editor.saved)
        self.assertEqual(self.closed_count, 1)

    def test_repeated_open_close(self) -> None:
        """重复开关编辑器：预览组件复用且状态正确。"""
        self.editor.open_editor()
        first_preview = self.editor.preview
        self.editor.close_editor()
        self.editor.open_editor()
        self.assertIs(self.editor.preview, first_preview)
        self.assertTrue(first_preview.isVisible())
        self.assertEqual(self.closed_count, 1)

    def test_drag_moves_preview_within_bounds(self) -> None:
        """拖动只移动预览，边界保护生效（不会拖出编辑器区域）。"""
        self.editor.setGeometry(0, 0, 1920, 1080)
        self.editor.open_editor()
        preview = self.editor.preview
        before = preview.pos()
        # 模拟按下-移动-松开（显式指定 global 坐标，offscreen 下必要）
        from PySide6.QtGui import QMouseEvent

        press_global = preview.mapToGlobal(QPoint(preview.width() // 2, preview.height() // 2))
        move_global = press_global + QPoint(60, 40)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(preview.mapFromGlobal(press_global)),
            QPointF(press_global),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        preview.mousePressEvent(press)
        self.assertIsNotNone(preview._drag_offset)
        preview.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove, QPointF(preview.mapFromGlobal(move_global)),
                QPointF(move_global),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )
        )
        preview.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(preview.mapFromGlobal(move_global)),
                QPointF(move_global),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            )
        )
        after = preview.pos()
        self.assertNotEqual(before, after)
        # 边界保护
        margin = preview.EDGE_MARGIN
        self.assertGreaterEqual(after.x(), margin)
        self.assertGreaterEqual(after.y(), margin)
        self.assertLessEqual(after.x(), self.editor.width() - preview.width() - margin)
        self.assertLessEqual(after.y(), self.editor.height() - preview.height() - margin)

    def test_preview_shows_full_cue(self) -> None:
        """预览显示完整提示（图标 + 文字），皮肤/强度可配置。"""
        self.editor.open_editor(skin="cartoon", intensity="prominent")
        preview = self.editor.preview
        self.assertTrue(preview._text.isVisibleTo(preview))
        self.assertEqual(preview._icon.text(), "👀")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
