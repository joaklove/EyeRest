"""时间工具（占位，后续实现）。

预期职责：常用时间格式化、时长计算、时间窗口判断等辅助函数。
"""

from __future__ import annotations

from datetime import datetime


def format_duration(seconds: float) -> str:
    """将秒数格式化为 ``mm:ss`` 或 ``hh:mm:ss`` 字符串（待实现）。"""
    # TODO: 实现
    raise NotImplementedError


def now() -> datetime:
    """返回当前本地时间（可测试的封装点）。"""
    return datetime.now()
