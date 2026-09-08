"""日志系统（``app.utils.logger``）单元测试。

覆盖三处修复：日志目录跟随便携数据目录、GUI 模式下的空控制台 handler、
以及滚动失败时不丢失日志记录。
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils import logger as logger_mod  # noqa: E402
from app.utils import system  # noqa: E402


def _make_record(msg: str) -> logging.LogRecord:
    """构造一条可直接交给 handler 的日志记录。"""
    return logging.LogRecord("test", logging.INFO, "path", 1, msg, None, None)


class TestLogDir(unittest.TestCase):
    """日志目录必须与应用数据目录保持一致。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        logger_mod._initialized.clear()
        self.addCleanup(logger_mod._initialized.clear)

    def test_follows_env_var(self) -> None:
        """设置 ``EYEREST_DATA_DIR`` 时，日志目录随之迁移。"""
        target = self.tmp_path / "custom"
        with patch.dict(os.environ, {"EYEREST_DATA_DIR": str(target)}):
            self.assertEqual(logger_mod._get_log_dir(), target / "logs")

    def test_follows_frozen_executable(self) -> None:
        """打包模式下日志落在可执行文件同级的 ``data/logs/``。"""
        fake_exe = self.tmp_path / "EyeRest.exe"
        fake_exe.touch()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(system, "is_frozen", return_value=True):
                with patch.object(sys, "executable", str(fake_exe)):
                    result = logger_mod._get_log_dir()
        self.assertEqual(result, self.tmp_path / "data" / "logs")
        self.assertTrue(result.is_dir())

    def test_matches_system_app_data_dir(self) -> None:
        """日志目录始终是应用数据目录下的 ``logs`` 子目录。"""
        target = self.tmp_path / "shared"
        with patch.dict(os.environ, {"EYEREST_DATA_DIR": str(target)}):
            data_dir = system.get_app_data_dir()
            log_dir = logger_mod._get_log_dir()
        self.assertEqual(log_dir, data_dir / "logs")


class TestConsoleHandler(unittest.TestCase):
    """GUI 模式（stdout 为 None）下不得构造持有无效流的 handler。"""

    def test_null_handler_without_stdout(self) -> None:
        with patch.object(sys, "stdout", None):
            handler = logger_mod._build_console_handler()
        self.assertIsInstance(handler, logging.NullHandler)

    def test_stream_handler_with_stdout(self) -> None:
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            handler = logger_mod._build_console_handler()
        self.assertIsInstance(handler, logging.StreamHandler)


class TestRobustRotatingFileHandler(unittest.TestCase):
    """滚动失败时日志仍应写入，不得静默丢弃。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _build(self) -> logger_mod._RobustRotatingFileHandler:
        return logger_mod._RobustRotatingFileHandler(
            filename=str(self.tmp_path / "eyerest.log"),
            when="midnight",
            interval=1,
            backupCount=3,
            encoding="utf-8",
        )

    def test_writes_log_when_rollover_fails(self) -> None:
        """模拟 Windows 上文件被占用导致 ``os.rename`` 失败。"""
        handler = self._build()
        with patch.object(handler, "shouldRollover", return_value=True):
            with patch.object(handler, "doRollover", side_effect=OSError("文件被占用")):
                handler.emit(_make_record("survives-rollover-failure"))
        handler.close()
        content = (self.tmp_path / "eyerest.log").read_text(encoding="utf-8")
        self.assertIn("survives-rollover-failure", content)

    def test_normal_write(self) -> None:
        """正常情况下日志照常写入。"""
        handler = self._build()
        handler.emit(_make_record("normal-message"))
        handler.close()
        content = (self.tmp_path / "eyerest.log").read_text(encoding="utf-8")
        self.assertIn("normal-message", content)

    def test_rollover_still_attempted(self) -> None:
        """未失败时仍应正常执行滚动。"""
        handler = self._build()
        with patch.object(handler, "shouldRollover", return_value=True):
            with patch.object(handler, "doRollover") as mock_roll:
                handler.emit(_make_record("with-rollover"))
        mock_roll.assert_called_once()
        handler.close()


if __name__ == "__main__":
    unittest.main()
