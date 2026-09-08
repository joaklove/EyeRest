"""统一单调时钟。

所有引擎的时间计算都必须基于单调时钟（``time.monotonic``），而不是墙钟
（``time.time``）——后者会被系统时间调整、NTP 同步、夏令时影响，导致
"休眠 8 小时后醒来发现已经用眼 8 小时"这类荒谬结果。

同时禁止倒计时使用增量累减（``remaining -= 1``）：tick 可能因系统繁忙而
迟到或堆积，累减会持续漂移。正确做法是记录**目标时间**并每次用当前时间
反推剩余量。

用法::

    from app.core.clock import MonotonicClock

    clock = MonotonicClock()
    end_at = clock.deadline(20.0)          # 20 秒后的目标时刻
    left = clock.remaining(end_at)         # 还剩多少秒（不会为负）

测试时可注入假时钟精确控制时间流动::

    class FakeClock:
        def __init__(self): self.t = 0.0
        def now(self): return self.t
        def deadline(self, d): return self.t + d
        def remaining(self, deadline): return max(0.0, deadline - self.t)
        def advance(self, d): self.t += d
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """时钟协议：所有需要计时的组件都依赖此协议，便于测试替换。"""

    def now(self) -> float:
        """返回当前单调时间（秒）。非真实时间戳，仅用于计算间隔。"""
        ...

    def deadline(self, duration_seconds: float) -> float:
        """返回 ``duration_seconds`` 之后的目标时刻。"""
        ...

    def remaining(self, deadline: float) -> float:
        """返回距离 ``deadline`` 还剩多少秒，已到达则返回 0.0。"""
        ...


class MonotonicClock:
    """基于 ``time.monotonic`` 的单调时钟实现。"""

    __slots__ = ()

    def now(self) -> float:
        """返回当前单调时间（秒）。"""
        return time.monotonic()

    def deadline(self, duration_seconds: float) -> float:
        """返回 ``duration_seconds`` 之后的目标时刻。

        Args:
            duration_seconds: 持续时间（秒），负数会被当作 0 处理。

        Returns:
            目标时刻（单调时间轴上的绝对值）。
        """
        return self.now() + max(0.0, float(duration_seconds))

    def remaining(self, deadline: float) -> float:
        """返回距离目标时刻的剩余秒数。

        Args:
            deadline: 由 :meth:`deadline` 生成的目标时刻。

        Returns:
            剩余秒数；若已到达或超过目标时刻则返回 0.0。
        """
        return max(0.0, deadline - self.now())

    def elapsed(self, since: float) -> float:
        """返回从 ``since`` 到现在经过的秒数（不会为负）。"""
        return max(0.0, self.now() - since)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"MonotonicClock(now={self.now():.3f})"


class FakeClock:
    """测试用假时钟：手动推进时间，让时间相关逻辑可确定性测试。"""

    __slots__ = ("t",)

    def __init__(self, start: float = 0.0) -> None:
        self.t = float(start)

    def now(self) -> float:
        """返回当前假时间。"""
        return self.t

    def deadline(self, duration_seconds: float) -> float:
        """返回目标时刻。"""
        return self.t + max(0.0, float(duration_seconds))

    def remaining(self, deadline: float) -> float:
        """返回剩余秒数。"""
        return max(0.0, deadline - self.t)

    def elapsed(self, since: float) -> float:
        """返回经过秒数。"""
        return max(0.0, self.t - since)

    def advance(self, seconds: float) -> None:
        """推进时间 ``seconds`` 秒。"""
        self.t += float(seconds)

    def set(self, value: float) -> None:
        """直接设置当前时间。"""
        self.t = float(value)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"FakeClock(t={self.t:.3f})"


#: 全局默认时钟实例。生产代码使用；测试请注入 FakeClock。
default_clock = MonotonicClock()
