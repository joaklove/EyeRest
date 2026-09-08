"""开机自启动管理（通过 HKCU 注册表）。

使用 Windows 注册表 ``HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
实现应用的开机自启开关。无需管理员权限（HKCU）。

PyInstaller 打包的 exe 直接使用 ``sys.executable``；
开发模式下使用 ``pythonw.exe`` + 脚本路径，避免弹出控制台窗口。

非 Windows 平台下所有方法返回 ``False``，不会抛出异常。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)

# winreg 仅在 Windows 可用
try:
    import winreg

    _HAS_WINREG = True
except ImportError:
    winreg = None  # type: ignore[assignment]
    _HAS_WINREG = False


class StartupManager:
    """开机自启动管理（通过 HKCU 注册表）。

    通过读写 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
    注册表键实现应用开机自启动。所有方法均为类方法，无需实例化。

    注册表值名：``EyeRest``
    注册表值数据：
        - PyInstaller 打包模式：``"C:\\path\\to\\EyeRest.exe"``
        - 开发模式：``"C:\\path\\to\\pythonw.exe" "G:\\path\\to\\app\\main.py"``
    """

    REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_NAME = "EyeRest"

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @classmethod
    def _get_command(cls) -> str:
        """构建注册表值数据（启动命令行）。

        - PyInstaller 打包模式：直接返回带引号的 ``sys.executable``
        - 开发模式：返回 ``"pythonw.exe" "main.py"`` 格式
        """
        if getattr(sys, "frozen", False):
            # PyInstaller 打包的 exe
            return f'"{sys.executable}"'

        # 开发模式：pythonw.exe + 脚本路径
        python_exe = sys.executable

        # 尝试将 python.exe 替换为 pythonw.exe（避免控制台窗口）
        if python_exe.endswith("python.exe"):
            pythonw_exe = python_exe[: -len("python.exe")] + "pythonw.exe"
        elif python_exe.endswith("pythonw.exe"):
            pythonw_exe = python_exe
        else:
            # 回退：在同目录查找 pythonw.exe
            pythonw_exe = str(Path(python_exe).with_name("pythonw.exe"))

        # 脚本路径：app/main.py（相对于本文件所在目录的上两级）
        script_path = str(Path(__file__).resolve().parent.parent / "main.py")
        return f'"{pythonw_exe}" "{script_path}"'

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------
    @classmethod
    def is_enabled(cls) -> bool:
        """检查是否已启用开机自启。

        Returns:
            已启用返回 ``True``，未启用或非 Windows 平台返回 ``False``。
        """
        if not _HAS_WINREG:
            return False
        try:
            with winreg.OpenKey(  # type: ignore[union-attr]
                winreg.HKEY_CURRENT_USER,  # type: ignore[union-attr]
                cls.REGISTRY_KEY,
                0,
                winreg.KEY_READ,  # type: ignore[union-attr]
            ) as key:
                winreg.QueryValueEx(key, cls.APP_NAME)  # type: ignore[union-attr]
                return True
        except FileNotFoundError:
            return False
        except OSError:
            logger.exception("读取注册表失败")
            return False

    @classmethod
    def enable(cls) -> bool:
        """启用开机自启（写入注册表）。

        Returns:
            成功返回 ``True``，失败或非 Windows 平台返回 ``False``。
        """
        if not _HAS_WINREG:
            logger.warning("非 Windows 平台，不支持开机自启")
            return False
        try:
            command = cls._get_command()
            with winreg.OpenKey(  # type: ignore[union-attr]
                winreg.HKEY_CURRENT_USER,  # type: ignore[union-attr]
                cls.REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,  # type: ignore[union-attr]
            ) as key:
                winreg.SetValueEx(  # type: ignore[union-attr]
                    key, cls.APP_NAME, 0, winreg.REG_SZ, command  # type: ignore[union-attr]
                )
            logger.info("开机自启已启用: %s", command)
            return True
        except OSError:
            logger.exception("写入注册表失败")
            return False

    @classmethod
    def disable(cls) -> bool:
        """禁用开机自启（移除注册表项）。

        值不存在时视为已禁用，返回 ``True``。

        Returns:
            成功返回 ``True``，失败或非 Windows 平台返回 ``False``。
        """
        if not _HAS_WINREG:
            logger.warning("非 Windows 平台，不支持开机自启")
            return False
        try:
            with winreg.OpenKey(  # type: ignore[union-attr]
                winreg.HKEY_CURRENT_USER,  # type: ignore[union-attr]
                cls.REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,  # type: ignore[union-attr]
            ) as key:
                winreg.DeleteValue(key, cls.APP_NAME)  # type: ignore[union-attr]
            logger.info("开机自启已禁用")
            return True
        except FileNotFoundError:
            # 值不存在，视为已禁用
            return True
        except OSError:
            logger.exception("删除注册表值失败")
            return False

    @classmethod
    def toggle(cls, enabled: bool) -> bool:
        """切换开机自启状态。

        Args:
            enabled: ``True`` 启用，``False`` 禁用。

        Returns:
            操作是否成功。
        """
        if enabled:
            return cls.enable()
        return cls.disable()
