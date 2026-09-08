"""统计服务（V0.5 减法重构：JSON 统计替代 SQLite 聚合）。

对外接口保持不变（``get_daily_stats`` / ``get_weekly_stats`` /
``get_monthly_stats`` / ``get_break_trend`` / ``get_usage_trend`` /
``aggregate_daily_stat``），内部改为读取 :class:`StatsStore` 维护的
每日计数，因此统计页与 Dashboard 无需改动即可继续工作。

字段映射（与旧 DailyStat 对齐，未采集的字段为 0）::

    total_active_seconds  ← 屏幕暴露时长 screen_seconds
    total_breaks          ← 远眺 + 活动 + 深度休息 次数
    total_short_breaks    ← 远眺次数
    total_long_breaks     ← 深度休息次数
    total_skipped_breaks  ← 深度休息跳过次数
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.services.stats_store import StatsStore, get_stats_store
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _today_local() -> date:
    """返回本地今日日期。"""
    return date.today()


def _day_str(day: date | str | None) -> str:
    """把入参统一成 ``YYYY-MM-DD`` 字符串。"""
    if day is None:
        return _today_local().isoformat()
    if isinstance(day, str):
        return day
    return day.isoformat()


def _format_mmdd(day_str: str) -> str:
    """将 ``YYYY-MM-DD`` 格式化为 ``MM-DD``。"""
    try:
        return datetime.strptime(day_str, "%Y-%m-%d").strftime("%m-%d")
    except (ValueError, TypeError):
        return day_str


class StatisticsService:
    """统计服务（JSON 版）。

    Args:
        store: 统计存储实例；未提供时使用全局单例。
    """

    def __init__(self, store: Optional[StatsStore] = None, db: Any = None) -> None:
        """初始化统计服务。

        Args:
            store: StatsStore 实例。
            db: **已废弃**，仅为兼容旧调用保留。
        """
        self._store = store if store is not None else get_stats_store()

    # ------------------------------------------------------------------
    # 单日 / 区间
    # ------------------------------------------------------------------
    def get_daily_stats(self, date_str: Optional[str] = None) -> dict[str, Any]:
        """获取指定日期（默认今天）的统计字典。"""
        return self._to_daily_dict(self._store.get_day(_day_str(date_str)))

    def get_weekly_stats(self) -> list[dict[str, Any]]:
        """获取最近 7 天统计（按日期升序）。"""
        return self._range_stats(7)

    def get_monthly_stats(self) -> list[dict[str, Any]]:
        """获取最近 30 天统计（按日期升序）。"""
        return self._range_stats(30)

    def _range_stats(self, days: int) -> list[dict[str, Any]]:
        """获取最近 ``days`` 天统计列表。"""
        return [self._to_daily_dict(day) for day in self._store.history(days)]

    # ------------------------------------------------------------------
    # 趋势数据（供图表使用）
    # ------------------------------------------------------------------
    def get_usage_trend(self, days: int = 7) -> dict[str, list[Any]]:
        """获取使用时长趋势。

        Returns:
            ``{"dates": [...], "active_seconds": [...], "idle_seconds": [...]}``
        """
        raw = self._store.history(days)
        return {
            "dates": [_format_mmdd(day["date"]) for day in raw],
            "active_seconds": [float(day.get("screen_seconds", 0.0)) for day in raw],
            # V0.5 不再统计键鼠空闲：键鼠空闲 ≠ 未用眼，保留字段以兼容图表
            "idle_seconds": [0.0 for _ in raw],
        }

    def get_break_trend(self, days: int = 7) -> dict[str, list[Any]]:
        """获取休息次数趋势。

        Returns:
            ``{"dates": [...], "short_breaks": [...], "long_breaks": [...],
              "skipped": [...]}``
        """
        raw = self._store.history(days)
        return {
            "dates": [_format_mmdd(day["date"]) for day in raw],
            "short_breaks": [int(day.get("look_away", 0)) for day in raw],
            "long_breaks": [int(day.get("deep_break", 0)) for day in raw],
            "skipped": [int(day.get("skipped_break", 0)) for day in raw],
        }

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------
    def aggregate_daily_stat(self, date_str: Optional[str] = None) -> dict[str, Any]:
        """聚合指定日期（默认今天）的统计。

        JSON 版数据在触发时即已累计，这里只需读取并返回，保持接口兼容。
        """
        return self.get_daily_stats(date_str)

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------
    @staticmethod
    def _to_daily_dict(day: dict[str, Any]) -> dict[str, Any]:
        """把 StatsStore 的一天记录映射为旧 DailyStat 字典结构。"""
        look_away = int(day.get("look_away", 0))
        move = int(day.get("move", 0))
        deep = int(day.get("deep_break", 0))
        return {
            "date": day.get("date", ""),
            "total_active_seconds": float(day.get("screen_seconds", 0.0)),
            "total_idle_seconds": 0.0,
            "total_breaks": look_away + move + deep,
            "total_short_breaks": look_away,
            "total_long_breaks": deep,
            "total_skipped_breaks": int(day.get("skipped_break", 0)),
            "total_paused_seconds": 0.0,
            "avg_work_duration": 0.0,
            "avg_break_duration": 0.0,
            # V0.5 新增：四层节奏原始计数
            "blink_cues": int(day.get("blink_cue", 0)),
            "move_count": move,
        }

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def get_recent_dates(self, days: int = 7) -> list[str]:
        """返回最近 ``days`` 天的日期字符串（升序）。"""
        today = _today_local()
        return [
            (today - timedelta(days=offset)).isoformat()
            for offset in range(days - 1, -1, -1)
        ]
