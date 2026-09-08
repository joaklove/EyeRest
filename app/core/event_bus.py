"""事件总线：简单的发布/订阅模式，线程安全。

用法::

    from app.core.event_bus import EventBus, EventType

    bus = EventBus()
    bus.subscribe(EventType.ActivityChanged, lambda data: print(data))
    bus.publish(EventType.ActivityChanged, {"is_idle": False})
    bus.unsubscribe(EventType.ActivityChanged, callback)
"""

from __future__ import annotations

import threading
from collections import defaultdict
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    """事件类型枚举。"""

    # 活动状态变化（active / idle）
    ACTIVITY_CHANGED = "activity_changed"
    # 应用状态机变化（running / paused / suspended 等）
    STATE_CHANGED = "state_changed"
    # 休息即将触发（提前提醒）
    BREAK_WARNING = "break_warning"
    # 休息触发
    BREAK_TRIGGERED = "break_triggered"
    # 休息完成
    BREAK_COMPLETED = "break_completed"
    # 休息跳过
    BREAK_SKIPPED = "break_skipped"
    # 应用暂停
    APP_PAUSE = "app_pause"
    # 应用恢复
    APP_RESUME = "app_resume"
    # 设置变更
    SETTINGS_CHANGED = "settings_changed"
    # 系统锁屏
    SYSTEM_LOCK = "system_lock"
    # 系统解锁
    SYSTEM_UNLOCK = "system_unlock"
    # 系统睡眠
    SYSTEM_SLEEP = "system_sleep"
    # 系统唤醒
    SYSTEM_WAKE = "system_wake"

    # --- V1.0 屏幕暴露节奏（Screen Exposure Rhythm）---
    # 屏幕暴露会话状态变化（on / away / off）
    SCREEN_SESSION_CHANGED = "screen_session_changed"
    # 判定自然休息（离开超过 natural_rest_threshold）
    NATURAL_REST_DETECTED = "natural_rest_detected"
    # 眨眼提示（Blink Cycle 内的一次 Cue；V1.0 正式版）
    BLINK_CUE = "blink_cue"
    # Blink Cycle 开始 / 结束（统计口径：cycle_started / cycle_finished）
    BLINK_CYCLE_STARTED = "blink_cycle_started"
    BLINK_CYCLE_FINISHED = "blink_cycle_finished"
    # 活动提醒（Move，V1.0 第三层节奏）
    MOVE_CUE = "move_cue"
    # 以下两个为 V1.0 早期遗留枚举：V1 没有摄像头，无法知道用户是否真眨眼，
    # 不再发布"完成/跳过"语义的眨眼事件（数据真实性原则）。保留枚举值兼容旧订阅。
    BLINK_COMPLETED = "blink_completed"
    BLINK_SKIPPED = "blink_skipped"


# 回调类型：接收一个可选 data 参数，不返回值
Callback = Callable[[Any], None]


class EventBus:
    """线程安全的发布/订阅事件总线。

    同一事件类型可注册多个回调；回调异常会被捕获并记录，不影响其他回调。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[EventType, list[Callback]] = defaultdict(list)

    def subscribe(self, event_type: EventType, callback: Callback) -> None:
        """注册事件监听器。

        Args:
            event_type: 事件类型
            callback: 回调函数，签名 ``callback(data=None)``
        """
        with self._lock:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: EventType, callback: Callback) -> None:
        """取消事件监听器。

        Args:
            event_type: 事件类型
            callback: 注册时使用的回调函数
        """
        with self._lock:
            handlers = self._subscribers.get(event_type)
            if handlers and callback in handlers:
                handlers.remove(callback)

    def publish(self, event_type: EventType, data: Any = None) -> None:
        """发布事件，同步调用所有已注册的监听器。

        Args:
            event_type: 事件类型
            data: 可选的事件数据
        """
        # 复制一份，避免回调中增删监听器导致迭代异常
        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))

        for callback in handlers:
            try:
                callback(data)
            except Exception:  # noqa: BLE001
                # 不允许单个回调异常影响整个事件分发
                import logging

                logging.getLogger(__name__).exception(
                    "事件监听器执行失败: event=%s callback=%s",
                    event_type.value,
                    getattr(callback, "__name__", repr(callback)),
                )

    def clear(self) -> None:
        """清空所有订阅（主要用于测试或重置）。"""
        with self._lock:
            self._subscribers.clear()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_global_bus: EventBus | None = None
_global_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """获取全局 EventBus 单例。"""
    global _global_bus
    with _global_lock:
        if _global_bus is None:
            _global_bus = EventBus()
        return _global_bus
