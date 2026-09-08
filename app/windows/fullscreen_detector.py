"""Windows 全屏检测（V1.1）。

通过 Win32 API 检测当前前台窗口是否处于全屏状态：

* ``GetForegroundWindow`` → ``GetWindowRect`` 获取前台窗口矩形；
* ``MonitorFromWindow`` + ``GetMonitorInfo`` 获取窗口所在显示器的完整矩形；
* 比较两者：窗口矩形覆盖显示器矩形（允许少量像素误差）即视为全屏。

检测到全屏时（如演示、视频、游戏），上层可据此延迟休息提醒，降低打扰。

仅在 Windows 上可用；其他平台回退为返回 ``False``（视为未全屏）。
"""

from __future__ import annotations

import sys

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 全屏判定允许的像素误差（处理边框取整 / DPI 缩放的少量偏差）
_TOLERANCE = 2

# 已知桌面/系统窗口类名：这些窗口始终覆盖屏幕，但并非"全屏应用"，
# 检测到它们时返回 False，避免在桌面/任务栏上误判为全屏。
_SHELL_WINDOW_CLASSES = frozenset(
    {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
)


# ---------------------------------------------------------------------------
# Win32 结构体与 API 声明
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        """对应 Win32 ``MONITORINFO`` 结构体。"""

        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    _USER32 = ctypes.windll.user32

    # 显式声明原型，避免 64 位 / 32 位句柄截断等潜在问题
    _GetForegroundWindow = _USER32.GetForegroundWindow
    _GetForegroundWindow.restype = wintypes.HWND

    _GetClassNameW = _USER32.GetClassNameW
    _GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _GetClassNameW.restype = ctypes.c_int

    _GetWindowRect = _USER32.GetWindowRect
    _GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _GetWindowRect.restype = wintypes.BOOL

    _MonitorFromWindow = _USER32.MonitorFromWindow
    _MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _MonitorFromWindow.restype = wintypes.HANDLE

    _GetMonitorInfoW = _USER32.GetMonitorInfoW
    _GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    _GetMonitorInfoW.restype = wintypes.BOOL


def _covers_monitor(
    win_left: int,
    win_top: int,
    win_width: int,
    win_height: int,
    mon_left: int,
    mon_top: int,
    mon_width: int,
    mon_height: int,
    tolerance: int = _TOLERANCE,
) -> bool:
    """判断窗口矩形是否覆盖显示器矩形（纯函数，便于单元测试）。

    Args:
        win_left/top/width/height: 窗口矩形（虚拟屏幕坐标）。
        mon_left/top/width/height: 显示器完整矩形（含任务栏区域）。
        tolerance: 允许的像素误差。

    Returns:
        窗口覆盖显示器时返回 ``True``。
    """
    return (
        win_left <= mon_left + tolerance
        and win_top <= mon_top + tolerance
        and win_width >= mon_width - tolerance
        and win_height >= mon_height - tolerance
    )


def is_fullscreen() -> bool:
    """检测当前前台窗口是否全屏。

    Returns:
        若前台窗口覆盖其所在显示器则返回 ``True``；非 Windows 平台、
        无前台窗口、桌面/系统窗口、窗口尺寸非法或 API 调用失败时返回
        ``False``。
    """
    if sys.platform != "win32":
        logger.debug("非 Windows 平台，is_fullscreen 返回 False")
        return False

    try:
        # 获取前台窗口句柄
        hwnd = _GetForegroundWindow()
        if not hwnd:
            return False

        # 获取窗口类名，排除桌面/系统窗口
        class_name = ctypes.create_unicode_buffer(256)  # type: ignore[name-defined]
        _GetClassNameW(hwnd, class_name, 256)
        if class_name.value in _SHELL_WINDOW_CLASSES:
            return False

        # 获取前台窗口矩形
        win_rect = wintypes.RECT()  # type: ignore[name-defined]
        if not _GetWindowRect(hwnd, ctypes.byref(win_rect)):  # type: ignore[name-defined]
            logger.warning("GetWindowRect 调用失败，返回 False")
            return False
        win_left = int(win_rect.left)
        win_top = int(win_rect.top)
        win_width = int(win_rect.right - win_rect.left)
        win_height = int(win_rect.bottom - win_rect.top)
        if win_width <= 0 or win_height <= 0:
            return False

        # 获取窗口所在的显示器（MONITOR_DEFAULTTONEAREST = 2）
        monitor = _MonitorFromWindow(hwnd, 2)
        if not monitor:
            return False

        info = MONITORINFO()  # type: ignore[name-defined]
        info.cbSize = ctypes.sizeof(MONITORINFO)  # type: ignore[name-defined]
        if not _GetMonitorInfoW(monitor, ctypes.byref(info)):  # type: ignore[name-defined]
            logger.warning("GetMonitorInfoW 调用失败，返回 False")
            return False

        mon_rect = info.rcMonitor  # 完整显示器区域（含任务栏）
        mon_left = int(mon_rect.left)
        mon_top = int(mon_rect.top)
        mon_width = int(mon_rect.right - mon_rect.left)
        mon_height = int(mon_rect.bottom - mon_rect.top)

        return _covers_monitor(
            win_left,
            win_top,
            win_width,
            win_height,
            mon_left,
            mon_top,
            mon_width,
            mon_height,
        )
    except Exception:  # noqa: BLE001
        # 任何异常（DLL 加载失败、参数错误等）都不应崩溃应用
        logger.exception("全屏检测发生异常，返回 False")
        return False


class FullscreenDetector:
    """Windows 全屏检测器的薄封装，便于依赖注入与测试替换。

    每次调用动态查找模块级 :func:`is_fullscreen`，便于在测试中通过
    ``patch.object`` 进行 mock。
    """

    def __init__(self) -> None:
        # 不在这里绑定引用，允许测试时 patch 模块级函数
        pass

    def is_fullscreen(self) -> bool:
        """返回当前前台窗口是否全屏。"""
        return is_fullscreen()
