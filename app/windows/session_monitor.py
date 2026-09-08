"""会话事件监控：监听系统锁屏 / 解锁事件。

提供两种工作模式：

1. **Qt 通知模式**（首选，实时可靠）：创建隐藏的 ``QWidget``，通过
   ``WTSRegisterSessionNotification`` 注册会话通知，在 Qt 事件循环中
   捕获 ``WM_WTSSESSION_CHANGE`` 消息，实时检测锁屏（``WTS_SESSION_LOCK``）
   与解锁（``WTS_SESSION_UNLOCK``）。

2. **轮询模式**（回退 / 可测试）：后台线程以固定间隔调用可注入的
   ``session_state_provider`` 获取当前锁屏状态，状态变化时触发处理。
   默认 provider 使用 WTS API 尽力而为地检测会话状态。

检测到锁屏时调用 :meth:`StateMachine.on_system_lock`，解锁时调用
:meth:`StateMachine.on_system_unlock`（传入当前空闲秒数，由状态机判断是否
重置工作计数）。同时发布 ``SYSTEM_LOCK`` / ``SYSTEM_UNLOCK`` 事件。

仅在 Windows 平台启用；非 Windows 平台 :meth:`start` 为安全的空操作。

设计要点：

* 线程安全：所有可变状态由 ``threading.RLock`` 保护
* Qt 通知模式在 Qt 主线程分发事件；轮询模式在后台线程分发
* 核心处理方法 ``_on_lock`` / ``_on_unlock`` 与检测模式解耦，便于测试
* 与 StateMachine 解耦：状态机方法异常被捕获，不影响监听
"""

from __future__ import annotations

import sys
import threading
from typing import Any, Callable, Optional

from app.core.event_bus import EventBus, EventType, get_event_bus
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Win32 常量（仅 Windows 下有意义）
# ---------------------------------------------------------------------------
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WM_WTSSESSION_CHANGE = 0x02B1
NOTIFY_FOR_THIS_SESSION = 0x0


class SessionMonitor:
    """Windows 会话事件监控器（锁屏 / 解锁）。

    优先使用 Qt 会话通知（实时），不可用时回退到轮询模式。
    """

    # 默认轮询间隔（秒）
    DEFAULT_POLL_INTERVAL: float = 2.0

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_machine: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        session_state_provider: Optional[Callable[[], Optional[bool]]] = None,
        idle_provider: Optional[Callable[[], float]] = None,
        use_qt_notifications: bool = True,
    ) -> None:
        """初始化会话监控器。

        Args:
            event_bus: 事件总线；为 None 时使用全局单例。
            state_machine: 状态机实例，需实现 ``on_system_lock()`` /
                ``on_system_unlock(idle_seconds=...)``。可为 None。
            poll_interval: 轮询模式下的间隔（秒），最小不低于 0.5 秒。
            session_state_provider: 返回当前锁屏状态的可调用对象：
                ``True`` 表示已锁屏，``False`` 表示未锁屏，``None`` 表示未知。
                为 None 且启用 Qt 通知时，由 Qt 通知驱动；否则使用默认 WTS provider。
            idle_provider: 返回当前空闲秒数的可调用对象，解锁时传入状态机。
            use_qt_notifications: 是否优先使用 Qt 会话通知模式（默认 True）。
                若 Qt 不可用或注册失败，自动回退到轮询模式。
        """
        if poll_interval < 0.5:
            poll_interval = 0.5

        self._bus = event_bus if event_bus is not None else get_event_bus()
        self._state_machine = state_machine
        self._poll_interval = float(poll_interval)
        self._session_state_provider = session_state_provider
        self._idle_provider = idle_provider
        self._use_qt_notifications = use_qt_notifications

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Qt 通知模式相关（懒初始化）
        self._qt_widget: Any = None
        self._native_filter: Any = None
        self._using_qt: bool = False

        # 轮询模式下的上一次状态（None = 未知）
        self._last_locked: Optional[bool] = None

    # ------------------------------------------------------------------
    # 公共查询
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        """是否正在监听（Qt 通知模式或轮询模式）。"""
        with self._lock:
            if self._using_qt:
                return self._qt_widget is not None
            return self._thread is not None and self._thread.is_alive()

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @property
    def using_qt_notifications(self) -> bool:
        """当前是否使用 Qt 通知模式。"""
        with self._lock:
            return self._using_qt

    # ------------------------------------------------------------------
    # 监听控制
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动会话监听（幂等）。

        优先尝试 Qt 通知模式；失败或禁用时回退到轮询模式。
        非 Windows 平台为安全空操作。
        """
        with self._lock:
            if self.is_running():
                logger.debug("SessionMonitor 已在监听，忽略 start")
                return

            if sys.platform != "win32":
                logger.debug("非 Windows 平台，SessionMonitor 不启动监听")
                return

        # 尝试 Qt 通知模式
        if self._use_qt_notifications:
            if self._try_start_qt_notifications():
                return

        # 回退到轮询模式
        self._start_polling()

    def stop(self, timeout: float = 2.0) -> None:
        """停止会话监听（幂等）。

        Args:
            timeout: 等待轮询线程退出的最长秒数。
        """
        with self._lock:
            if self._using_qt:
                self._stop_qt_notifications()
                return

            if self._thread is None or not self._thread.is_alive():
                self._thread = None
                return

            self._stop_event.set()
            thread = self._thread

        thread.join(timeout=timeout)

        with self._lock:
            if self._thread is thread:
                if thread.is_alive():
                    logger.warning("SessionMonitor 轮询线程在 %.1fs 内未退出", timeout)
                self._thread = None
        logger.info("SessionMonitor 轮询已停止")

    # ------------------------------------------------------------------
    # Qt 通知模式
    # ------------------------------------------------------------------
    def _try_start_qt_notifications(self) -> bool:
        """尝试启动 Qt 会话通知模式，成功返回 True。"""
        try:
            from PySide6.QtCore import QAbstractNativeEventFilter, QByteArray, Qt
            from PySide6.QtWidgets import QApplication, QWidget
        except ImportError:
            logger.debug("PySide6 不可用，SessionMonitor 回退到轮询模式")
            return False

        app = QApplication.instance()
        if app is None:
            logger.debug("QApplication 不存在，SessionMonitor 回退到轮询模式")
            return False

        try:
            import ctypes
            from ctypes import wintypes

            # WTSRegisterSessionNotification 导出自 wtsapi32.dll（非 user32）
            wtsapi32 = ctypes.windll.wtsapi32
            wtsregister = wtsapi32.WTSRegisterSessionNotification
            wtsregister.argtypes = [wintypes.HWND, wintypes.DWORD]
            wtsregister.restype = wintypes.BOOL

            # 创建隐藏的消息窗口
            widget = QWidget()
            widget.setWindowTitle("EyeRest-SessionMonitor")
            widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            hwnd = int(widget.winId())

            if not wtsregister(hwnd, NOTIFY_FOR_THIS_SESSION):
                widget.deleteLater()
                logger.warning("WTSRegisterSessionNotification 注册失败，回退到轮询模式")
                return False

            # 定义原生事件过滤器
            monitor = self

            class SessionNativeFilter(QAbstractNativeEventFilter):
                def nativeEventFilter(self, eventType: QByteArray, message: int) -> int:
                    try:
                        if bytes(eventType) != b"windows_generic_MSG":
                            return 0
                        msg = ctypes.cast(
                            int(message),
                            ctypes.POINTER(wintypes.MSG),
                        ).contents
                        if msg.message == WM_WTSSESSION_CHANGE:
                            monitor._handle_session_change(msg.wParam)
                    except Exception:  # noqa: BLE001
                        logger.exception("处理 WM_WTSSESSION_CHANGE 异常")
                    return 0

            native_filter = SessionNativeFilter()
            app.installNativeEventFilter(native_filter)

            with self._lock:
                self._qt_widget = widget
                self._native_filter = native_filter
                self._using_qt = True

            logger.info("SessionMonitor Qt 会话通知已启动 (hwnd=%d)", hwnd)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("启动 Qt 会话通知失败，回退到轮询模式")
            return False

    def _stop_qt_notifications(self) -> None:
        """停止 Qt 会话通知模式。"""
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None and self._native_filter is not None:
                app.removeNativeEventFilter(self._native_filter)

            if self._qt_widget is not None:
                try:
                    import ctypes
                    from ctypes import wintypes

                    # WTSUnRegisterSessionNotification 导出自 wtsapi32.dll
                    wtsunregister = ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification
                    wtsunregister.argtypes = [wintypes.HWND]
                    wtsunregister.restype = wintypes.BOOL
                    hwnd = int(self._qt_widget.winId())
                    wtsunregister(hwnd)
                except Exception:  # noqa: BLE001
                    logger.exception("WTSUnRegisterSessionNotification 失败")
                self._qt_widget.deleteLater()
        except Exception:  # noqa: BLE001
            logger.exception("停止 Qt 会话通知异常")
        finally:
            with self._lock:
                self._qt_widget = None
                self._native_filter = None
                self._using_qt = False
            logger.info("SessionMonitor Qt 会话通知已停止")

    def _handle_session_change(self, wparam: int) -> None:
        """处理 WM_WTSSESSION_CHANGE 的 wParam。"""
        if wparam == WTS_SESSION_LOCK:
            self._on_lock()
        elif wparam == WTS_SESSION_UNLOCK:
            self._on_unlock()

    # ------------------------------------------------------------------
    # 轮询模式
    # ------------------------------------------------------------------
    def _start_polling(self) -> None:
        """启动轮询模式。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._last_locked = None
            self._thread = threading.Thread(
                target=self._poll_loop,
                name="eyerest-session-monitor",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "SessionMonitor 轮询已启动 interval=%.2fs", self._poll_interval
            )

    def _poll_loop(self) -> None:
        """轮询线程主循环：检测会话状态变化。"""
        provider = self._get_session_state_provider()
        while not self._stop_event.wait(self._poll_interval):
            try:
                is_locked = provider()
                if is_locked is None:
                    continue

                with self._lock:
                    last = self._last_locked
                    self._last_locked = is_locked

                # 首次观察（last 为 None）仅记录基线状态，不触发事件；
                # 仅在状态发生实际变化时才触发。
                if last is None:
                    continue

                if is_locked != last:
                    if is_locked:
                        self._on_lock()
                    else:
                        self._on_unlock()
            except Exception:  # noqa: BLE001
                logger.exception("SessionMonitor 轮询异常")

    def _get_session_state_provider(self) -> Callable[[], Optional[bool]]:
        """获取会话状态提供者。

        优先使用注入的 provider；否则在 Windows 上使用默认 WTS provider。
        """
        if self._session_state_provider is not None:
            return self._session_state_provider
        if sys.platform == "win32":
            return _default_session_state_provider
        return lambda: None

    # ------------------------------------------------------------------
    # 核心处理（与检测模式无关，便于测试）
    # ------------------------------------------------------------------
    def _on_lock(self) -> None:
        """处理系统锁屏。"""
        payload = {"reason": "session_lock"}
        logger.info("系统锁屏事件: %s", payload)
        self._publish(EventType.SYSTEM_LOCK, payload)
        self._notify_state_machine("on_system_lock", payload)

    def _on_unlock(self) -> None:
        """处理系统解锁。"""
        idle_seconds: Optional[float] = None
        if self._idle_provider is not None:
            try:
                idle_seconds = float(self._idle_provider())
            except Exception:  # noqa: BLE001
                logger.exception("获取空闲秒数失败，解锁时不重置计时")
                idle_seconds = None

        payload = {"idle_seconds": idle_seconds, "reason": "session_unlock"}
        logger.info("系统解锁事件: %s", payload)
        self._publish(EventType.SYSTEM_UNLOCK, payload)
        self._notify_state_machine(
            "on_system_unlock", payload, idle_seconds=idle_seconds
        )

    def _publish(self, event_type: EventType, data: Any = None) -> None:
        """发布事件（容错，不抛出异常）。"""
        if self._bus is None:
            return
        try:
            self._bus.publish(event_type, data)
        except Exception:  # noqa: BLE001
            logger.exception("发布 %s 事件失败", event_type.value)

    def _notify_state_machine(
        self, method_name: str, payload: dict, **kwargs: Any
    ) -> None:
        """调用状态机对应方法（容错）。"""
        if self._state_machine is None:
            return
        method = getattr(self._state_machine, method_name, None)
        if method is None or not callable(method):
            return
        try:
            method(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("调用 StateMachine.%s 失败", method_name)


# ---------------------------------------------------------------------------
# 默认 Windows 会话状态提供者
# ---------------------------------------------------------------------------
def _default_session_state_provider() -> Optional[bool]:
    """默认会话状态提供者（尽力而为）。

    使用 WTS API 枚举会话并判断当前控制台会话是否被锁定。
    返回 ``True``（锁定）/ ``False``（未锁定）/ ``None``（未知/失败）。

    注意：WTS 轮询无法 100% 准确区分锁定与未锁定（连接状态在锁定时仍为
    Active），因此此函数仅作为 Qt 通知模式不可用时的回退方案。生产环境
    下应优先使用 Qt 通知模式。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        # WTS 常量
        WTS_CURRENT_SERVER_HANDLE = 0
        WTS_ACTIVE = 1
        WTS_LOCKED = 8  # WTS_SESSIONSTATE_LOCKED

        class WTS_SESSION_INFO(ctypes.Structure):
            _fields_ = [
                ("SessionId", wintypes.DWORD),
                ("pWinStationName", wintypes.LPWSTR),
                ("State", ctypes.c_int),
            ]

        wtsapi = ctypes.windll.wtsapi32
        # 注意：DLL 导出表中只有带 W 后缀的 Unicode 版本
        # （WTSEnumerateSessionsW），无后缀名仅是 C 头文件中的宏。
        enumerate_sessions = wtsapi.WTSEnumerateSessionsW
        enumerate_sessions.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(WTS_SESSION_INFO)),
            ctypes.POINTER(wintypes.DWORD),
        ]
        enumerate_sessions.restype = wintypes.BOOL

        free_memory = wtsapi.WTSFreeMemory
        free_memory.argtypes = [ctypes.c_void_p]
        free_memory.restype = None

        # 获取当前控制台会话 ID
        # WTSGetActiveConsoleSessionId 导出自 kernel32.dll（非 wtsapi32）
        get_active_id = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId
        get_active_id.argtypes = []
        get_active_id.restype = wintypes.DWORD
        active_id = int(get_active_id())

        if active_id == 0xFFFFFFFF:  # INVALID_SESSION
            return None

        pp_sessions = ctypes.POINTER(WTS_SESSION_INFO)()
        count = wintypes.DWORD(0)
        if not enumerate_sessions(
            WTS_CURRENT_SERVER_HANDLE, 0, 1, ctypes.byref(pp_sessions), ctypes.byref(count)
        ):
            return None

        try:
            for i in range(count.value):
                info = pp_sessions[i]
                if info.SessionId == active_id:
                    # State == 8 (WTS_SESSIONSTATE_LOCKED) 表示锁定
                    # 注意：WTSEnumerateSessions 返回的 State 实际是
                    # WTS_CONNECTSTATE_CLASS，锁定时仍为 WTSActive(1)。
                    # 这里作为尽力而为的启发式判断。
                    return info.State == WTS_LOCKED
            return None
        finally:
            free_memory(pp_sessions)
    except Exception:  # noqa: BLE001
        logger.exception("默认会话状态提供者查询失败")
        return None
