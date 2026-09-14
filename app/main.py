"""EyeRest PySide6 应用入口。

启动方式：
    在 ``apps/EyeRest`` 目录下：
        python app/main.py
    或：
        python -m app.main
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径引导：确保无论是 ``python app/main.py`` 还是 ``python -m app.main``
# 都能正确导入 ``app.*`` 包。
# ---------------------------------------------------------------------------
if __package__ in (None, ""):
    # 直接运行脚本时，把项目根目录（app 的父目录）加入 sys.path
    _project_root = Path(__file__).resolve().parent.parent
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import defaults  # noqa: E402
from app.core.activity_monitor import ActivityMonitor  # noqa: E402
from app.core.blink_engine import BlinkEngine  # noqa: E402
from app.core.break_engine import BreakEngine  # noqa: E402
from app.core.clock import default_clock  # noqa: E402
from app.core.event_bus import EventType, get_event_bus  # noqa: E402
from app.core.move_engine import MoveEngine  # noqa: E402
from app.core.screen_session import ScreenSessionEngine  # noqa: E402
from app.core.state_machine import StateMachine  # noqa: E402
from app.core.timer_engine import TimerEngine  # noqa: E402
from app.services.break_service import BreakService  # noqa: E402
from app.services.sound_service import SoundService  # noqa: E402
from app.services.statistics_service import StatisticsService  # noqa: E402
from app.services.stats_store import get_stats_store  # noqa: E402
from app.services.usage_service import UsageService  # noqa: E402
from app.config.settings_store import get_settings_store  # noqa: E402
from app.i18n import tr  # noqa: E402
from app.ui.break_window import BreakWindow, BreakWarningPopup  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.position_editor import PositionEditor  # noqa: E402
from app.ui.tray import TrayIcon  # noqa: E402
from app.ui.visual_cue import VisualCuePopup  # noqa: E402
from app.utils.logger import get_logger, shutdown_logging  # noqa: E402
from app.windows import fullscreen_detector  # noqa: E402
from app.windows.power_monitor import PowerMonitor  # noqa: E402
from app.windows.session_monitor import SessionMonitor  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    """应用主入口。"""
    # 初始化 Qt 应用
    app = QApplication(sys.argv)
    app.setApplicationName(defaults.APP_NAME)
    app.setApplicationVersion(defaults.APP_VERSION)
    app.setOrganizationName(defaults.APP_NAME)

    # 品牌字体（圆体）须在任何窗口创建前加载
    from app.ui.theme.fonts import load_fonts

    load_fonts()

    # 关键：关闭所有窗口时不退出应用（主窗口关闭只是隐藏到托盘）
    # 只有托盘"退出"菜单才真正退出
    app.setQuitOnLastWindowClosed(False)

    # 初始化事件总线（全局单例）
    event_bus = get_event_bus()

    logger.info("=" * 60)
    logger.info("%s v%s 启动", defaults.APP_NAME, defaults.APP_VERSION)
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 配置与统计存储（须先于引擎构造，以便注入用户配置）
    #
    # V0.5 减法重构：不再使用数据库，配置落 config.json、统计落 stats.json
    # ------------------------------------------------------------------
    stats_store = get_stats_store()

    break_service = BreakService(event_bus=event_bus)
    settings = break_service.get_all_settings()
    logger.info("BreakService 已初始化（配置: %s）", get_settings_store().path)

    # ------------------------------------------------------------------
    # 提示音服务（本地合成，不抢焦点；方案清单见 sound_synth.SCHEMES）
    # ------------------------------------------------------------------
    sound = SoundService(
        enabled=settings["enable_sound"],
        scheme=settings["sound_scheme"],
        volume=settings["sound_volume"],
        intensity=settings["cue_intensity"],
    )
    sound.ensure_sounds()
    logger.info(
        "SoundService 已初始化（enabled=%s scheme=%s）",
        settings["enable_sound"],
        settings["sound_scheme"],
    )

    # 四层节奏触发次数统计（数据诚实：记录"提示"次数，不是真实眨眼次数）
    def _record(kind: str) -> None:
        try:
            stats_store.record(kind)
        except Exception:  # noqa: BLE001
            logger.exception("记录统计失败: %s", kind)

    def _blink_cues_today() -> int:
        try:
            return int(stats_store.today().get("blink_cue", 0))
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------
    # 核心组件：活动监控 → 计时引擎 → 状态机
    # ------------------------------------------------------------------
    activity_monitor = ActivityMonitor(
        idle_threshold=settings["idle_threshold"],
        natural_rest_threshold=settings["natural_rest_threshold"],
        event_bus=event_bus,
    )
    activity_monitor.start_polling()
    logger.info("ActivityMonitor 已启动")

    timer_engine = TimerEngine(activity_monitor, event_bus=event_bus)
    timer_engine.start()
    logger.info("TimerEngine 已启动")

    # ------------------------------------------------------------------
    # V1.0 核心：屏幕暴露会话（Screen Exposure Session）
    #
    # 键鼠空闲 ≠ 眼睛没在看屏幕。看 PDF / 视频 / 代码时可以几分钟不碰键鼠，
    # 但视觉负荷一点没少。因此休息与眨眼计时一律基于「屏幕暴露」，
    # GetLastInputInfo 只用于判断「是否长时间离开了电脑」。
    #
    # fullscreen_provider 提供第二重保险：检测到「全屏内容消费」
    # （看视频 / 全屏演示 / 全屏应用）时改用放宽阈值，避免把看视频
    # 误判成离开——那正是视觉负荷最高、最不该停提示的场景。
    # ------------------------------------------------------------------
    screen_session = ScreenSessionEngine(
        clock=default_clock,
        idle_provider=activity_monitor.get_idle_seconds,
        event_bus=event_bus,
        away_threshold=settings["away_threshold"],
        natural_rest_threshold=settings["natural_rest_threshold"],
        resume_threshold=settings["session_resume_threshold"],
        fullscreen_provider=fullscreen_detector.is_fullscreen_content,
    )
    logger.info(
        "ScreenSessionEngine 已初始化（V1.0 屏幕暴露模型；"
        "全屏豁免阈值 %.0fs / %.0fs）",
        defaults.FULLSCREEN_AWAY_THRESHOLD,
        defaults.FULLSCREEN_NATURAL_REST_THRESHOLD,
    )

    blink_engine = BlinkEngine(
        clock=default_clock,
        session_engine=screen_session,
        cycle_seconds=settings["blink_cycle_seconds"],
        cue_interval=settings["blink_cue_interval"],
        cue_duration=settings["blink_cue_duration"],
        enabled=settings["blink_enabled"],
        event_bus=event_bus,
    )
    logger.info(
        "BlinkEngine 已初始化（Blink Cycle %.0fs，Cue 间隔 %.0fs）",
        settings["blink_cycle_seconds"],
        settings["blink_cue_interval"],
    )

    # Move：活动提醒（45 分钟为默认建议值，不是医学硬阈值）
    move_engine = MoveEngine(
        clock=default_clock,
        session_engine=screen_session,
        interval=settings["move_interval"],
        duration=settings["move_duration"],
        enabled=settings["move_enabled"],
        event_bus=event_bus,
    )
    logger.info("MoveEngine 已初始化（间隔 %.0f 分钟）", settings["move_interval"] / 60)

    state_machine = StateMachine(
        timer_engine=timer_engine,
        event_bus=event_bus,
        activity_monitor=activity_monitor,
    )
    logger.info("StateMachine 已初始化")

    # 启动保护：INACTIVE → ACTIVE，使 BreakEngine 可正常触发警告 / 休息
    state_machine.start_protection()

    # ------------------------------------------------------------------
    # 休息引擎（BreakEngine）
    # ------------------------------------------------------------------
    break_engine = BreakEngine(
        timer_engine=timer_engine,
        state_machine=state_machine,
        event_bus=event_bus,
        config={
            "SHORT_WORK_DURATION": settings["short_work_duration"],
            "SHORT_BREAK_DURATION": settings["short_break_duration"],
            "LONG_WORK_DURATION": settings["long_work_duration"],
            "LONG_BREAK_DURATION": settings["long_break_duration"],
            "WARNING_DURATION": settings["warning_duration"],
            "POSTPONE_DURATION": settings["postpone_duration"],
            "MAX_POSTPONE": settings["max_postpone"],
        },
        fullscreen_provider=fullscreen_detector.is_fullscreen,
        defer_on_fullscreen=settings["defer_on_fullscreen"],
        # V1.0：休息时机基于屏幕暴露时间，而非键鼠活跃时间
        session_engine=screen_session,
    )
    break_engine.start()
    logger.info("BreakEngine 已启动（计时源：屏幕暴露）")

    # 使用 QTimer 在 Qt 主线程每秒驱动全部引擎（顺序很重要）：
    #   1. 推进屏幕暴露会话（ON/AWAY/OFF 三态与暴露累计）
    #   2. 眨眼提示（基于暴露时间，休息进行中不打扰）
    #   3. 休息引擎（基于暴露时间判定警告 / 短休息 / 长休息）
    # 位置编辑模式标志（自定义位置时暂停四层节奏）
    editing_position = {"active": False}

    def _on_tick() -> None:
        # 位置编辑模式下暂停四层节奏，避免拖动中的角色被下一次 Cue 覆盖
        if editing_position["active"]:
            return
        try:
            screen_session.tick()
        except Exception:  # noqa: BLE001
            logger.exception("ScreenSessionEngine tick 失败")
        # 屏幕暴露时长累计（每秒 1 秒，内部节流落盘）
        try:
            if screen_session.state is not None and screen_session.state.name == "ON":
                usage_service.record_screen_seconds(1.0)
        except Exception:  # noqa: BLE001
            logger.exception("累计屏幕暴露时长失败")
        # 休息窗口是全屏模态，期间不再插播眨眼/活动提示
        if not break_engine.is_break_in_progress():
            try:
                blink_engine.tick()
            except Exception:  # noqa: BLE001
                logger.exception("BlinkEngine tick 失败")
            try:
                move_engine.tick()
            except Exception:  # noqa: BLE001
                logger.exception("MoveEngine tick 失败")
        try:
            break_engine.tick()
        except Exception:  # noqa: BLE001
            logger.exception("BreakEngine tick 失败")

    tick_timer = QTimer(app)
    tick_timer.setInterval(1000)
    tick_timer.timeout.connect(_on_tick)
    tick_timer.start()

    # 设置热更新：保存后即时生效（无需重启）
    # 每次 SETTINGS_CHANGED 都读取权威最新设置并同步到引擎与活动监控器
    def _on_settings_changed(_data: object) -> None:
        latest = break_service.get_all_settings()
        break_engine.update_settings(latest)
        activity_monitor.update_thresholds(
            latest["idle_threshold"], latest["natural_rest_threshold"]
        )
        # V1.0：屏幕暴露阈值与眨眼节奏同步热更新
        screen_session.update_thresholds(
            away=latest["away_threshold"],
            natural_rest=latest["natural_rest_threshold"],
            resume=latest["session_resume_threshold"],
        )
        blink_engine.update_settings(
            cycle_seconds=latest["blink_cycle_seconds"],
            cue_interval=latest["blink_cue_interval"],
            cue_duration=latest["blink_cue_duration"],
            enabled=latest["blink_enabled"],
        )
        move_engine.update_settings(
            interval=latest["move_interval"],
            duration=latest["move_duration"],
            enabled=latest["move_enabled"],
        )
        visual_cue.apply_appearance(
            skin=latest["cue_skin"],
            intensity=latest["cue_intensity"],
            position_locked=latest["cue_position_locked"],
        )
        visual_cue.apply_position(
            latest["cue_position"],
            latest["cue_pos_x"],
            latest["cue_pos_y"],
            latest["cue_pos_monitor"],
        )
        sound.update_settings(
            enabled=latest["enable_sound"],
            scheme=latest["sound_scheme"],
            volume=latest["sound_volume"],
            intensity=latest["cue_intensity"],
        )
        logger.info("设置已热更新到运行中的引擎")

    event_bus.subscribe(EventType.SETTINGS_CHANGED, _on_settings_changed)
    logger.info("设置热更新订阅已建立")

    # ------------------------------------------------------------------
    # 统计服务与使用统计（V0.5：JSON 存储，无数据库）
    # ------------------------------------------------------------------
    statistics_service = StatisticsService(store=stats_store)
    logger.info("StatisticsService 已初始化")

    usage_service = UsageService(
        timer_engine=timer_engine,
        break_engine=break_engine,
        store=stats_store,
    )
    # 开始新的使用会话
    usage_service.start_session()
    logger.info("UsageService 已启动新会话")

    # ------------------------------------------------------------------
    # Windows 系统事件监听（Task 15）
    # 电源/睡眠监听 + 会话/锁屏监听，驱动状态机进入 SLEEP / LOCKED
    # ------------------------------------------------------------------
    power_monitor = PowerMonitor(
        event_bus=event_bus,
        state_machine=state_machine,
        idle_provider=activity_monitor.get_idle_seconds,
    )
    session_monitor = SessionMonitor(
        event_bus=event_bus,
        state_machine=state_machine,
        idle_provider=activity_monitor.get_idle_seconds,
    )
    power_monitor.start()
    session_monitor.start()
    logger.info("系统事件监听已启动 (PowerMonitor + SessionMonitor)")

    # V1.0：锁屏 / 休眠时屏幕不再暴露，会话必须结束；
    # 唤醒 / 解锁后重新开始一段新会话（不把锁屏时间算作用眼）。
    def _on_system_locked(_data: object) -> None:
        screen_session.on_system_lock()

    def _on_system_unlocked(_data: object) -> None:
        screen_session.on_system_unlock()

    def _on_system_sleep(_data: object) -> None:
        screen_session.on_system_sleep()

    def _on_system_wake(_data: object) -> None:
        screen_session.on_system_wake()

    for _evt, _handler in (
        (EventType.SYSTEM_LOCK, _on_system_locked),
        (EventType.SYSTEM_UNLOCK, _on_system_unlocked),
        (EventType.SYSTEM_SLEEP, _on_system_sleep),
        (EventType.SYSTEM_WAKE, _on_system_wake),
    ):
        try:
            event_bus.subscribe(_evt, _handler)
        except Exception:  # noqa: BLE001
            logger.exception("订阅系统事件失败: %s", getattr(_evt, "value", _evt))
    logger.info("屏幕暴露会话已接入锁屏/休眠信号")

    # ------------------------------------------------------------------
    # UI 组件
    # ------------------------------------------------------------------
    window = MainWindow(
        timer_engine=timer_engine,
        break_engine=break_engine,
        state_machine=state_machine,
        usage_service=usage_service,
        statistics_service=statistics_service,
        break_service=break_service,
        event_bus=event_bus,
        # V1.0：Dashboard 展示四层节奏与屏幕暴露时长
        screen_session_engine=screen_session,
        blink_engine=blink_engine,
        move_engine=move_engine,
        blink_stats_provider=_blink_cues_today,
        settings_hooks={
            "open_position_editor": lambda: _open_position_editor(),
            "apply_preset_position": lambda value: _apply_preset_position(value),
            "preview_sound": lambda scheme: sound.preview(scheme),
            "notify": lambda text: window.notify(text),
        },
    )
    # 连接 Dashboard 快捷操作按钮到引擎处理
    window.connect_dashboard_actions()
    window.show()
    logger.info("主窗口已显示")

    tray = TrayIcon(main_window=window, event_bus=event_bus)
    tray.setup_tray()
    tray.show_tray()
    logger.info("系统托盘已启动")

    # ------------------------------------------------------------------
    # 休息窗口集成（深度休息用 BreakWindow；其余走统一视觉组件）
    # ------------------------------------------------------------------
    # 复用窗口实例，避免每次触发都创建新对象
    break_window = BreakWindow()
    warning_popup = BreakWarningPopup()

    # 统一视觉提醒组件：四层节奏共用（眨眼/远眺/活动），非模态不抢焦点
    visual_cue = VisualCuePopup()
    visual_cue.apply_appearance(
        skin=settings["cue_skin"],
        intensity=settings["cue_intensity"],
        position_locked=settings["cue_position_locked"],
    )
    visual_cue.apply_position(
        settings["cue_position"],
        settings["cue_pos_x"],
        settings["cue_pos_y"],
        settings["cue_pos_monitor"],
    )

    def _on_cue_position_changed(x: int, y: int, monitor: int) -> None:
        """用户拖动提示后保存位置（下次启动恢复）。"""
        try:
            break_service.set_setting("cue_position", "custom")
            break_service.set_setting("cue_pos_x", str(x))
            break_service.set_setting("cue_pos_y", str(y))
            break_service.set_setting("cue_pos_monitor", str(monitor))
            logger.info("提示位置已保存: (%d, %d) monitor=%d", x, y, monitor)
        except Exception:  # noqa: BLE001
            logger.exception("保存提示位置失败")

    visual_cue.cue_position_changed.connect(_on_cue_position_changed)

    def _on_blink_cue(data: object) -> None:
        if not isinstance(data, dict):
            return
        # 其他提示（远眺/活动/深度）正在展示时，眨眼 Cue 让路
        if visual_cue.is_cue_visible():
            return
        _record("blink_cue")
        visual_cue.show_cue(
            kind="blink",
            with_text=bool(data.get("with_text", True)),
            duration=float(data.get("duration", defaults.BLINK_CUE_DURATION)),
        )
        # 注意：单次 Blink Cue 不出声，声音只在 Blink Cycle 结束时响一次
        # （避免一分钟叮 5 次）——见 _on_blink_cycle_finished

    event_bus.subscribe(EventType.BLINK_CUE, _on_blink_cue)

    # Blink Cycle 结束：周期级音效（每周期最多一声，避免一分钟响 5 次）
    def _on_blink_cycle_finished(_data: object) -> None:
        sound.play("blink_cycle")

    event_bus.subscribe(EventType.BLINK_CYCLE_FINISHED, _on_blink_cycle_finished)

    def _on_move_cue(data: object) -> None:
        if not isinstance(data, dict):
            return
        _record("move")
        visual_cue.show_cue(kind="move", duration=8.0)
        sound.play("move")

    event_bus.subscribe(EventType.MOVE_CUE, _on_move_cue)

    def _on_cue_acted(kind: str) -> None:
        """用户点击提示（已知晓/已完成）。"""
        if kind == "move":
            move_engine.complete_activity()
        elif kind == "look_away":
            break_engine.on_break_complete()

    def _on_cue_finished(kind: str) -> None:
        """提示自动结束。远眺 20 秒走完 = 完成一次远眺。"""
        if kind == "look_away":
            break_engine.on_break_complete()

    visual_cue.cue_acted.connect(_on_cue_acted)
    visual_cue.cue_finished.connect(_on_cue_finished)
    logger.info("统一视觉提示已接线（眨眼/远眺/活动）")
    # ------------------------------------------------------------------
    # 位置编辑模式（自定义位置：暂停全部护眼节奏，编辑完恢复）
    # ------------------------------------------------------------------
    # 编辑模式使用独立的 VisualCuePreview（普通子控件），不再复用
    # VisualCuePopup——职责分离，不存在窗口属性转换（V0.5.1 教训）。
    position_editor = PositionEditor()

    def _on_position_saved(x: int, y: int, monitor: int) -> None:
        try:
            break_service.set_setting("cue_position", "custom")
            break_service.set_setting("cue_pos_x", str(x))
            break_service.set_setting("cue_pos_y", str(y))
            break_service.set_setting("cue_pos_monitor", str(monitor))
            latest = break_service.get_all_settings()
            visual_cue.apply_position(
                "custom", latest["cue_pos_x"], latest["cue_pos_y"], latest["cue_pos_monitor"]
            )
            logger.info("提示位置已保存: (%d, %d) monitor=%d", x, y, monitor)
            window.notify(tr("position.saved_feedback"))
        except Exception:  # noqa: BLE001
            logger.exception("保存提示位置失败")

    def _on_editor_closed() -> None:
        editing_position["active"] = False
        logger.info("位置编辑结束，护眼节奏已恢复")

    position_editor.position_saved.connect(_on_position_saved)
    position_editor.editor_closed.connect(_on_editor_closed)

    def _open_position_editor() -> None:
        """打开位置编辑器：先暂停护眼节奏，避免编辑过程中被 Cue 覆盖。"""
        editing_position["active"] = True
        visual_cue.hide_cue()
        latest = break_service.get_all_settings()
        position_editor.open_editor(
            skin=str(latest.get("cue_skin") or "minimal"),
            intensity=str(latest.get("cue_intensity") or "standard"),
            position_mode=str(latest.get("cue_position") or "default"),
            pos_x=latest.get("cue_pos_x"),
            pos_y=latest.get("cue_pos_y"),
            monitor=int(latest.get("cue_pos_monitor") or 0),
        )

    def _apply_preset_position(value: str) -> None:
        """预设位置：立即生效（设置页已写入配置，这里同步到提示组件）。"""
        latest = break_service.get_all_settings()
        visual_cue.apply_position(
            value,
            latest["cue_pos_x"],
            latest["cue_pos_y"],
            latest["cue_pos_monitor"],
        )
        logger.info("提示位置已切换为预设: %s", value)

    # 跟踪延迟次数（由 BreakEngine 维护，UI 仅显示）
    current_postpone_count: list[int] = [0]

    # 连接休息窗口信号到 BreakEngine
    def _on_break_completed(break_type: str) -> None:
        logger.info("UI 报告休息完成: type=%s", break_type)
        break_engine.on_break_complete()

    def _on_break_skipped(break_type: str) -> None:
        logger.info("UI 报告休息跳过: type=%s", break_type)
        if break_type == "long":
            _record("skipped_break")
        break_engine.on_break_skip()

    def _on_break_postponed() -> None:
        logger.info("UI 报告休息延迟")
        current_postpone_count[0] += 1
        break_engine.on_postpone()

    break_window.break_completed.connect(_on_break_completed)
    break_window.break_skipped.connect(_on_break_skipped)
    break_window.break_postponed.connect(_on_break_postponed)

    # 连接警告弹窗信号到 BreakEngine
    def _on_warning_accepted() -> None:
        logger.info("UI 报告立即休息")
        break_engine.on_break_start()

    def _on_warning_postponed() -> None:
        logger.info("UI 报告警告阶段延迟")
        current_postpone_count[0] += 1
        break_engine.on_postpone()

    warning_popup.warning_accepted.connect(_on_warning_accepted)
    warning_popup.warning_postponed.connect(_on_warning_postponed)

    # 订阅休息触发事件：远眺 → 视觉提示；深度休息 → 全屏休息窗口
    def _on_break_triggered(data: object) -> None:
        if not isinstance(data, dict):
            return
        break_type = str(data.get("break_type", "short"))
        duration = int(data.get("duration", 0)) or None
        current_postpone_count[0] = 0
        # 分层开关：远眺 / 深度休息可单独关闭（眨眼与活动由各自引擎控制）
        if break_type == "long":
            if not break_service.get_setting("deep_break_enabled", True):
                return
        elif not break_service.get_setting("look_away_enabled", True):
            return
        if break_type == "long":
            # 深度休息：全屏休息窗口（离开屏幕休息一下）
            _record("deep_break")
            visual_cue.hide_cue()
            break_window.configure(break_type, duration)
            break_window.set_postpone_count(current_postpone_count[0])
            break_window.show_break()
            sound.play("deep_break")
        else:
            # 远眺：非模态视觉提示 20 秒（不抢焦点，走完即完成）
            _record("look_away")
            seconds = duration or defaults.SHORT_BREAK_DURATION
            visual_cue.show_cue(
                kind="look_away",
                duration=float(seconds),
                countdown_seconds=seconds,
            )
            sound.play("look_away")

    # 订阅休息警告事件：仅深度休息保留 30 秒预告弹窗
    def _on_break_warning(data: object) -> None:
        if not isinstance(data, dict):
            return
        break_type = str(data.get("break_type", "short"))
        if break_type != "long":
            return  # 远眺不做预告，到点直接给 20 秒视觉提示
        warning_popup.configure(break_type)
        warning_popup.show_warning(postpone_count=current_postpone_count[0])

    event_bus.subscribe(EventType.BREAK_TRIGGERED, _on_break_triggered)
    event_bus.subscribe(EventType.BREAK_WARNING, _on_break_warning)
    logger.info("休息窗口事件订阅已建立")

    # 连接托盘退出信号：先强制关闭主窗口，再退出应用
    def _on_quit_requested() -> None:
        logger.info("收到退出请求，正在退出应用...")
        # 结束当前使用会话，保存统计
        try:
            usage_service.end_session()
            stats_store.flush()
            logger.info("使用会话已结束，统计已落盘")
        except Exception:  # noqa: BLE001
            logger.exception("结束使用会话失败")
        # 停止休息引擎与定时器
        tick_timer.stop()
        break_engine.stop()
        blink_engine.reset()
        # 停止系统事件监听
        power_monitor.stop()
        session_monitor.stop()
        # 停止核心引擎
        timer_engine.stop()
        activity_monitor.stop_polling()
        # 关闭 UI
        visual_cue.hide_cue()
        break_window.hide_break()
        warning_popup.hide_warning()
        window.force_close()
        tray.cleanup()
        app.quit()

    tray.exit_requested.connect(_on_quit_requested)

    # 连接设置请求信号（可选，主窗口已有 show_settings 方法时直接调用）
    def _on_settings_requested() -> None:
        if hasattr(window, "show_settings"):
            window.show_settings()

    tray.settings_requested.connect(_on_settings_requested)

    # 进入 Qt 事件循环
    exit_code = app.exec()

    logger.info("%s 退出 (code=%d)", defaults.APP_NAME, exit_code)
    shutdown_logging()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
