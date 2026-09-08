"""休息配置服务 - 管理休息相关设置。

负责在 JSON 设置存储（``JsonSettingsStore``，V0.5 起替代 SQLite）之上
封装类型安全的读写接口，并在设置变更时通过 EventBus 发布
``SETTINGS_CHANGED`` 事件。

所有时长均以「秒」为单位存储；UI 层负责与分钟的换算。
"""

from __future__ import annotations

from typing import Any

from app.config import defaults as default_constants
from app.config.settings_store import JsonSettingsStore, get_settings_store
from app.core.event_bus import EventBus, EventType, get_event_bus
from app.i18n import tr
from app.utils.logger import get_logger

_log = get_logger(__name__)


class BreakService:
    """休息配置服务 - 管理休息相关设置。

    提供类型安全的设置读写、批量保存与重置，并在变更时发布事件。
    数据库中的值统一以字符串存储，读取时根据 ``DEFAULT_SETTINGS``
    声明的类型自动转换为 ``int`` / ``bool``。
    """

    DEFAULT_SETTINGS: dict[str, Any] = {
        "short_work_duration": default_constants.SHORT_WORK_DURATION,      # 1200 秒
        "short_break_duration": default_constants.SHORT_BREAK_DURATION,    # 20 秒
        "long_work_duration": default_constants.LONG_WORK_DURATION,        # 5400 秒
        "long_break_duration": default_constants.LONG_BREAK_DURATION,      # 300 秒
        "warning_duration": default_constants.WARNING_DURATION,            # 30 秒
        # --- V0.5：四层节奏各自的开关 ---
        "look_away_enabled": default_constants.LOOK_AWAY_ENABLED,          # 远眺提醒
        "deep_break_enabled": default_constants.DEEP_BREAK_ENABLED,        # 深度休息提醒
        "idle_threshold": default_constants.IDLE_THRESHOLD,                # 60 秒
        "natural_rest_threshold": default_constants.NATURAL_REST_THRESHOLD,  # 300 秒
        "postpone_duration": default_constants.POSTPONE_DURATION,          # 300 秒
        "max_postpone": default_constants.MAX_POSTPONE,                    # 2 次
        # --- V1.0 屏幕暴露节奏 ---
        # 离开多久算 AWAY（暂停累计暴露，但不重置计时器）
        "away_threshold": default_constants.AWAY_THRESHOLD,                # 180 秒
        # 从 AWAY/OFF 恢复所需的活跃信号
        "session_resume_threshold": default_constants.SESSION_RESUME_THRESHOLD,  # 60 秒
        # --- 眨眼提示（V1.0 正式版：Blink Cycle 多次 Cue）---
        "blink_enabled": default_constants.BLINK_ENABLED,                  # 启用眨眼提示
        "blink_cycle_seconds": default_constants.BLINK_CYCLE_SECONDS,      # 60 秒周期
        "blink_cue_interval": default_constants.BLINK_CUE_INTERVAL,        # 周期内 10 秒一次
        "blink_cue_count": default_constants.BLINK_CUE_COUNT,              # 每周期 5 次
        "blink_cue_duration": default_constants.BLINK_CUE_DURATION,        # 3 秒
        # --- 活动提醒（Move，V1.0 第三层）---
        "move_enabled": default_constants.MOVE_ENABLED,                    # 启用活动提醒
        "move_interval": default_constants.MOVE_INTERVAL,                  # 45 分钟
        "move_duration": default_constants.MOVE_DURATION,                  # 建议 3 分钟
        # --- 统一视觉组件（Skin / 位置 / 强度）---
        "cue_skin": default_constants.CUE_SKIN,                            # 极简眼睛
        "cue_position": default_constants.CUE_POSITION,                    # 底部中央
        "cue_pos_x": default_constants.CUE_POS_X,                          # 自定义坐标
        "cue_pos_y": default_constants.CUE_POS_Y,
        "cue_pos_monitor": default_constants.CUE_POS_MONITOR,
        "cue_position_locked": default_constants.CUE_POSITION_LOCKED,      # 锁定位置
        "cue_intensity": default_constants.CUE_INTENSITY,                  # 标准强度
        # --- 声音（V0.5 五套提示音）---
        "enable_sound": default_constants.ENABLE_SOUND,                    # 提示音总开关
        "sound_scheme": default_constants.SOUND_SCHEME,                    # 音效方案
        "sound_volume": default_constants.SOUND_VOLUME,                    # 音量 0~1
        "enable_notification": True,                                       # 启用系统通知
        "auto_start": False,                                               # 开机自启
        "minimize_to_tray": True,                                          # 最小化到托盘
        "defer_on_fullscreen": default_constants.DEFER_ON_FULLSCREEN,      # 全屏时延迟提醒
        "language": "zh",                                                  # 界面语言
    }

    def __init__(
        self,
        store: JsonSettingsStore | None = None,
        event_bus: EventBus | None = None,
        db: Any = None,
    ) -> None:
        """初始化休息配置服务。

        Args:
            store: JSON 设置存储实例。未提供时使用全局单例。
            event_bus: EventBus 实例。未提供时使用全局单例 ``get_event_bus()``。
            db: **已废弃**，仅为兼容旧调用保留，不再使用。
        """
        self._store = store if store is not None else get_settings_store()
        self._event_bus = event_bus if event_bus is not None else get_event_bus()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _coerce(self, key: str, raw: Any) -> Any:
        """将存储值转换为 ``DEFAULT_SETTINGS`` 声明的类型。

        JSON 原生支持 bool/int/str，但旧版本曾以字符串存储，这里统一兜底。
        """
        default = self.DEFAULT_SETTINGS.get(key)
        if isinstance(default, bool):
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
        return raw

    # ------------------------------------------------------------------
    # 单个设置读写
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        """获取单个设置值。

        若配置中不存在该键，且该键在 ``DEFAULT_SETTINGS`` 中声明，
        则返回声明的默认值；否则返回 ``default`` 参数。

        Args:
            key: 设置键名。
            default: 当键不存在且无内置默认值时返回的兜底值。

        Returns:
            转换后的设置值。
        """
        raw = self._store.get(key, None)
        if raw is None:
            if key in self.DEFAULT_SETTINGS:
                return self.DEFAULT_SETTINGS[key]
            return default
        return self._coerce(key, raw)

    def set_setting(self, key: str, value: Any) -> None:
        """设置单个设置，并发布 ``SETTINGS_CHANGED`` 事件。

        Args:
            key: 设置键名。
            value: 设置值（以 JSON 原生类型存储）。
        """
        self._store.set(key, value)
        _log.debug("设置已更新: %s = %r", key, value)
        if self._event_bus is not None:
            self._event_bus.publish(
                EventType.SETTINGS_CHANGED,
                {"key": key, "value": value},
            )

    # ------------------------------------------------------------------
    # 批量读写
    # ------------------------------------------------------------------
    def get_all_settings(self) -> dict[str, Any]:
        """获取所有设置（含默认值）。

        返回 ``DEFAULT_SETTINGS`` 中全部键的当前值，未显式存储的键
        回退到内置默认值。
        """
        result: dict[str, Any] = {}
        for key, default in self.DEFAULT_SETTINGS.items():
            raw = self._store.get(key, None)
            result[key] = self._coerce(key, raw) if raw is not None else default
        return result

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """批量保存设置。

        仅保存 ``DEFAULT_SETTINGS`` 中已声明的键，未知键会被忽略。
        保存完成后发布一次 ``SETTINGS_CHANGED`` 事件，携带所有变更项。

        Args:
            settings: 待保存的设置字典。

        Returns:
            实际写入并发生变更的设置字典。
        """
        changed: dict[str, Any] = {}
        current = self.get_all_settings()
        for key, value in settings.items():
            if key not in self.DEFAULT_SETTINGS:
                continue
            if current.get(key) == value:
                continue
            self._store.set(key, value)
            changed[key] = value
        if changed:
            _log.debug("批量保存设置: %s", changed)
            if self._event_bus is not None:
                self._event_bus.publish(
                    EventType.SETTINGS_CHANGED,
                    {"settings": changed},
                )
        return changed

    def reset_to_defaults(self) -> None:
        """重置为默认设置。

        删除配置中所有 ``DEFAULT_SETTINGS`` 声明的键，使其回退到
        内置默认值，并发布 ``SETTINGS_CHANGED`` 事件。
        """
        for key in self.DEFAULT_SETTINGS:
            self._store.delete(key)
        _log.info("已重置所有设置为默认值")
        if self._event_bus is not None:
            self._event_bus.publish(
                EventType.SETTINGS_CHANGED,
                {"reset": True, "settings": dict(self.DEFAULT_SETTINGS)},
            )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def format_duration_label(self, seconds: int) -> str:
        """格式化时长标签（秒→分钟/小时），文案随界面语言本地化。

        Examples:
            >>> svc.format_duration_label(20)
            '20 秒'
            >>> svc.format_duration_label(1200)
            '20 分钟'
            >>> svc.format_duration_label(5400)
            '1 小时 30 分钟'

        Args:
            seconds: 秒数。

        Returns:
            人类可读的时长字符串。
        """
        try:
            total = int(seconds)
        except (TypeError, ValueError):
            return str(seconds)

        if total < 60:
            return tr("duration.seconds", seconds=total)

        minutes, sec = divmod(total, 60)
        if minutes < 60:
            if sec:
                return tr("duration.minutes_seconds", minutes=minutes, seconds=sec)
            return tr("duration.minutes", minutes=minutes)

        hours, minutes = divmod(minutes, 60)
        if minutes:
            return tr("duration.hours_minutes", hours=hours, minutes=minutes)
        return tr("duration.hours", hours=hours)
