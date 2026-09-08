"""位置编辑模式测试（V0.5.1：单顶层窗口 + VisualCue 子控件架构）。

背景：旧实现里 PositionEditor 与 VisualCuePopup 是两个独立 Tool/TopMost
顶层窗口，Windows 下 Z-order 不可靠，全屏遮罩会把眼睛盖住。
现在 VisualCue 在编辑模式下临时 reparent 为 PositionEditor 的子控件，
从结构上消除层级竞争。
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config.settings_store import JsonSettingsStore  # noqa: E402
from app.ui.position_editor import PositionEditor  # noqa: E402
from app.ui.visual_cue import VisualCuePopup  # noqa: E402


class TestPositionEditor(unittest.TestCase):
    """位置编辑器 + VisualCue 预览模式测试（offscreen）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._tmpdir = Path(tempfile.mkdtemp(prefix="eyerest_posedit_"))
        JsonSettingsStore(self._tmpdir / "config.json")  # 隔离数据目录副作用
        self.cue = VisualCuePopup()
        self.editor = PositionEditor(self.cue)

    def tearDown(self) -> None:
        if self.cue.is_previewing():
            self.cue.end_preview()
        self.editor.hide()
        self.cue.hide()

    # ------------------------------------------------------------------
    # reparent 架构
    # ------------------------------------------------------------------
    def test_preview_embeds_cue_into_editor(self) -> None:
        """start_preview(host) 后 VisualCue 必须成为编辑器子控件。"""
        self.assertIsNone(self.cue.parentWidget())
        self.assertTrue(self.cue.isWindow())

        self.editor.open_editor()

        self.assertIs(self.cue.parentWidget(), self.editor)
        self.assertFalse(self.cue.isWindow())  # 不再是顶层窗口
        self.assertTrue(self.cue.isVisible())  # 眼睛立即出现
        self.assertTrue(self.cue.is_previewing())
        self.assertFalse(self.cue.is_cue_visible())  # 预览不算提示展示

    def test_end_preview_restores_top_level(self) -> None:
        """end_preview 后 VisualCue 恢复为独立顶层 Tool 窗口。"""
        self.editor.open_editor()
        self.assertIs(self.cue.parentWidget(), self.editor)

        self.cue.end_preview()

        self.assertIsNone(self.cue.parentWidget())
        self.assertTrue(self.cue.isWindow())
        self.assertFalse(self.cue.is_previewing())
        self.assertFalse(self.cue.isVisible())
        # 恢复后的 windowFlags 必须仍是置顶 Tool（供正常提醒使用）
        from PySide6.QtCore import Qt

        flags = self.cue.windowFlags()
        self.assertTrue(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
        self.assertTrue(bool(flags & Qt.WindowType.Tool))
        self.assertFalse(bool(flags & Qt.WindowType.FramelessWindowHint) is False)

    def test_reopen_editor_after_close(self) -> None:
        """重复打开/关闭编辑器（reparent 来回切换）状态保持一致。"""
        for _ in range(2):
            self.editor.open_editor()
            self.assertIs(self.cue.parentWidget(), self.editor)
            self.assertTrue(self.cue.isVisible())
            self.cue.end_preview()
            self.assertIsNone(self.cue.parentWidget())
            self.assertTrue(self.cue.isWindow())

    # ------------------------------------------------------------------
    # 坐标换算（保存数据格式保持 x/y/monitor）
    # ------------------------------------------------------------------
    def test_current_position_from_child_widget(self) -> None:
        """子控件模式下 current_position() 返回相对显示器的局部坐标。"""
        self.editor.open_editor()
        self.cue.move(300, 300)

        x, y, monitor = self.cue.current_position()

        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertGreaterEqual(monitor, 0)

    def test_save_emits_monitor_relative_coords(self) -> None:
        """保存位置：信号发射 (x, y, monitor)，且编辑器关闭。"""
        self.editor.open_editor()
        self.cue.move(400, 400)

        received: list[tuple[int, int, int]] = []
        self.editor.position_saved.connect(lambda x, y, m: received.append((x, y, m)))
        closed: list[bool] = []
        self.editor.editor_closed.connect(lambda: closed.append(True))

        self.editor._on_save()

        self.assertEqual(len(received), 1)
        x, y, monitor = received[0]
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertTrue(self.editor.saved)
        self.assertEqual(closed, [True])
        self.assertFalse(self.cue.is_previewing())

    def test_cancel_keeps_position_flag_false(self) -> None:
        """取消编辑：不保存（saved=False），退出预览并关闭。"""
        self.editor.open_editor()

        closed: list[bool] = []
        self.editor.editor_closed.connect(lambda: closed.append(True))
        self.editor._on_cancel()

        self.assertFalse(self.editor.saved)
        self.assertEqual(closed, [True])
        self.assertFalse(self.cue.is_previewing())

    def test_editor_accepts_focus(self) -> None:
        """编辑器允许获取焦点（Esc/Enter 键盘操作可靠）。"""
        self.assertFalse(
            self.editor.testAttribute(__import__("PySide6").QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        )


if __name__ == "__main__":
    unittest.main()
