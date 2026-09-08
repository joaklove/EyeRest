"""休息引擎（BreakEngine）。

实现 20-20-20 规则的核心调度逻辑：

* 每 20 分钟有效用眼 → 提前 30 秒警告 → 20 秒短休息
* 每 3 次短休息后触发 1 次 5 分钟长休息（间隔 90 分钟工作）
* 休息完成 / 跳过 / 自然休息后重置 active_seconds
* 支持延迟（postpone）：最多 2 次，每次 5 分钟
* 监听自然休息事件（idle ≥ 120s），被动重置计数

BreakEngine 只负责**状态流转与触发时机**，休息倒计时窗口（BreakWindow UI）
由上层负责。引擎通过 :class:`~app.core.event_bus.EventBus` 发布休息相关事件，
并驱动 :class:`~app.core.state_machine.StateMachine` 进行状态转换。

用法::

    from app.core.break_engine import BreakEngine

    engine = BreakEngine(timer_engine, state_machine, event_bus=bus)
    engine.start()
    # 每秒调用一次
    engine.tick()
    # ...
    engine.stop()
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from app.config import defaults
from app.core.event_bus import EventBus, EventType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class BreakEngine:
    """休息引擎。

    根据计时源的已用秒数，按 20-20-20 规则触发警告 / 短休息 / 长休息，
    并管理延迟、跳过、完成、自然休息等流程。

    V1.0：计时源默认使用 **屏幕暴露时间**
    （:class:`~app.core.screen_session.ScreenSessionEngine`），
    键鼠空闲不再重置用眼计时；未装配时回退 TimerEngine 的 ``active_seconds``。

    线程安全：所有可变状态由 ``threading.RLock`` 保护。
    """

    def __init__(
        self,
        timer_engine: Any,
        state_machine: Any,
        event_bus: Optional[EventBus] = None,
        config: Optional[dict] = None,
        fullscreen_provider: Optional[Callable[[], bool]] = None,
        defer_on_fullscreen: bool = True,
        session_engine: Optional[Any] = None,
    ) -> None:
        """初始化休息引擎。

        Args:
            timer_engine: 计时引擎，提供 ``get_active_seconds()`` / ``reset()`` 等接口。
            state_machine: 状态机，提供 ``on_break_warning()`` / ``on_break_triggered()``
                / ``on_break_completed()`` / ``on_break_skipped()`` / ``on_natural_rest()``。
            event_bus: 事件总线；为 None 时不发布事件也不订阅自然休息事件。
        config: 可选配置覆盖，键为 defaults 模块中的常量名（如 ``SHORT_WORK_DURATION``）。
        fullscreen_provider: 返回当前是否全屏的可调用对象；为 None 时不启用
            全屏延迟（V1.1 全屏检测）。
        defer_on_fullscreen: 全屏程序运行时是否延迟提醒，默认开启；
            设为 False 时即使处于全屏也会正常提醒。
        session_engine: 屏幕暴露会话引擎（:class:`~app.core.screen_session.ScreenSessionEngine`）。
            提供时休息时机基于**屏幕暴露时间**判定；为 None 时回退到
            TimerEngine 的 ``active_seconds``（向后兼容）。
        """
        self._timer_engine = timer_engine
        self._state_machine = state_machine
        self._bus = event_bus
        self._fullscreen_provider = fullscreen_provider
        self._defer_on_fullscreen = bool(defer_on_fullscreen)
        #: V1.0 屏幕暴露会话引擎；装配后休息时机基于**屏幕暴露时间**而非活跃时间
        self._session_engine = session_engine

        # 配置：允许通过 config 字典覆盖 defaults 中的值
        cfg = dict(config) if config else {}
        self._short_work_duration = float(
            cfg.get("SHORT_WORK_DURATION", defaults.SHORT_WORK_DURATION)
        )
        self._long_work_duration = float(
            cfg.get("LONG_WORK_DURATION", defaults.LONG_WORK_DURATION)
        )
        self._short_break_duration = float(
            cfg.get("SHORT_BREAK_DURATION", defaults.SHORT_BREAK_DURATION)
        )
        self._long_break_duration = float(
            cfg.get("LONG_BREAK_DURATION", defaults.LONG_BREAK_DURATION)
        )
        self._warning_duration = float(
            cfg.get("WARNING_DURATION", defaults.WARNING_DURATION)
        )
        self._postpone_duration = float(
            cfg.get("POSTPONE_DURATION", defaults.POSTPONE_DURATION)
        )
        self._max_postpone = int(cfg.get("MAX_POSTPONE", defaults.MAX_POSTPONE))

        # 可变状态
        self._lock = threading.RLock()
        self._running: bool = False
        self._short_break_count: int = 0  # 已完成短休息次数（达到 3 触发长休息）
        self._postpone_count: int = 0  # 当前轮已延迟次数
        self._postpone_offset: float = 0.0  # 延迟偏移秒数（累加进 work_duration）
        self._warning_triggered: bool = False  # 警告是否已触发（防重复）
        self._break_in_progress: bool = False  # 休息是否进行中
        self._current_break_type: str = "short"  # 当前休息类型 short/long
        self._break_count_today: int = 0  # 今日已完成休息数
        # V1.0：短休息会重置屏幕暴露计时，长周期必须自己记账，
        # 否则每次短休息后长休息计时被清零，90 分钟周期永远走不到。
        self._long_elapsed_accum: float = 0.0

        # 事件订阅句柄
        self._natural_rest_callback = None

    # ------------------------------------------------------------------
    # 运行时配置热更新（设置保存后调用，不重置计数/进度）
    # ------------------------------------------------------------------
    def update_settings(self, settings: dict) -> None:
        """运行时热更新配置（线程安全，不重置已累积的休息进度）。

        仅更新传入字典中存在的键，缺失键保持原值。供设置页保存后即时生效，
        无需重启或重建引擎。

        Args:
            settings: 部分或完整的设置字典，键名与 ``DEFAULT_SETTINGS`` 一致。
        """
        if not isinstance(settings, dict):
            return
        with self._lock:
            if "short_work_duration" in settings:
                self._short_work_duration = float(settings["short_work_duration"])
            if "long_work_duration" in settings:
                self._long_work_duration = float(settings["long_work_duration"])
            if "short_break_duration" in settings:
                self._short_break_duration = float(settings["short_break_duration"])
            if "long_break_duration" in settings:
                self._long_break_duration = float(settings["long_break_duration"])
            if "warning_duration" in settings:
                self._warning_duration = float(settings["warning_duration"])
            if "postpone_duration" in settings:
                self._postpone_duration = float(settings["postpone_duration"])
            if "max_postpone" in settings:
                self._max_postpone = int(settings["max_postpone"])
            if "defer_on_fullscreen" in settings:
                self._defer_on_fullscreen = bool(settings["defer_on_fullscreen"])
        logger.info("BreakEngine 配置已热更新: %s", {k: settings[k] for k in settings})

    # ------------------------------------------------------------------
    # 生命周期控制
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动休息检测，订阅自然休息事件。

        若已启动则忽略。
        """
        with self._lock:
            if self._running:
                logger.debug("BreakEngine 已在运行，忽略 start")
                return
            self._running = True

        # 订阅 ACTIVITY_CHANGED 事件以感知自然休息
        if self._bus is not None and self._natural_rest_callback is None:
            self._natural_rest_callback = self._on_activity_changed
            self._bus.subscribe(EventType.ACTIVITY_CHANGED, self._natural_rest_callback)

        logger.info("BreakEngine 已启动")

    def stop(self) -> None:
        """停止休息检测，取消事件订阅。

        若已停止则忽略。
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._bus is not None and self._natural_rest_callback is not None:
            self._bus.unsubscribe(
                EventType.ACTIVITY_CHANGED, self._natural_rest_callback
            )
            self._natural_rest_callback = None

        logger.info("BreakEngine 已停止")

    # ------------------------------------------------------------------
    # tick 核心逻辑
    # ------------------------------------------------------------------
    def tick(self, delta_seconds: float = 1.0) -> None:
        """每秒调用一次，检测是否需要警告 / 休息。

        Args:
            delta_seconds: 本次推进的秒数（当前实现不直接使用，由 TimerEngine 累加）。
        """
        with self._lock:
            if not self._running or self._break_in_progress:
                return

        # 全屏时延迟提醒与休息，避免打断全屏工作（V1.1 全屏检测）
        # 仅当 defer_on_fullscreen 开启时生效（用户可在设置中关闭）
        if self._defer_on_fullscreen and self.is_fullscreen():
            return

        # 在锁外读取已用时长，避免长时间持锁
        try:
            active = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            logger.exception("获取已用时长失败，跳过本次 tick")
            return

        with self._lock:
            # 休息触发优先于警告
            due = self._get_due_break_locked(active)
            if due is not None:
                self._on_break_start_locked(due)
                return
            if self._should_warn_locked(active) and not self._warning_triggered:
                self._on_warning_locked()

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def should_warn(self) -> bool:
        """是否应该触发警告（已用时长 >= work_duration - warning_duration）。"""
        try:
            active = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            return False
        with self._lock:
            return self._should_warn_locked(active)

    def should_break(self) -> bool:
        """是否应该触发休息（已用时长 >= work_duration）。"""
        try:
            active = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            return False
        with self._lock:
            return self._should_break_locked(active)

    def get_remaining_seconds(self) -> float:
        """距离下一次休息（远眺或深度休息，先到者）的剩余秒数。"""
        try:
            active = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            return 0.0
        with self._lock:
            return self._next_remaining_locked(active)

    def get_long_remaining_seconds(self) -> float:
        """距离**深度休息**（Deep Break）的剩余秒数（不会小于 0）。

        V1.0 正式版：深度休息与短休息解耦，由独立长周期账本驱动。
        """
        try:
            elapsed = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            return 0.0
        with self._lock:
            return self._deep_remaining_locked(elapsed)

    #: 语义化别名：深度休息剩余秒数
    get_deep_remaining_seconds = get_long_remaining_seconds

    def get_long_work_duration(self) -> float:
        """长周期工作时长（秒），供 UI 计算长休息进度。"""
        with self._lock:
            return self._long_work_duration

    def get_elapsed_seconds(self) -> float:
        """当前计时源的已用秒数（屏幕暴露时间优先）。"""
        try:
            return self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            return 0.0

    def get_current_break_type(self) -> str:
        """获取当前休息类型 ``short`` / ``long``。

        基于已完成短休息次数推导：短休息次数 < 3 → short，否则 long。
        """
        with self._lock:
            return self._get_break_type_locked()

    def get_break_count_today(self) -> int:
        """今日已完成休息数（含短休息与长休息）。"""
        with self._lock:
            return self._break_count_today

    def get_short_break_count(self) -> int:
        """已完成短休息次数（供测试与调试）。"""
        with self._lock:
            return self._short_break_count

    def get_postpone_count(self) -> int:
        """当前轮已延迟次数（供测试与调试）。"""
        with self._lock:
            return self._postpone_count

    def is_running(self) -> bool:
        """引擎是否正在运行。"""
        with self._lock:
            return self._running

    def is_break_in_progress(self) -> bool:
        """休息是否进行中。"""
        with self._lock:
            return self._break_in_progress

    def is_warning_triggered(self) -> bool:
        """警告是否已触发。"""
        with self._lock:
            return self._warning_triggered

    def is_fullscreen(self) -> bool:
        """当前前台窗口是否全屏（容错，未注入检测器时返回 False）。"""
        if self._fullscreen_provider is None:
            return False
        try:
            return bool(self._fullscreen_provider())
        except Exception:  # noqa: BLE001
            logger.exception("全屏检测失败，视为未全屏")
            return False

    # ------------------------------------------------------------------
    # 事件回调
    # ------------------------------------------------------------------
    def on_warning(self) -> None:
        """触发警告（发布 BREAK_WARNING 事件并驱动状态机）。"""
        with self._lock:
            self._on_warning_locked()

    def on_break_start(self, break_type: str = "short") -> None:
        """开始休息（发布 BREAK_TRIGGERED 事件并驱动状态机）。

        Args:
            break_type: 休息类型 ``short`` / ``long``，为 None 或其他值时自动推导。
        """
        with self._lock:
            self._on_break_start_locked(break_type)

    def on_break_complete(self) -> None:
        """休息完成：重置 active_time、计数归零、发布 BREAK_COMPLETED。"""
        self._finish_break(completed=True)

    def on_break_skip(self) -> None:
        """跳过休息：重置 active_time、计数归零、发布 BREAK_SKIPPED。"""
        self._finish_break(completed=False)

    def on_postpone(self) -> bool:
        """延迟休息（POSTPONE_DURATION 秒后重新触发）。

        延迟期间 active_seconds 继续累加，但 work_duration 同步增加偏移，
        因此实际触发时间被推迟。延迟计数 ≤ MAX_POSTPONE 时返回 True。

        Returns:
            是否延迟成功。
        """
        with self._lock:
            if self._postpone_count >= self._max_postpone:
                logger.info("已达到最大延迟次数 %d，无法继续延迟", self._max_postpone)
                return False
            self._postpone_count += 1
            self._postpone_offset += self._postpone_duration
            # 重置警告状态，延迟期间重新累积到新的警告阈值
            self._warning_triggered = False
            logger.info(
                "休息已延迟 %d 次，本次延迟 %.0fs，偏移总计 %.0fs",
                self._postpone_count,
                self._postpone_duration,
                self._postpone_offset,
            )
        return True

    # ------------------------------------------------------------------
    # 自然休息处理
    # ------------------------------------------------------------------
    def _on_activity_changed(self, data: Any) -> None:
        """处理 ACTIVITY_CHANGED 事件，感知自然休息。

        Args:
            data: 事件 payload，预期包含 ``is_natural_rest`` 布尔字段。
        """
        if not isinstance(data, dict):
            return
        if not data.get("is_natural_rest"):
            return

        with self._lock:
            if not self._running:
                return
            # 休息进行中不响应自然休息（由 BreakWindow 接管）
            if self._break_in_progress:
                return

        logger.info("检测到自然休息（idle ≥ 120s），重置 active_time")

        # 重置计时引擎
        self._safe_timer_reset()
        # 驱动状态机
        self._safe_state_call("on_natural_rest")
        # 重置警告与延迟状态（自然休息等同于被动重置）
        with self._lock:
            self._warning_triggered = False
            self._postpone_count = 0
            self._postpone_offset = 0.0
        # 自然休息不增加休息计数

    # ------------------------------------------------------------------
    # 内部逻辑（调用方需持锁）
    # ------------------------------------------------------------------
    def _get_elapsed_seconds(self) -> float:
        """返回用于判定休息时机的「已用时长」（秒）。

        V1.0 语义：优先使用 **屏幕暴露时间**
        （:class:`~app.core.screen_session.ScreenSessionEngine` 的
        ``exposure_seconds``）—— 屏幕亮着、未锁屏即算用眼，键鼠空闲
        不中断计时；未装配 SessionEngine 时回退到 TimerEngine 的
        ``get_active_seconds()``，保持向后兼容。

        Raises:
            Exception: 读取失败时向上抛出，由调用方容错处理。
        """
        if self._session_engine is not None:
            try:
                return max(0.0, float(self._session_engine.exposure_seconds))
            except Exception:  # noqa: BLE001
                logger.exception("读取屏幕暴露时长失败，回退 TimerEngine")
        if self._timer_engine is None:
            return 0.0
        return max(0.0, float(self._timer_engine.get_active_seconds()))

    def _get_work_duration_locked(self) -> float:
        """下一轮休息对应的工作时长（不含延迟偏移）。

        V1.0 正式版：短休息（远眺）固定 20 分钟一轮；
        深度休息由独立的长周期账本驱动（见 ``_deep_remaining_locked``）。
        """
        return self._short_work_duration

    def _short_remaining_locked(self, elapsed: float) -> float:
        """距离远眺（Look Away，短休息）的剩余秒数。"""
        return max(0.0, self._short_work_duration + self._postpone_offset - elapsed)

    def _deep_remaining_locked(self, elapsed: float) -> float:
        """距离深度休息（Deep Break）的剩余秒数。

        深度休息与短休息**解耦**：它由独立的长周期账本累计
        （短休息会重置屏幕暴露计时，但不清零长周期账本），
        到期时间只取决于总暴露时长，不再依赖"完成了几次短休息"。
        """
        total = self._long_elapsed_accum + elapsed
        return max(0.0, self._long_work_duration + self._postpone_offset - total)

    def _get_due_break_locked(self, elapsed: float) -> Optional[str]:
        """当前到期的休息类型；两者同时到期时深度休息优先。"""
        if self._deep_remaining_locked(elapsed) <= 0:
            return "long"
        if self._short_remaining_locked(elapsed) <= 0:
            return "short"
        return None

    def _next_remaining_locked(self, elapsed: float) -> float:
        """距离下一次任意休息的剩余秒数。"""
        return min(
            self._short_remaining_locked(elapsed),
            self._deep_remaining_locked(elapsed),
        )

    def _get_next_break_type_locked(self, elapsed: float) -> str:
        """下一次将触发的休息类型（先到者）。"""
        return (
            "long"
            if self._deep_remaining_locked(elapsed) <= self._short_remaining_locked(elapsed)
            else "short"
        )

    def _get_total_work_duration_locked(self) -> float:
        """兼容保留：下一轮短休息的总工作时长（含延迟偏移）。"""
        return self._short_work_duration + self._postpone_offset

    def _get_break_type_locked(self) -> str:
        """获取当前应触发的休息类型。"""
        try:
            elapsed = self._get_elapsed_seconds()
        except Exception:  # noqa: BLE001
            elapsed = 0.0
        return self._get_next_break_type_locked(elapsed)

    def _should_warn_locked(self, elapsed: float) -> bool:
        """是否应触发警告（内部实现，调用方需持锁）。"""
        remaining = self._next_remaining_locked(elapsed)
        return 0.0 < remaining <= self._warning_duration

    def _should_break_locked(self, elapsed: float) -> bool:
        """是否应触发休息（内部实现，调用方需持锁）。"""
        return self._get_due_break_locked(elapsed) is not None

    def _on_warning_locked(self) -> None:
        """触发警告（内部实现，调用方需持锁）。"""
        if self._warning_triggered:
            return
        if self._break_in_progress:
            return
        self._warning_triggered = True
        logger.info("触发休息警告（剩余 %.0fs）", self.get_remaining_seconds())
        self._safe_state_call("on_break_warning")
        self._publish(
            EventType.BREAK_WARNING,
            {
                "break_type": self._get_break_type_locked(),
                "remaining_seconds": self.get_remaining_seconds(),
            },
        )

    def _on_break_start_locked(self, break_type: Optional[str] = None) -> None:
        """开始休息（内部实现，调用方需持锁）。"""
        if self._break_in_progress:
            return
        if break_type is None:
            break_type = self._get_break_type_locked()
        normalized = "long" if break_type == "long" else "short"
        self._current_break_type = normalized
        self._break_in_progress = True
        self._warning_triggered = False
        duration = (
            self._long_break_duration
            if normalized == "long"
            else self._short_break_duration
        )
        logger.info("触发%s休息（时长 %.0fs）", normalized, duration)
        self._safe_state_call("on_break_triggered", normalized)
        self._publish(
            EventType.BREAK_TRIGGERED,
            {
                "break_type": normalized,
                "duration": duration,
            },
        )

    def _finish_break(self, completed: bool) -> None:
        """结束休息的公共逻辑。

        Args:
            completed: True 表示正常完成，False 表示跳过。
        """
        with self._lock:
            if not self._break_in_progress:
                logger.debug("当前无休息进行中，忽略 finish_break")
                return
            break_type = self._current_break_type
            event_type = (
                EventType.BREAK_COMPLETED if completed else EventType.BREAK_SKIPPED
            )
            method_name = "on_break_completed" if completed else "on_break_skipped"
            state_reason = "break_completed" if completed else "break_skipped"

            # 先把本轮已用时长记进长周期账本——下面会重置计时源
            try:
                elapsed = self._get_elapsed_seconds()
            except Exception:  # noqa: BLE001
                elapsed = 0.0

            # 释放休息进行中标记，避免状态机回调重入
            self._break_in_progress = False
            self._warning_triggered = False

            # 更新短休息计数与长周期累计
            if break_type == "short":
                self._short_break_count += 1
                self._long_elapsed_accum += elapsed
            else:  # long 休息后重置短休息计数与长周期账本
                self._short_break_count = 0
                self._long_elapsed_accum = 0.0

            # 重置延迟计数与偏移
            self._postpone_count = 0
            self._postpone_offset = 0.0

            # 今日休息计数 +1（跳过也算一次休息，因为重置了 active_time）
            self._break_count_today += 1

        # 锁外执行可能耗时的外部调用
        self._safe_state_call(method_name)
        self._safe_timer_reset()
        self._publish(
            event_type,
            {
                "break_type": break_type,
                "reason": state_reason,
                "short_break_count": self._short_break_count,
            },
        )
        logger.info(
            "休息%s：type=%s short_count=%d today=%d",
            "完成" if completed else "跳过",
            break_type,
            self._short_break_count,
            self._break_count_today,
        )

    # ------------------------------------------------------------------
    # 容错辅助
    # ------------------------------------------------------------------
    def _publish(self, event_type: EventType, data: Any = None) -> None:
        """发布事件（容错，不抛出异常）。"""
        if self._bus is None:
            return
        try:
            self._bus.publish(event_type, data)
        except Exception:  # noqa: BLE001
            logger.exception("发布事件失败: %s", event_type.value)

    def _safe_state_call(self, method_name: str, *args: Any) -> None:
        """安全调用状态机方法（容错）。"""
        if self._state_machine is None:
            return
        method = getattr(self._state_machine, method_name, None)
        if method is None or not callable(method):
            return
        try:
            method(*args)
        except Exception:  # noqa: BLE001
            logger.exception("调用状态机方法 %s 失败", method_name)

    def _safe_timer_reset(self) -> None:
        """安全重置计时源（容错）。

        V1.0：装配了 ScreenSessionEngine 时优先重置屏幕暴露会话
        （开启一段全新的会话），否则回退重置 TimerEngine。
        """
        if self._session_engine is not None:
            method = getattr(self._session_engine, "reset_session", None)
            if method is not None and callable(method):
                try:
                    method("break_finished")
                    return
                except Exception:  # noqa: BLE001
                    logger.exception("重置 ScreenSessionEngine 失败，回退 TimerEngine")
        if self._timer_engine is None:
            return
        method = getattr(self._timer_engine, "reset", None)
        if method is None or not callable(method):
            return
        try:
            method()
        except Exception:  # noqa: BLE001
            logger.exception("重置 TimerEngine 失败")
