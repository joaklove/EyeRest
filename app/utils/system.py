"""系统工具函数。

提供跨平台/Windows 特定的系统辅助能力：应用数据目录、日志目录、
可执行路径、操作系统检测、前台窗口信息等。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.config import defaults


def is_windows() -> bool:
    """当前是否运行在 Windows 上。"""
    return sys.platform == "win32"


def is_frozen() -> bool:
    """当前是否为打包后的可执行文件（PyInstaller / Nuitka 等）。"""
    return getattr(sys, "frozen", False)


def get_app_data_dir() -> Path:
    """返回应用数据目录（数据库、日志的存放位置），不存在则创建。

    位置按以下优先级确定（由高到低）：

    1. 环境变量 ``EYEREST_DATA_DIR`` —— 显式指定，方便便携化或自定义位置。
    2. 打包模式（frozen）：可执行文件同级目录下的 ``data/``。
       即便携模式，让数据库与日志留在程序所在盘，不写入系统盘。
    3. Windows 开发模式：``%APPDATA%/EyeRest/``。
    4. 其他平台：``~/.eyerest/``。
    """
    env_dir = os.environ.get(defaults.DATA_DIR_ENV)
    if env_dir:
        data_dir = Path(env_dir)
    elif is_frozen():
        data_dir = Path(sys.executable).resolve().parent / defaults.PORTABLE_DATA_DIRNAME
    else:
        appdata = os.environ.get("APPDATA")
        if appdata and is_windows():
            data_dir = Path(appdata) / defaults.APP_NAME
        else:
            data_dir = Path.home() / f".{defaults.APP_NAME.lower()}"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_log_dir() -> Path:
    """返回日志目录（``<app_data_dir>/logs/``），不存在则创建。"""
    log_dir = get_app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_db_path() -> Path:
    """返回 SQLite 数据库文件路径。"""
    return get_app_data_dir() / defaults.DB_FILENAME


def get_app_path() -> Path:
    """返回应用根目录。

    * 打包模式（frozen）：返回可执行文件所在目录。
    * 开发模式：返回项目根目录（本文件上溯三级）。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # app/utils/system.py -> app/utils -> app -> project root
    return Path(__file__).resolve().parent.parent.parent


def get_resource_path(relative: str) -> Path:
    """返回资源文件路径。

    打包模式下从 PyInstaller 的 ``_MEIPASS`` 临时目录读取；开发模式下
    从项目根目录读取。
    """
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = get_app_path()
    return base / relative


def ensure_dir(path: Path | str) -> Path:
    """确保目录存在，返回 Path 对象。"""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_foreground_window_title() -> str:
    """获取当前前台窗口标题（Windows 专用）。

    非 Windows 或调用失败时返回空字符串。
    """
    if not is_windows():
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:  # noqa: BLE001
        return ""


def get_screen_size() -> tuple[int, int]:
    """获取主屏幕分辨率 (width, height)。

    Windows 下优先调用 Win32 API；失败时回退到 (0, 0)。
    """
    if not is_windows():
        return (0, 0)
    try:
        import ctypes

        user32 = ctypes.windll.user32
        width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        return (int(width), int(height))
    except Exception:  # noqa: BLE001
        return (0, 0)


def is_admin() -> bool:
    """当前进程是否具有管理员/root 权限。"""
    if is_windows():
        try:
            import ctypes

            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:  # noqa: BLE001
            return False
    return os.geteuid() == 0  # type: ignore[attr-defined]
