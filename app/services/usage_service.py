"""使用统计服务（V0.5 减法重构：不再依赖数据库）。

负责维护「当前使用会话」的概念（开始 / 结束），把屏幕暴露时长累加到
:class:`StatsStore`，并聚合今日摘要供 Dashboard 展示。

用法::

    from app.services.usage_service import UsageService

    service = UsageService()
    service.start_session()
    # ... 运行期间 ...
    summary = service.get_today_summary()
    service.end_session()
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.stats_store import StatsStore, get_stats_store
from app.utils.logger import get_logger

logger = get_logger(__name__)


class UsageService:
    """使用统计服务（JSON 版）。

    设计要点：

    * 会话开始时记录基线，结束（或退出）时把累计暴露时长写入统计存储
    * :meth:`get_today_summary` 读取 JSON 统计 + 当前会话实时增量
    * 不再有 usage_sessions / break_events 表，统计口径以 StatsStore 为准
    """

    def __init__(
        self,
        db: Any = None,
        timer_engine: Any = None,
        break_engine: Any = None,
        store: Optional[StatsStore] = None,
    ) -> None:
        """初始化使用统计服务。

        Args:
            db: **已废弃**，仅为兼容旧调用保留。
            timer_engine: 可选的计时引擎，用于读取累计时长。
            break_engine: 可选的休息引擎，用于读取今日休息计数。
            store: 统计存储实例；未提供时使用全局单例。
        """
        self._store = store if store is not None else get_stats_store()
        self._timer_engine = timer_engine
        self._break_engine = break_engine

        # 当前会话状态
        self._current_session_id: Optional[int] = None
        self._baseline_active: float = 0.0
        self._session_active: bool = False

    # ------------------------------------------------------------------
    # 引擎注入
    # ------------------------------------------------------------------
    def set_engines(self, timer_engine: Any = None, break_engine: Any = None) -> None:
        """注入引擎引用（在 main 中组装依赖后调用）。

        Args:
            timer_engine: 计时引擎实例。
            break_engine: 休息引擎实例。
        """
        if timer_engine is not None:
            self._timer_engine = timer_engine
        if break_engine is not None:
            self._break_engine = break_engine
        logger.debug("UsageService 引擎引用已设置")

    # ------------------------------------------------------------------
    # 会话生命周期
    # ------------------------------------------------------------------
    def start_session(self) -> int:
        """开始新的使用会话，返回 session_id（本地递增）。

        若已有活跃会话，会先结束旧会话再开始新会话（把增量写回统计）。
        """
        if self._session_active:
            self.end_session()

        self._current_session_id = (self._current_session_id or 0) + 1
        self._baseline_active = self._safe_get_active_seconds()
        self._session_active = True
        logger.info("使用会话已开始 (id=%s)", self._current_session_id)
        return int(self._current_session_id)

    def end_session(self, session_id: Optional[int] = None) -> Optional[dict]:
        """结束当前会话，把本次会话的暴露时长增量写入统计存储。

        Args:
            session_id: 兼容旧接口，忽略（只维护单个当前会话）。

        Returns:
            本次会话摘要；无活跃会话时返回 None。
        """
        if not self._session_active:
            return None

        delta = max(0.0, self._safe_get_active_seconds() - self._baseline_active)
        try:
            self._store.add_screen_seconds(delta)
            self._store.flush()
        except Exception:  # noqa: BLE001
            logger.exception("写入会话时长失败")
        self._session_active = False
        summary = {
            "session_id": self._current_session_id,
            "active_seconds": float(delta),
        }
        logger.info("使用会话已结束: 本次 %.0fs", delta)
        return summary

    def get_current_session_id(self) -> Optional[int]:
        """返回当前会话 id；无活跃会话时返回 None。"""
        return self._current_session_id if self._session_active else None

    def is_session_active(self) -> bool:
        """返回是否存在活跃会话。"""
        return self._session_active

    # ------------------------------------------------------------------
    # 今日摘要
    # ------------------------------------------------------------------
    def get_today_summary(self) -> dict:
        """获取今日使用摘要。

        Returns:
            包含 ``active_seconds`` / ``idle_seconds`` / ``break_count`` /
            ``look_away_count`` / ``deep_break_count`` / ``skipped_breaks`` /
            ``blink_cues`` / ``move_count`` / ``session_count`` 的字典。

        注：``break_count`` 是 ``look_away_count + deep_break_count`` 的合计，
        保留给"总计"口径的调用方；首页今日数据卡分列远眺 / 活动 / 长休三列，
        应当各取各的独立计数（见 ``SPEC_COMPONENTS_AND_BLOCKS.md`` §4 第 3 条）。
        """
        try:
            today = self._store.today()
        except Exception:  # noqa: BLE001
            logger.exception("读取今日统计失败")
            today = {}

        stored_active = float(today.get("screen_seconds", 0.0))
        # 叠加当前会话尚未落盘的实时增量
        if self._session_active:
            stored_active += max(
                0.0, self._safe_get_active_seconds() - self._baseline_active
            )

        look_away = int(today.get("look_away", 0))
        deep = int(today.get("deep_break", 0))
        return {
            "active_seconds": stored_active,
            "idle_seconds": 0.0,
            "break_count": look_away + deep,
            "look_away_count": look_away,
            "deep_break_count": deep,
            "skipped_breaks": int(today.get("skipped_break", 0)),
            "blink_cues": int(today.get("blink_cue", 0)),
            "move_count": int(today.get("move", 0)),
            "session_count": 1 if self._session_active else 0,
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def format_duration(self, seconds: float) -> str:
        """格式化时长为 ``HH:MM:SS``。

        Args:
            seconds: 秒数。

        Returns:
            ``HH:MM:SS`` 形式的字符串（负数归零）。
        """
        try:
            total = max(0, int(float(seconds)))
        except (TypeError, ValueError):
            total = 0
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def format_short_duration(self, seconds: float) -> str:
        """格式化为简短形式（不足 1 小时为 ``MM:SS``，否则 ``HH:MM``）。

        Args:
            seconds: 秒数。

        Returns:
            简短时长字符串。
        """
        try:
            total = max(0, int(float(seconds)))
        except (TypeError, ValueError):
            total = 0
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}"
        return f"{minutes:02d}:{secs:02d}"

    # ------------------------------------------------------------------
    # 安全访问（引擎可能未注入或抛错）
    # ------------------------------------------------------------------
    def _safe_get_active_seconds(self) -> float:
        """安全读取计时引擎的活跃秒数（兼容方法式与属性式引擎）。"""
        engine = self._timer_engine
        if engine is None:
            return 0.0
        for name in ("get_active_seconds", "active_seconds"):
            try:
                value = getattr(engine, name)
                value = value() if callable(value) else value
                return float(value)
            except Exception:  # noqa: BLE001
                continue
        return 0.0

    def record_screen_seconds(self, seconds: float) -> None:
        """直接累计屏幕暴露时长（供 main 每秒 tick 调用，内部节流落盘）。"""
        try:
            self._store.add_screen_seconds(seconds)
        except Exception:  # noqa: BLE001
            logger.exception("累计屏幕暴露时长失败")
