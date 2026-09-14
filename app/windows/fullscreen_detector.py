"""Windows 全屏检测。

本模块提供**两条用途不同的**检测路径：

## 1. 窗口矩形比较 —— :func:`is_fullscreen`

* ``GetForegroundWindow`` → ``GetWindowRect`` 获取前台窗口矩形；
* ``MonitorFromWindow`` + ``GetMonitorInfo`` 获取窗口所在显示器的完整矩形；
* 比较两者：窗口矩形覆盖显示器矩形（允许少量像素误差）即视为全屏。

用于 BreakEngine 的打扰抑制（全屏时延迟弹休息窗）。

## 2. 系统通知状态 —— :func:`is_fullscreen_content`

调用 shell32 的 ``SHQueryUserNotificationState``，直接问系统
「当前有没有全屏应用在运行 / 是否处于演示模式」。

用于 ScreenSessionEngine 判断「人在看全屏内容」还是「人离开了」。
相比矩形比较，它由系统自行维护，能识别**独占全屏**（D3D flip exclusive），
也不受"前台窗口恰好是谁"影响。API 不可用时回退到路径 1。

检测到全屏时（如演示、视频、游戏），上层可据此延迟休息提醒、或
把「长时间无键鼠输入」正确地归为「在看内容」而不是「离开」。

仅在 Windows 上可用；其他平台回退为返回 ``False``（视为未全屏）。
"""

from __future__ import annotations

import sys
from enum import IntEnum
from typing import Optional

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
#: ``SHQueryUserNotificationState`` 的函数指针；非 Windows 或系统过旧时为
#: ``None``（调用方据此回退到窗口矩形比较）
_SHQueryUserNotificationState = None

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
    _SHELL32 = ctypes.windll.shell32

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

    # SHQueryUserNotificationState（shell32）：直接问系统「当前有没有全屏应用
    # 在运行 / 是否处于演示模式」。比 GetWindowRect 比较更权威的地方在于，
    # 它由系统自己维护，且能识别**独占全屏**（D3D flip exclusive）——那种模式
    # 下前台窗口矩形可能并不覆盖整屏，矩形比较会漏判。
    try:
        _SHQueryUserNotificationState = _SHELL32.SHQueryUserNotificationState
        _SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        _SHQueryUserNotificationState.restype = ctypes.c_long
    except AttributeError:  # pragma: no cover - 极旧系统才会缺这个导出
        logger.warning("shell32 未导出 SHQueryUserNotificationState，回退到窗口矩形比较")
        _SHQueryUserNotificationState = None


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


# ---------------------------------------------------------------------------
# 全屏内容消费检测（SHQueryUserNotificationState）
# ---------------------------------------------------------------------------


class NotificationState(IntEnum):
    """``SHQueryUserNotificationState`` 的返回状态。"""

    #: 用户不在（屏保 / 会话断开）—— **不算**在看内容
    NOT_PRESENT = 1
    #: 有全屏应用在运行 / 演示模式 / D3D 全屏
    BUSY = 2
    #: D3D 应用独占全屏（游戏、部分播放器的独占模式）
    RUNNING_D3D_FULL_SCREEN = 3
    #: 演示模式（连接投影仪等）
    PRESENTATION_MODE = 4
    #: 正常桌面，接受通知
    ACCEPTS_NOTIFICATIONS = 5
    #: 首次登录后的安静时段
    QUIET_TIME = 6
    #: Windows 8+ 全屏应用
    APP = 7


#: 视为「全屏内容消费」的状态集合。
#:
#: 刻意**不含** :attr:`NotificationState.NOT_PRESENT`：用户不在屏幕前
#: 与「正在看全屏内容」是相反的两件事，前者仍应判为离开。
_FULLSCREEN_CONTENT_STATES = frozenset(
    {
        NotificationState.BUSY,
        NotificationState.RUNNING_D3D_FULL_SCREEN,
        NotificationState.PRESENTATION_MODE,
        NotificationState.APP,
    }
)


def is_fullscreen_content_state(state: Optional[int]) -> bool:
    """纯函数：给定通知状态码，判断是否属于「全屏内容消费」。

    独立成纯函数便于单元测试，不依赖真实系统状态。

    Args:
        state: ``SHQueryUserNotificationState`` 的原始状态码；``None``
            表示查询失败或不可用。

    Returns:
        属于全屏内容消费时返回 ``True``；``None`` 或未知状态码返回 ``False``。
    """
    if state is None:
        return False
    try:
        return NotificationState(int(state)) in _FULLSCREEN_CONTENT_STATES
    except (ValueError, TypeError):
        return False


def query_notification_state() -> Optional[NotificationState]:
    """查询系统当前的用户通知状态。

    Returns:
        状态枚举值；非 Windows、系统未导出该 API、调用失败或返回未知
        状态码时返回 ``None``。
    """
    if sys.platform != "win32" or _SHQueryUserNotificationState is None:
        return None
    try:
        raw = ctypes.c_int(0)  # type: ignore[name-defined]
        hresult = _SHQueryUserNotificationState(  # type: ignore[name-defined]
            ctypes.byref(raw)  # type: ignore[name-defined]
        )
        if hresult != 0:
            logger.debug(
                "SHQueryUserNotificationState 失败: HRESULT=0x%08X",
                hresult & 0xFFFFFFFF,
            )
            return None
        try:
            return NotificationState(raw.value)
        except ValueError:
            logger.debug("SHQueryUserNotificationState 返回未知状态: %s", raw.value)
            return None
    except Exception:  # noqa: BLE001
        logger.exception("查询通知状态异常，返回 None")
        return None


def is_fullscreen_content() -> bool:
    """当前是否处于「全屏内容消费」状态（看视频 / 全屏演示 / 全屏应用）。

    与 :func:`is_fullscreen` 的分工：

    * :func:`is_fullscreen` 比较前台窗口与显示器矩形，服务于
      **BreakEngine 的打扰抑制**（要不要弹休息窗）。
    * :func:`is_fullscreen_content` 直接问系统 shell「有没有全屏应用在跑」，
      服务于 **ScreenSessionEngine 的暴露判定**（这个人是在看内容，还是走了）。
      它能覆盖矩形比较会漏判的独占全屏，也不受"前台窗口恰好是谁"影响。

    系统 API 不可用时回退为 :func:`is_fullscreen`，保证行为不退化。

    Returns:
        处于全屏内容消费时返回 ``True``。
    """
    state = query_notification_state()
    if state is not None:
        return state in _FULLSCREEN_CONTENT_STATES
    return is_fullscreen()


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

    def is_fullscreen_content(self) -> bool:
        """返回当前是否处于「全屏内容消费」状态（看视频 / 演示 / 全屏应用）。"""
        return is_fullscreen_content()

    def notification_state(self) -> Optional[NotificationState]:
        """返回系统当前的通知状态；不可用时返回 ``None``。"""
        return query_notification_state()
