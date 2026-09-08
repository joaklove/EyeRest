"""全屏检测器（FullscreenDetector）单元测试。

由于 ``is_fullscreen()`` 依赖 Windows API，测试通过 mock 模块级 Win32
函数来覆盖各类分支，并用纯函数 ``_covers_monitor`` 验证判定逻辑本身。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.windows import fullscreen_detector


# ---------------------------------------------------------------------------
# 测试辅助：通过 ctypes.byref 返回的 CArgObject 回填结构体
# ---------------------------------------------------------------------------
def _fill_rect(rect_ref, left: int, top: int, right: int, bottom: int) -> None:
    """回填 ``wintypes.RECT`` 结构体（``rect_ref`` 由 ``ctypes.byref`` 传入）。"""
    rect = rect_ref._obj
    rect.left = left
    rect.top = top
    rect.right = right
    rect.bottom = bottom


def _fill_monitor_info(info_ref, left: int, top: int, right: int, bottom: int) -> None:
    """回填 ``MONITORINFO`` 结构体的 ``rcMonitor``。"""
    info = info_ref._obj
    info.rcMonitor.left = left
    info.rcMonitor.top = top
    info.rcMonitor.right = right
    info.rcMonitor.bottom = bottom
    # rcWork 与 rcMonitor 保持一致（当前实现只读取 rcMonitor）
    info.rcWork.left = left
    info.rcWork.top = top
    info.rcWork.right = right
    info.rcWork.bottom = bottom


# ---------------------------------------------------------------------------
# _covers_monitor 纯函数测试
# ---------------------------------------------------------------------------
class TestCoversMonitor(unittest.TestCase):
    """窗口矩形覆盖显示器矩形的判定逻辑。"""

    def test_exact_cover_returns_true(self) -> None:
        self.assertTrue(
            fullscreen_detector._covers_monitor(0, 0, 1920, 1080, 0, 0, 1920, 1080)
        )

    def test_maximized_with_taskbar_returns_false(self) -> None:
        # 最大化窗口通常只覆盖工作区（rcWork），底部留任务栏，不应判为全屏
        self.assertFalse(
            fullscreen_detector._covers_monitor(0, 0, 1920, 1040, 0, 0, 1920, 1080)
        )

    def test_small_window_returns_false(self) -> None:
        self.assertFalse(
            fullscreen_detector._covers_monitor(100, 100, 800, 600, 0, 0, 1920, 1080)
        )

    def test_window_larger_than_monitor_returns_true(self) -> None:
        self.assertTrue(
            fullscreen_detector._covers_monitor(-5, -5, 1930, 1090, 0, 0, 1920, 1080)
        )

    def test_tolerance_boundary(self) -> None:
        tol = fullscreen_detector._TOLERANCE
        # 误差恰好在 tolerance 内 → 全屏
        self.assertTrue(
            fullscreen_detector._covers_monitor(
                tol, tol, 1920 - tol, 1080 - tol, 0, 0, 1920, 1080, tolerance=tol
            )
        )
        # 误差超出 tolerance → 非全屏
        self.assertFalse(
            fullscreen_detector._covers_monitor(
                tol + 1, tol + 1, 1920 - (tol + 1), 1080 - (tol + 1),
                0, 0, 1920, 1080, tolerance=tol,
            )
        )


# ---------------------------------------------------------------------------
# is_fullscreen 的 Win32 调用流程测试（mock API）
# ---------------------------------------------------------------------------
class TestIsFullscreen(unittest.TestCase):
    """is_fullscreen 的各类分支（mock Win32 API）。"""

    def test_non_windows_returns_false(self) -> None:
        with patch.object(fullscreen_detector.sys, "platform", "linux"):
            self.assertFalse(fullscreen_detector.is_fullscreen())

    def test_no_foreground_window_returns_false(self) -> None:
        with patch.object(fullscreen_detector, "_GetForegroundWindow", return_value=0):
            self.assertFalse(fullscreen_detector.is_fullscreen())

    def test_shell_window_returns_false(self) -> None:
        def fake_class(hwnd, buf, n) -> int:
            buf.value = "Progman"
            return 7

        with patch.object(
            fullscreen_detector, "_GetForegroundWindow", return_value=123456
        ), patch.object(
            fullscreen_detector, "_GetClassNameW", side_effect=fake_class
        ):
            self.assertFalse(fullscreen_detector.is_fullscreen())

    def test_fullscreen_returns_true(self) -> None:
        def fake_class(hwnd, buf, n) -> int:
            buf.value = "Chrome_WidgetWin_1"
            return 18

        def fake_rect(hwnd, ref) -> int:
            _fill_rect(ref, 0, 0, 1920, 1080)
            return 1

        def fake_info(monitor, ref) -> int:
            _fill_monitor_info(ref, 0, 0, 1920, 1080)
            return 1

        with patch.object(
            fullscreen_detector, "_GetForegroundWindow", return_value=123456
        ), patch.object(
            fullscreen_detector, "_GetClassNameW", side_effect=fake_class
        ), patch.object(
            fullscreen_detector, "_GetWindowRect", side_effect=fake_rect
        ), patch.object(
            fullscreen_detector, "_MonitorFromWindow", return_value=789
        ), patch.object(
            fullscreen_detector, "_GetMonitorInfoW", side_effect=fake_info
        ):
            self.assertTrue(fullscreen_detector.is_fullscreen())

    def test_window_smaller_than_monitor_returns_false(self) -> None:
        def fake_class(hwnd, buf, n) -> int:
            buf.value = "Chrome_WidgetWin_1"
            return 18

        def fake_rect(hwnd, ref) -> int:
            _fill_rect(ref, 100, 100, 800, 600)
            return 1

        def fake_info(monitor, ref) -> int:
            _fill_monitor_info(ref, 0, 0, 1920, 1080)
            return 1

        with patch.object(
            fullscreen_detector, "_GetForegroundWindow", return_value=123456
        ), patch.object(
            fullscreen_detector, "_GetClassNameW", side_effect=fake_class
        ), patch.object(
            fullscreen_detector, "_GetWindowRect", side_effect=fake_rect
        ), patch.object(
            fullscreen_detector, "_MonitorFromWindow", return_value=789
        ), patch.object(
            fullscreen_detector, "_GetMonitorInfoW", side_effect=fake_info
        ):
            self.assertFalse(fullscreen_detector.is_fullscreen())

    def test_get_window_rect_failure_returns_false(self) -> None:
        def fake_class(hwnd, buf, n) -> int:
            buf.value = "Chrome_WidgetWin_1"
            return 18

        with patch.object(
            fullscreen_detector, "_GetForegroundWindow", return_value=123456
        ), patch.object(
            fullscreen_detector, "_GetClassNameW", side_effect=fake_class
        ), patch.object(
            fullscreen_detector, "_GetWindowRect", return_value=0
        ):
            self.assertFalse(fullscreen_detector.is_fullscreen())

    def test_exception_returns_false(self) -> None:
        with patch.object(
            fullscreen_detector, "_GetForegroundWindow", side_effect=OSError("boom")
        ):
            self.assertFalse(fullscreen_detector.is_fullscreen())


# ---------------------------------------------------------------------------
# FullscreenDetector 类委托测试
# ---------------------------------------------------------------------------
class TestFullscreenDetectorClass(unittest.TestCase):
    """FullscreenDetector 薄封装委托。"""

    def test_delegates_to_module_function(self) -> None:
        detector = fullscreen_detector.FullscreenDetector()
        with patch.object(
            fullscreen_detector, "is_fullscreen", return_value=True
        ) as mock_fn:
            self.assertTrue(detector.is_fullscreen())
            mock_fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
