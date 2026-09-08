"""应用数据目录（``app.utils.system.get_app_data_dir``）单元测试。

覆盖便携化行为：环境变量优先、打包模式落在可执行文件同级，
以及开发模式沿用 ``%APPDATA%`` 的既有行为。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import defaults  # noqa: E402
from app.utils import system  # noqa: E402


class TestGetAppDataDir(unittest.TestCase):
    """应用数据目录的位置优先级与自动创建行为。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # 确保测试期间环境变量不泄漏
        self.addCleanup(patch.dict(os.environ, {}, clear=False).stop)

    # ------------------------------------------------------------------
    # 环境变量优先
    # ------------------------------------------------------------------
    def test_env_var_has_highest_priority(self) -> None:
        """设置了 ``EYEREST_DATA_DIR`` 时，无条件使用该路径。"""
        target = self.tmp_path / "custom-data"
        with patch.dict(os.environ, {defaults.DATA_DIR_ENV: str(target)}):
            with patch.object(system, "is_frozen", return_value=True):
                result = system.get_app_data_dir()
        self.assertEqual(result, target)
        self.assertTrue(result.is_dir())

    def test_env_var_wins_over_appdata(self) -> None:
        """开发模式下，环境变量也应覆盖 ``%APPDATA%``。"""
        target = self.tmp_path / "portable"
        with patch.dict(os.environ, {defaults.DATA_DIR_ENV: str(target)}):
            with patch.object(system, "is_frozen", return_value=False):
                result = system.get_app_data_dir()
        self.assertEqual(result, target)

    def test_env_var_directory_is_created(self) -> None:
        """指定的目录不存在时应自动创建。"""
        target = self.tmp_path / "nested" / "deep" / "data"
        self.assertFalse(target.exists())
        with patch.dict(os.environ, {defaults.DATA_DIR_ENV: str(target)}):
            result = system.get_app_data_dir()
        self.assertTrue(result.is_dir())

    # ------------------------------------------------------------------
    # 打包模式（便携）
    # ------------------------------------------------------------------
    def test_frozen_uses_dir_next_to_executable(self) -> None:
        """打包模式下数据目录位于可执行文件同级的 ``data/``。"""
        fake_exe = self.tmp_path / "dist" / "EyeRest.exe"
        fake_exe.parent.mkdir(parents=True, exist_ok=True)
        fake_exe.touch()

        env = os.environ.copy()
        env.pop(defaults.DATA_DIR_ENV, None)
        with patch.dict(os.environ, env, clear=True):
            with patch.object(system, "is_frozen", return_value=True):
                with patch.object(sys, "executable", str(fake_exe)):
                    result = system.get_app_data_dir()

        expected = self.tmp_path / "dist" / defaults.PORTABLE_DATA_DIRNAME
        self.assertEqual(result, expected)
        self.assertTrue(result.is_dir())

    def test_frozen_does_not_touch_appdata(self) -> None:
        """打包模式不得回落到 ``%APPDATA%``。"""
        fake_exe = self.tmp_path / "EyeRest.exe"
        fake_exe.touch()

        env = os.environ.copy()
        env.pop(defaults.DATA_DIR_ENV, None)
        env["APPDATA"] = str(self.tmp_path / "Roaming")
        with patch.dict(os.environ, env, clear=True):
            with patch.object(system, "is_frozen", return_value=True):
                with patch.object(sys, "executable", str(fake_exe)):
                    result = system.get_app_data_dir()

        self.assertNotIn("Roaming", str(result))
        self.assertEqual(result, self.tmp_path / defaults.PORTABLE_DATA_DIRNAME)

    # ------------------------------------------------------------------
    # 开发模式（沿用既有行为）
    # ------------------------------------------------------------------
    def test_dev_mode_uses_appdata_on_windows(self) -> None:
        """开发模式下 Windows 仍使用 ``%APPDATA%/EyeRest``。"""
        env = os.environ.copy()
        env.pop(defaults.DATA_DIR_ENV, None)
        env["APPDATA"] = str(self.tmp_path / "Roaming")
        with patch.dict(os.environ, env, clear=True):
            with patch.object(system, "is_frozen", return_value=False):
                with patch.object(system, "is_windows", return_value=True):
                    result = system.get_app_data_dir()

        self.assertEqual(result, self.tmp_path / "Roaming" / defaults.APP_NAME)
        self.assertTrue(result.is_dir())

    def test_dev_mode_falls_back_to_home(self) -> None:
        """非 Windows 且无 APPDATA 时回落到家目录下的隐藏目录。"""
        env = os.environ.copy()
        env.pop(defaults.DATA_DIR_ENV, None)
        env.pop("APPDATA", None)
        with patch.dict(os.environ, env, clear=True):
            with patch.object(system, "is_frozen", return_value=False):
                with patch.object(system, "is_windows", return_value=False):
                    with patch.object(
                        Path, "home", return_value=self.tmp_path / "home"
                    ):
                        result = system.get_app_data_dir()

        self.assertEqual(result.name, f".{defaults.APP_NAME.lower()}")
        self.assertTrue(result.is_dir())

    # ------------------------------------------------------------------
    # 派生路径
    # ------------------------------------------------------------------
    def test_db_path_follows_data_dir(self) -> None:
        """数据库路径应随数据目录一起迁移。"""
        target = self.tmp_path / "custom-data"
        with patch.dict(os.environ, {defaults.DATA_DIR_ENV: str(target)}):
            db_path = system.get_db_path()
        self.assertEqual(db_path, target / defaults.DB_FILENAME)

    def test_log_dir_follows_data_dir(self) -> None:
        """日志目录应随数据目录一起迁移。"""
        target = self.tmp_path / "custom-data"
        with patch.dict(os.environ, {defaults.DATA_DIR_ENV: str(target)}):
            log_dir = system.get_log_dir()
        self.assertEqual(log_dir, target / "logs")
        self.assertTrue(log_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
