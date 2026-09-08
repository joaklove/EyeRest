"""日志系统：基于 Python logging 模块，按日滚动文件 + 控制台输出。

日志目录由 ``app.utils.system.get_log_dir()`` 统一决定，与数据库保持在
同一数据目录内：

* 环境变量 ``EYEREST_DATA_DIR``：显式指定时优先
* 打包模式（frozen）：可执行文件同级 ``data/logs/``（便携，不写系统盘）
* 开发模式：``%AppData%/EyeRest/logs/``

文件名格式：``eyerest.log``，滚动后为 ``eyerest.log.<YYYY-MM-DD>.log``
"""

from __future__ import annotations

import logging
import sys
from logging import FileHandler
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.utils import system

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 缓存已初始化的 logger，避免重复添加 handler
_initialized: set[str] = set()


class _RobustRotatingFileHandler(TimedRotatingFileHandler):
    """滚动失败时仍能继续写日志的文件 handler。

    ``TimedRotatingFileHandler.emit()`` 在滚动失败时会吞掉整条日志记录
    （Windows 上日志文件被其他进程占用时 ``os.rename`` 会失败，很常见），
    且 GUI 模式下 ``sys.stderr`` 为 None，连 "Logging error" 都无法输出，
    表现为"日志莫名消失"。这里把滚动与写入解耦：滚动失败仅跳过本次滚动，
    日志照常写入当前文件。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.shouldRollover(record):
                self.doRollover()
        except Exception:  # noqa: BLE001 - 滚动失败不应阻断日志写入
            pass
        FileHandler.emit(self, record)


def _get_log_dir() -> Path:
    """返回日志目录。

    统一复用 ``app.utils.system.get_log_dir()``，确保日志与数据库始终位于
    同一数据目录（便携模式下两者都在程序所在盘）。
    """
    return system.get_log_dir()


def _build_file_handler() -> TimedRotatingFileHandler:
    """构建按日滚动的文件 handler。"""
    log_dir = _get_log_dir()
    handler = _RobustRotatingFileHandler(
        filename=str(log_dir / "eyerest.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    return handler


def _build_console_handler() -> logging.Handler:
    """构建控制台 handler。

    GUI 模式（PyInstaller ``console=False``）下 ``sys.stdout`` 为 None，
    此时返回空 handler，避免 StreamHandler 持有无效流导致写入异常。
    """
    if sys.stdout is None:
        return logging.NullHandler()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, _DATE_FORMAT))
    return handler


def get_logger(name: str = "eyerest") -> logging.Logger:
    """获取（并按需初始化）一个命名 logger。

    首次调用某个 name 时会自动挂载文件与控制台 handler。
    重复调用不会重复挂载，避免日志重复输出。

    Args:
        name: logger 名称，建议使用模块名如 ``app.core.timer_engine``。

    Returns:
        配置好的 ``logging.Logger`` 实例。
    """
    logger = logging.getLogger(name)
    if name in _initialized:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # 避免 root logger 重复输出

    # 清理可能已存在的 handler（防御性）
    logger.handlers.clear()
    logger.addHandler(_build_file_handler())
    logger.addHandler(_build_console_handler())

    _initialized.add(name)
    return logger


def shutdown_logging() -> None:
    """优雅关闭日志系统，刷新并关闭所有 handler。"""
    for name in list(_initialized):
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass
        logger.handlers.clear()
    _initialized.clear()
