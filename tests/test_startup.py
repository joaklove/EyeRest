"""StartupManager 单元测试。

覆盖开机自启的启用 / 禁用 / 切换 / 查询，以及非 Windows 平台降级。

在 Windows 上使用真实 HKCU 注册表（``Software\\Microsoft\\Windows\\CurrentVersion\\Run``），
测试前后保存 / 恢复原始值，确保不残留测试数据。
在非 Windows 平台上验证优雅降级。
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from app.windows import startup as startup_module
from app.windows.startup import StartupManager


class TestStartupManagerWindows(unittest.TestCase):
    """StartupManager 在 Windows 平台的测试（真实注册表操作）。"""

    def setUp(self) -> None:
        # 非 Windows 平台跳过真实注册表测试
        if not startup_module._HAS_WINREG:
            self.skipTest("非 Windows 平台，跳过注册表测试")

        import winreg

        self._winreg = winreg
        # 保存注册表中可能已存在的 EyeRest 值
        self._saved_value: str | None = None
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                StartupManager.REGISTRY_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                value, _regtype = winreg.QueryValueEx(key, StartupManager.APP_NAME)
                self._saved_value = value
        except FileNotFoundError:
            pass

    def tearDown(self) -> None:
        if not startup_module._HAS_WINREG:
            return
        # 恢复原始状态
        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER,
                StartupManager.REGISTRY_KEY,
                0,
                self._winreg.KEY_SET_VALUE,
            ) as key:
                if self._saved_value is not None:
                    self._winreg.SetValueEx(
                        key,
                        StartupManager.APP_NAME,
                        0,
                        self._winreg.REG_SZ,
                        self._saved_value,
                    )
                else:
                    self._winreg.DeleteValue(key, StartupManager.APP_NAME)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # is_enabled
    # ------------------------------------------------------------------
    def test_is_enabled_false_when_not_set(self) -> None:
        """未设置时应返回 False。"""
        # 先确保清除
        StartupManager.disable()
        self.assertFalse(StartupManager.is_enabled())

    def test_is_enabled_true_after_enable(self) -> None:
        """启用后应返回 True。"""
        StartupManager.disable()
        StartupManager.enable()
        self.assertTrue(StartupManager.is_enabled())

    # ------------------------------------------------------------------
    # enable
    # ------------------------------------------------------------------
    def test_enable_writes_registry(self) -> None:
        """enable 应向注册表写入 EyeRest 值。"""
        StartupManager.disable()
        result = StartupManager.enable()
        self.assertTrue(result)

        # 验证注册表中确实写入了值
        with self._winreg.OpenKey(
            self._winreg.HKEY_CURRENT_USER,
            StartupManager.REGISTRY_KEY,
            0,
            self._winreg.KEY_READ,
        ) as key:
            value, regtype = self._winreg.QueryValueEx(key, StartupManager.APP_NAME)
        self.assertEqual(regtype, self._winreg.REG_SZ)
        self.assertIsInstance(value, str)
        self.assertTrue(len(value) > 0)

    def test_enable_idempotent(self) -> None:
        """多次 enable 不应报错。"""
        StartupManager.enable()
        result = StartupManager.enable()
        self.assertTrue(result)

    # ------------------------------------------------------------------
    # disable
    # ------------------------------------------------------------------
    def test_disable_removes_registry(self) -> None:
        """disable 应移除注册表中的 EyeRest 值。"""
        StartupManager.enable()
        self.assertTrue(StartupManager.is_enabled())

        result = StartupManager.disable()
        self.assertTrue(result)
        self.assertFalse(StartupManager.is_enabled())

    def test_disable_when_not_set_returns_true(self) -> None:
        """未设置时 disable 应返回 True（幂等）。"""
        StartupManager.disable()
        result = StartupManager.disable()
        self.assertTrue(result)

    # ------------------------------------------------------------------
    # toggle
    # ------------------------------------------------------------------
    def test_toggle_enable(self) -> None:
        """toggle(True) 应启用。"""
        StartupManager.disable()
        result = StartupManager.toggle(True)
        self.assertTrue(result)
        self.assertTrue(StartupManager.is_enabled())

    def test_toggle_disable(self) -> None:
        """toggle(False) 应禁用。"""
        StartupManager.enable()
        result = StartupManager.toggle(False)
        self.assertTrue(result)
        self.assertFalse(StartupManager.is_enabled())

    def test_toggle_roundtrip(self) -> None:
        """toggle 往返切换。"""
        StartupManager.toggle(True)
        self.assertTrue(StartupManager.is_enabled())
        StartupManager.toggle(False)
        self.assertFalse(StartupManager.is_enabled())
        StartupManager.toggle(True)
        self.assertTrue(StartupManager.is_enabled())

    # ------------------------------------------------------------------
    # 命令构建
    # ------------------------------------------------------------------
    def test_get_command_returns_nonempty_string(self) -> None:
        """_get_command 应返回非空字符串。"""
        command = StartupManager._get_command()
        self.assertIsInstance(command, str)
        self.assertTrue(len(command) > 0)

    def test_get_command_dev_mode_contains_script(self) -> None:
        """开发模式下命令应包含 main.py 路径。"""
        if getattr(sys, "frozen", False):
            self.skipTest("PyInstaller 打包模式，跳过开发模式测试")
        command = StartupManager._get_command()
        self.assertIn("main.py", command)


class TestStartupManagerNonWindows(unittest.TestCase):
    """StartupManager 非 Windows 平台降级测试。"""

    def setUp(self) -> None:
        # 模拟 winreg 不可用
        self._original_has_winreg = startup_module._HAS_WINREG
        startup_module._HAS_WINREG = False

    def tearDown(self) -> None:
        startup_module._HAS_WINREG = self._original_has_winreg

    def test_is_enabled_returns_false(self) -> None:
        """非 Windows 平台 is_enabled 返回 False。"""
        self.assertFalse(StartupManager.is_enabled())

    def test_enable_returns_false(self) -> None:
        """非 Windows 平台 enable 返回 False。"""
        self.assertFalse(StartupManager.enable())

    def test_disable_returns_false(self) -> None:
        """非 Windows 平台 disable 返回 False。"""
        self.assertFalse(StartupManager.disable())

    def test_toggle_returns_false(self) -> None:
        """非 Windows 平台 toggle 返回 False。"""
        self.assertFalse(StartupManager.toggle(True))
        self.assertFalse(StartupManager.toggle(False))


class TestStartupManagerConstants(unittest.TestCase):
    """StartupManager 常量与结构测试。"""

    def test_registry_key_constant(self) -> None:
        """REGISTRY_KEY 应为标准 Run 路径。"""
        self.assertEqual(
            StartupManager.REGISTRY_KEY,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        )

    def test_app_name_constant(self) -> None:
        """APP_NAME 应为 EyeRest。"""
        self.assertEqual(StartupManager.APP_NAME, "EyeRest")

    def test_level_constants(self) -> None:
        """验证类属性为 classmethod（可通过类调用）。"""
        # 确保 is_enabled / enable / disable / toggle 是可调用的类方法
        self.assertTrue(callable(StartupManager.is_enabled))
        self.assertTrue(callable(StartupManager.enable))
        self.assertTrue(callable(StartupManager.disable))
        self.assertTrue(callable(StartupManager.toggle))


if __name__ == "__main__":
    unittest.main()
