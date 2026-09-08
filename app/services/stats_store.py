"""今日与近 14 天统计存储（V0.5 减法重构：替代原 SQLite 统计表）。

V1 不做复杂统计，也不再依赖数据库；这里用一个极小的 JSON 文件
（数据目录下的 ``stats.json``）记录四层节奏的**触发次数**与**屏幕暴露
时长**，供 Dashboard 与统计页展示。

数据诚实原则：记录的是「提示触发次数」，不是用户真实眨眼次数。

结构::

    {
      "days": {
        "2026-09-08": {
          "screen_seconds": 12345,
          "blink_cue": 72,
          "look_away": 14,
          "move": 5,
          "deep_break": 2
        }
      }
    }

写入策略：计数与秒数先在内存累计，累计到阈值或显式 ``flush()``
时才落盘，避免每秒写文件。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.config import defaults
from app.utils.logger import get_logger

_log = get_logger(__name__)

#: 统计键：四层节奏触发次数 + 深度休息跳过次数
KIND_KEYS: tuple[str, ...] = (
    "blink_cue",
    "look_away",
    "move",
    "deep_break",
    "skipped_break",
)

#: 保留的历史天数（超出自动丢弃，文件体积恒定）
MAX_HISTORY_DAYS = 30

#: 累计多少秒数/次数的变化才落盘一次
_FLUSH_THRESHOLD = 30


def _empty_day() -> dict[str, Any]:
    """构造一天的空统计记录。"""
    return {"screen_seconds": 0.0, **{key: 0 for key in KIND_KEYS}}


class StatsStore:
    """基于 JSON 文件的轻量统计存储。"""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = get_stats_path()
        self._path = Path(path)
        self._days: dict[str, dict[str, Any]] = {}
        self._pending = 0
        self._load()
        self._rollover()
        # 首次启动落盘今日骨架，保证 stats.json 文件始终可见
        if not self._path.exists():
            self.today()
            self.flush()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        """统计文件路径。"""
        return self._path

    def today(self) -> dict[str, Any]:
        """返回今日统计（含 ``date`` 键）。"""
        self._rollover()
        day = self._days.get(_today_str())
        if day is None:
            day = _empty_day()
            self._days[_today_str()] = day
        return {"date": _today_str(), **day}

    def history(self, days: int = 7) -> list[dict[str, Any]]:
        """返回最近 ``days`` 天（含今天）的统计，按日期升序。

        缺失的日期补零，保证图表数据连续。
        """
        self._rollover()
        result: list[dict[str, Any]] = []
        today = date.today()
        for offset in range(days - 1, -1, -1):
            day_str = (today - timedelta(days=offset)).isoformat()
            stored = self._days.get(day_str)
            result.append({"date": day_str, **(stored or _empty_day())})
        return result

    def get_day(self, day_str: str) -> dict[str, Any]:
        """返回指定日期（``YYYY-MM-DD``）的统计，缺失时补零。"""
        stored = self._days.get(day_str)
        return {"date": day_str, **(stored or _empty_day())}

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def record(self, kind: str, count: int = 1) -> None:
        """记录一次提醒触发（kind 见 :data:`KIND_KEYS`）。"""
        if kind not in KIND_KEYS:
            _log.warning("未知统计类型: %s", kind)
            return
        day = self._ensure_today()
        day[kind] = int(day.get(kind, 0)) + max(0, int(count))
        self._touch()

    def add_screen_seconds(self, seconds: float) -> None:
        """累计屏幕暴露时长（内部按阈值节流落盘）。"""
        if seconds <= 0:
            return
        day = self._ensure_today()
        day["screen_seconds"] = float(day.get("screen_seconds", 0.0)) + float(seconds)
        self._pending += 1
        if self._pending >= _FLUSH_THRESHOLD:
            self.flush()

    def flush(self) -> None:
        """强制把内存中的统计写入磁盘。"""
        self._pending = 0
        self._save()

    def reset(self) -> None:
        """清空全部统计数据。"""
        self._days = {}
        self._pending = 0
        self._save()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _ensure_today(self) -> dict[str, Any]:
        self._rollover()
        key = _today_str()
        if key not in self._days:
            self._days[key] = _empty_day()
        return self._days[key]

    def _rollover(self) -> None:
        """跨日时补齐新的一天，并裁剪超出的历史。"""
        key = _today_str()
        if key not in self._days:
            self._days[key] = _empty_day()
            # 首次进入新的一天时主动落盘，保证日期存在
            self._pending += 1
        if len(self._days) > MAX_HISTORY_DAYS:
            for old in sorted(self._days.keys())[:-MAX_HISTORY_DAYS]:
                self._days.pop(old, None)

    def _touch(self) -> None:
        """计数变化后落盘（次数型数据较少，直接写）。"""
        self._pending = 0
        self._save()

    def _load(self) -> None:
        try:
            if self._path.exists():
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("days"), dict):
                    self._days = loaded["days"]
                else:
                    _log.warning("统计文件格式异常，已重置: %s", self._path)
                    self._days = {}
        except (json.JSONDecodeError, OSError):
            _log.exception("统计文件读取失败，已重置: %s", self._path)
            self._days = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=".stats-", suffix=".tmp", dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"days": self._days}, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self._path)
            except Exception:
                try:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            _log.exception("统计文件写入失败: %s", self._path)


def _today_str() -> str:
    """返回本地今日日期字符串。"""
    return date.today().isoformat()


def get_stats_path() -> Path:
    """返回统计文件路径（数据目录下 ``stats.json``）。"""
    from app.utils.system import get_app_data_dir

    return Path(get_app_data_dir()) / defaults.STATS_FILENAME


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_store: StatsStore | None = None


def get_stats_store() -> StatsStore:
    """返回全局统计存储单例。"""
    global _store
    if _store is None:
        _store = StatsStore()
    return _store


def reset_stats_store() -> None:
    """重置单例（测试用）。"""
    global _store
    _store = None
