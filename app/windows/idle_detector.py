"""Windows 空闲检测。

通过 Win32 API ``GetLastInputInfo`` + ``GetTickCount`` 获取自最后一次键鼠
输入以来的空闲时长，供 :class:`app.core.activity_monitor.ActivityMonitor` 使用。

仅在 Windows 上可用；其他平台会回退为返回 0 秒（视为活跃）。
"""

from __future__ import annotations

import sys

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Win32 结构体定义
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        """对应 Win32 ``LASTINPUTINFO`` 结构体。"""

        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("dwTime", wintypes.DWORD),
        ]

    _USER32 = ctypes.windll.user32
    _KERNEL32 = ctypes.windll.kernel32

    # 显式声明原型，避免 64 位/32 位句柄截断等潜在问题
    _GetLastInputInfo = _USER32.GetLastInputInfo
    _GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    _GetLastInputInfo.restype = wintypes.BOOL

    _GetTickCount = _KERNEL32.GetTickCount
    _GetTickCount.argtypes = []
    _GetTickCount.restype = wintypes.DWORD
else:  # pragma: no cover - 非 Windows 平台
    ctypes = None  # type: ignore[assignment]

    class LASTINPUTINFO:  # type: ignore[no-redef]
        """非 Windows 平台的占位结构体。"""

        def __init__(self) -> None:
            self.cbSize = 0
            self.dwTime = 0


def get_idle_seconds() -> float:
    """获取自最后一次键鼠输入以来的空闲秒数。

    Returns:
        空闲秒数（非负）。若 API 调用失败或不在 Windows 平台，返回 0.0
        （假定为活跃状态），并记录日志。
    """
    if sys.platform != "win32":
        logger.debug("非 Windows 平台，get_idle_seconds 返回 0.0")
        return 0.0

    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)  # type: ignore[arg-type]
        if not _GetLastInputInfo(ctypes.byref(info)):
            logger.warning("GetLastInputInfo 调用失败，返回 0.0 秒")
            return 0.0
        tick = _GetTickCount()
        # GetTickCount 是 DWORD（32 位，约 49.7 天回绕）。正常使用场景下
        # tick >= dwTime，但若系统刚回绕可能出现 tick < dwTime，此时视为 0。
        elapsed_ms = tick - info.dwTime
        if elapsed_ms < 0:
            elapsed_ms = 0
        return elapsed_ms / 1000.0
    except Exception:  # noqa: BLE001
        # 任何异常（DLL 加载失败、参数错误等）都不应崩溃应用
        logger.exception("获取空闲秒数时发生异常，返回 0.0 秒")
        return 0.0


class IdleDetector:
    """Windows 空闲检测器的薄封装，便于依赖注入与测试替换。

    每次调用时动态查找模块级 :func:`get_idle_seconds`，便于在测试中
    通过 ``patch.object`` 进行 mock。
    """

    def __init__(self) -> None:
        # 不在这里绑定引用，允许测试时 patch 模块级函数
        pass

    def get_idle_seconds(self) -> float:
        """返回当前空闲秒数。"""
        return get_idle_seconds()
