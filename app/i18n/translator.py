"""i18n 翻译器：轻量级双语（zh / en）字符串翻译与语言切换通知。

设计要点：

* ``_STRINGS`` 保存 zh / en 两个语言字典，键集合必须完全一致；
* :class:`Translator` 维护当前语言与监听器列表，语言切换时同步通知
  所有已注册的回调（UI 组件在回调中执行 ``retranslate_ui``）；
* ``tr(key, **kwargs)`` 支持 ``{placeholder}`` 格式化参数，缺失键返回
  key 本身并记录 warning 日志，保证界面永不因缺翻译而崩溃。
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 支持的语言代码
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "en")

# ---------------------------------------------------------------------------
# 双语字典（zh 与 en 的键集合必须完全一致，tests/test_i18n.py 会校验）
# ---------------------------------------------------------------------------
_STRINGS: dict[str, dict[str, str]] = {
    "zh": {
        # ---- 导航 / 通用 ----
        "nav.dashboard": "首页",
        "nav.statistics": "统计",
        "nav.settings": "设置",
        "nav.about": "关于",
        "common.rest_now": "立即休息",
        "common.pause_30m": "暂停 30 分钟",
        "common.postpone": "延迟",
        "common.skip": "跳过",
        "common.postpone_minutes": "延迟 {minutes} 分钟",
        "common.unit_minutes": " 分钟",
        "common.unit_seconds": " 秒",
        "common.unit_times": " 次",
        # ---- 保护状态 ----
        "state.active": "正在保护",
        "state.idle": "空闲中",
        "state.break_warning": "即将休息",
        "state.short_break": "🌙 休息中",
        "state.long_break": "🌙 长休息中",
        "state.paused": "⏸ 已暂停",
        "state.locked": "🔒 已锁屏",
        "state.sleep": "💤 睡眠中",
        "state.inactive": "○ 未开始保护",
        # ---- 主窗口 ----
        "main.ready": "就绪",
        "main.started": "EyeRest 已启动",
        "main.state_format": "当前状态: {state}",
        "main.break_triggered": "已触发立即休息",
        "main.paused_30m": "已暂停 30 分钟",
        "main.timer_reset": "计时已重置",
        "main.state_active": "护眼节奏运行中",
        "sidebar.tagline": "更优雅的护眼体验·让专注更持久",
        "about.tagline": "更优雅的护眼体验·让专注更持久",
        "about.version": "版本",
        "about.principle": "设计理念",
        "about.principle_value": "提醒而非控制——以四层节奏守护你的眼睛",
        "about.persisted": "数据存储",
        "about.persisted_value": "本地 JSON 存储（与可执行文件同级的 data/ 目录）",
        # ---- Dashboard ----
        "dashboard.active_time_title": "今日有效用眼时间",
        "dashboard.uptime_format": "应用运行时间 {time}",
        "dashboard.next_break_title": "距离下次休息",
        "dashboard.break_in_progress": "休息进行中",
        "dashboard.breaking": "休息中",
        "dashboard.break_upcoming": "即将休息",
        "dashboard.stat_completed": "已休息",
        "dashboard.stat_skipped": "已跳过",
        "dashboard.stat_natural": "自然休息",
        "dashboard.reset_timer": "重置计时",
        # ---- V1.0 三层护眼节奏 ----
        "dashboard.rhythm_title": "护眼节奏",
        "dashboard.rhythm_blink": "眨眼",
        "dashboard.rhythm_short": "远眺",
        "dashboard.rhythm_move": "活动",
        "dashboard.rhythm_long": "深度休息",
        "dashboard.stat_blink_cues": "眨眼提示",
        "dashboard.stat_active": "专注时长",
        "dashboard.status_title": "当前状态",
        "dashboard.status_next_label": "下一次提醒",
        "dashboard.quick_settings_title": "快捷设置",
        "dashboard.quick_position": "提示位置",
        "dashboard.quick_intensity": "强度",
        "dashboard.quick_sound": "提示音",
        "dashboard.quick_more": "更多",
        "dashboard.hero_greeting": "你好，",
        "dashboard.hero_wish": "愿你的眼睛一直明亮",
        "dashboard.hero_tip": "小小的提醒，给眼睛多一点温柔",
        "dashboard.quote_line1": "适当的停顿",
        "dashboard.quote_line2": "是为了更好的前行",
        "dashboard.today_title": "今日数据",
        "dashboard.rhythm_paused": "已暂停",
        "dashboard.rhythm_away": "离开中",
        "dashboard.rhythm_off": "未在用眼",
        "dashboard.exposure_time_title": "当前屏幕暴露时长",
        # ---- 统计页 ----
        "stats.title": "📊 统计",
        "stats.refresh": "🔄 刷新",
        "stats.refresh_tooltip": "刷新统计数据",
        "stats.refreshed": "🔄 已刷新",
        "stats.refresh_failed": "❌ 刷新失败",
        "stats.usage_trend_title": "用眼趋势（最近 7 天）",
        "stats.break_trend_title": "休息趋势（最近 7 天）",
        "stats.details_title": "详细数据（最近 7 天）",
        "stats.series": "系列{i}",
        "stats.legend_active": "有效用眼",
        "stats.legend_idle": "空闲",
        "stats.legend_short": "短休息",
        "stats.legend_long": "长休息",
        "stats.legend_skipped": "跳过",
        "stats.card_active": "今日有效用眼",
        "stats.card_breaks": "今日休息次数",
        "stats.card_skipped": "今日跳过次数",
        "stats.card_avg_work": "平均工作时长",
        "stats.col_date": "日期",
        "stats.col_active": "有效用眼",
        "stats.col_idle": "空闲",
        "stats.col_short": "短休息",
        "stats.col_long": "长休息",
        "stats.col_skipped": "跳过",
        # ---- 设置页 ----
        "settings.title": "设置",
        "settings.subtitle": "配置休息计划、空闲检测与通知行为",
        "settings.save": "保存设置",
        "settings.reset": "恢复默认",
        "settings.group_general": "通用",
        "settings.language": "界面语言",
        "settings.group_break": "休息时长设置",
        "settings.group_idle": "空闲检测设置",
        "settings.group_postpone": "延迟设置",
        "settings.group_notification": "通知设置",
        "settings.group_behavior": "行为设置",
        "settings.short_work_duration": "短工作时长",
        "settings.short_break_duration": "短休息时长",
        "settings.long_work_duration": "深度休息周期",
        "settings.long_break_duration": "深度休息时长",
        # --- V1.0 正式版：Blink Cycle / Move / 视觉 ---
        "settings.group_blink": "👁 眨眼节奏",
        # --- V0.5 设置页重做：分组 / 反馈 / 声音 / 位置编辑 ---
        "settings.group_look_away": "🌿 远眺提醒",
        "settings.look_away_enabled": "启用远眺提醒",
        "settings.look_away_interval": "间隔",
        "settings.look_away_duration": "远眺时间",
        "settings.group_deep_break": "🧘 长休息",
        "settings.deep_break_enabled": "启用长休息提醒",
        "settings.deep_break_interval": "间隔",
        "settings.deep_break_duration": "休息时间",
        "settings.group_system": "⚙ 系统",
        "settings.group_language": "🌐 语言",
        "settings.group_advanced": "高级（默认隐藏）",
        # --- V0.5.1 声音 UX：分组拆分 / 试听 / 触发说明 ---
        "settings.group_sound": "🔊 声音",
        "settings.preview": "试听",
        "settings.sound_hint": "提示音只在重要提醒触发时播放一次；眨眼提示每个周期最多响一次。",
        "settings.intensity_hint": "安静：轻微动画，无声音 ｜ 标准：正常动画 + 文字 + 柔和声音 ｜ 明显：更明显的动画与声音",
        "settings.sound_scheme": "音效方案",
        "settings.sound_breath": "呼吸（默认）",
        "settings.sound_wood": "木质轻响",
        "settings.sound_glass": "玻璃轻音",
        "settings.sound_nature": "自然单音",
        "settings.sound_bell": "柔和铃音",
        "settings.sound_volume": "音量",
        "settings.saved_feedback": "✓ 设置已保存",
        "settings.no_changes": "没有需要保存的改动",
        "settings.reset_confirm_title": "恢复默认设置",
        "settings.reset_confirm_body": "恢复全部设置为默认值？",
        "settings.reset_feedback": "✓ 已恢复默认设置",
        "position.title": "调整提示位置",
        "position.hint": "拖动提示到你习惯的位置 · 保存后将恢复护眼提醒（Esc 取消）",
        "position.save": "保存位置",
        "position.cancel": "取消",
        "position.saved_feedback": "✓ 提示位置已保存",
        "settings.blink_enabled": "启用眨眼提示",
        "settings.blink_cycle_seconds": "眨眼周期",
        "settings.blink_cue_interval": "提示间隔",
        "settings.group_move": "🚶 活动提醒",
        "settings.move_enabled": "启用活动提醒",
        "settings.move_interval": "提醒间隔",
        "settings.move_duration": "建议活动时长",
        "settings.group_visual": "🎨 视觉提醒",
        "settings.cue_skin": "视觉形象",
        "settings.skin_minimal": "极简眼睛",
        "settings.skin_cartoon": "卡通双眼",
        "settings.skin_character": "小眼睛角色",
        "settings.cue_intensity": "提醒强度",
        "settings.intensity_quiet": "安静",
        "settings.intensity_standard": "标准",
        "settings.intensity_prominent": "明显",
        "settings.cue_position": "提示位置",
        "settings.position_default": "默认位置（底部中央）",
        "settings.position_bottom_left": "左下",
        "settings.position_bottom_right": "右下",
        "settings.position_top_left": "左上",
        "settings.position_top_right": "右上",
        "settings.position_custom": "自定义（拖动提示）",
        "settings.warning_duration": "提前警告时长",
        "settings.idle_threshold": "空闲阈值",
        "settings.natural_rest_threshold": "自然休息阈值",
        "settings.postpone_duration": "延迟时长",
        "settings.max_postpone": "最大延迟次数",
        "settings.enable_sound": "启用提示音",
        "settings.enable_notification": "启用系统通知",
        "settings.auto_start": "开机自启",
        "settings.minimize_to_tray": "最小化到托盘",
        "settings.defer_on_fullscreen": "全屏时延迟提醒",
        # ---- 托盘 ----
        "tray.effective_usage": "有效使用：{time}",
        "tray.next_break": "下一次休息：{time}",
        "tray.pause_today": "暂停今天",
        "tray.resume": "恢复保护",
        "tray.open_app": "打开 EyeRest",
        "tray.quit": "退出",
        "tray.paused_30m": "⏸ 已暂停（30分钟）",
        "tray.paused_today": "⏸ 已暂停（今天）",
        "tray.pause_2h": "⏸ 暂停 2 小时",
        "tray.paused_2h": "⏸ 已暂停（2 小时）",
        "tray.game_mode": "🎮 游戏 / 会议模式",
        "tray.game_mode_active": "🎮 游戏 / 会议模式（已暂停）",
        # ---- 休息窗口 / 警告弹窗 ----
        "break.title_short": "该休息一下了",
        "break.title_long": "长时间用眼休息",
        "break.subtitle_short": "放松 20 秒，看看远处",
        "break.subtitle_long": "起来活动 5 分钟",
        "break.postpone_hint": "还可延迟 {count} 次",
        "break.postpone_max": "已达最大延迟次数",
        "break.warning_title": "⏰ 即将休息",
        "break.warning_subtitle_short": "20 秒短休息即将开始",
        "break.warning_subtitle_long": "5 分钟长休息即将开始",
        "break.warning_countdown": "{seconds} 秒后开始",
        "break.warning_starting": "即将开始...",
        # ---- 眨眼提示（V1.0 第一层护眼节奏）----
        "blink.cue_hint": "眨眨眼，润一下",
        "blink.cue_hint_strong": "眼睛累了，做几次完整眨眼",
        # ---- 四层节奏 Cue（V1.0 正式版：四种条件反射）----
        "cue.blink": "眨眨眼",
        "cue.look_away": "看远处 20 秒",
        "cue.move": "起来动一动",
        "cue.deep": "离开屏幕休息一下",
        "cue.move_hint": "建议活动 {minutes} 分钟",
        # ---- 时长格式化（BreakService.format_duration_label）----
        "duration.seconds": "{seconds} 秒",
        "duration.minutes": "{minutes} 分钟",
        "duration.minutes_seconds": "{minutes} 分 {seconds} 秒",
        "duration.hours": "{hours} 小时",
        "duration.hours_minutes": "{hours} 小时 {minutes} 分钟",
    },
    "en": {
        # ---- Navigation / common ----
        "nav.dashboard": "Home",
        "nav.statistics": "Statistics",
        "nav.settings": "Settings",
        "nav.about": "About",
        "common.rest_now": "Rest Now",
        "common.pause_30m": "Pause 30 min",
        "common.postpone": "Postpone",
        "common.skip": "Skip",
        "common.postpone_minutes": "Postpone {minutes} min",
        "common.unit_minutes": " min",
        "common.unit_seconds": " s",
        "common.unit_times": " times",
        # ---- Protection states ----
        "state.active": "Protecting",
        "state.idle": "Idle",
        "state.break_warning": "Break Soon",
        "state.short_break": "🌙 On Break",
        "state.long_break": "🌙 Long Break",
        "state.paused": "⏸ Paused",
        "state.locked": "🔒 Locked",
        "state.sleep": "💤 Sleeping",
        "state.inactive": "○ Not Protecting",
        # ---- Main window ----
        "main.ready": "Ready",
        "main.started": "EyeRest started",
        "main.state_format": "State: {state}",
        "main.break_triggered": "Break triggered",
        "main.paused_30m": "Paused for 30 min",
        "main.timer_reset": "Timer reset",
        "main.state_active": "Protection active",
        "sidebar.tagline": "A calmer way to protect your eyes and your focus",
        "about.tagline": "A calmer way to protect your eyes and your focus",
        "about.version": "Version",
        "about.principle": "Design principle",
        "about.principle_value": "Reminder, not controller — four layers of rhythm protect your eyes",
        "about.persisted": "Data storage",
        "about.persisted_value": "Local JSON (data/ folder next to the executable)",
        "main.dashboard_placeholder": "Dashboard not initialized",
        "main.stats_placeholder": "Statistics page (not initialized)",
        "main.settings_placeholder": "Settings page (not initialized)",
        # ---- Dashboard ----
        "dashboard.active_time_title": "Today's Effective Eye-Use Time",
        "dashboard.uptime_format": "Uptime {time}",
        "dashboard.next_break_title": "Next Break In",
        "dashboard.break_in_progress": "Break In Progress",
        "dashboard.breaking": "On Break",
        "dashboard.break_upcoming": "Break Soon",
        "dashboard.stat_completed": "Completed",
        "dashboard.stat_skipped": "Skipped",
        "dashboard.stat_natural": "Natural Rest",
        "dashboard.reset_timer": "Reset Timer",
        # ---- V1.0 three-tier rhythm ----
        "dashboard.rhythm_title": "Eye-Care Rhythm",
        "dashboard.rhythm_blink": "Blink",
        "dashboard.rhythm_short": "Look Away",
        "dashboard.rhythm_move": "Move",
        "dashboard.rhythm_long": "Deep Break",
        "dashboard.stat_blink_cues": "Blink Cues",
        "dashboard.rhythm_paused": "Paused",
        "dashboard.rhythm_away": "Away",
        "dashboard.rhythm_off": "Not exposed",
        "dashboard.hero_greeting": "Hello,",
        "dashboard.hero_wish": "May your eyes stay bright",
        "dashboard.hero_tip": "A gentle reminder for your eyes",
        "dashboard.quote_line1": "A proper pause",
        "dashboard.quote_line2": "is for a better journey ahead",
        "dashboard.exposure_time_title": "Current Screen Exposure",
        # ---- Statistics page ----
        "stats.title": "📊 Statistics",
        "stats.refresh": "🔄 Refresh",
        "stats.refresh_tooltip": "Refresh statistics",
        "stats.refreshed": "🔄 Refreshed",
        "stats.refresh_failed": "❌ Refresh failed",
        "stats.usage_trend_title": "Eye-Use Trend (Last 7 Days)",
        "stats.break_trend_title": "Break Trend (Last 7 Days)",
        "stats.details_title": "Details (Last 7 Days)",
        "stats.series": "Series {i}",
        "stats.legend_active": "Active",
        "stats.legend_idle": "Idle",
        "stats.legend_short": "Short Breaks",
        "stats.legend_long": "Long Breaks",
        "stats.legend_skipped": "Skipped",
        "stats.card_active": "Today's Eye-Use",
        "stats.card_breaks": "Breaks Today",
        "stats.card_skipped": "Skipped Today",
        "stats.card_avg_work": "Avg Work Time",
        "stats.col_date": "Date",
        "stats.col_active": "Active",
        "stats.col_idle": "Idle",
        "stats.col_short": "Short",
        "stats.col_long": "Long",
        "stats.col_skipped": "Skipped",
        # ---- Settings page ----
        "settings.title": "Settings",
        "settings.subtitle": "Configure break schedule, idle detection and notifications",
        "settings.save": "Save",
        "settings.reset": "Restore Defaults",
        "settings.group_general": "General",
        "settings.language": "Language",
        "settings.group_break": "Break Durations",
        "settings.group_idle": "Idle Detection",
        "settings.group_postpone": "Postpone",
        "settings.group_notification": "Notifications",
        "settings.group_behavior": "Behavior",
        "settings.short_work_duration": "Short work duration",
        "settings.short_break_duration": "Short break duration",
        "settings.long_work_duration": "Deep break cycle",
        "settings.long_break_duration": "Deep break duration",
        # --- V1.0 final: Blink Cycle / Move / Visual ---
        "settings.group_blink": "👁 Blink Rhythm",
        # --- V0.5 settings rework: groups / feedback / sound / position editor ---
        "settings.group_look_away": "🌿 Look Away",
        "settings.look_away_enabled": "Enable look-away cues",
        "settings.look_away_interval": "Interval",
        "settings.look_away_duration": "Duration",
        "settings.group_deep_break": "🧘 Deep Break",
        "settings.deep_break_enabled": "Enable deep break",
        "settings.deep_break_interval": "Interval",
        "settings.deep_break_duration": "Break length",
        "settings.group_system": "⚙ System",
        "settings.group_language": "🌐 Language",
        "settings.group_advanced": "Advanced (collapsed)",
        # --- V0.5.1 sound UX: split group / preview / trigger hint ---
        "settings.group_sound": "🔊 Sound",
        "settings.preview": "Preview",
        "settings.sound_hint": "Sounds play once when an important reminder fires; the blink cue plays at most once per cycle.",
        "settings.intensity_hint": "Quiet: subtle animation, no sound ｜ Standard: normal animation + text + soft sound ｜ Prominent: more visible animation and sound",
        "settings.sound_scheme": "Sound scheme",
        "settings.sound_breath": "Breath (default)",
        "settings.sound_wood": "Wood tap",
        "settings.sound_glass": "Soft glass",
        "settings.sound_nature": "Nature chirp",
        "settings.sound_bell": "Soft bell",
        "settings.sound_volume": "Volume",
        "settings.saved_feedback": "✓ Settings saved",
        "settings.no_changes": "Nothing to save",
        "settings.reset_confirm_title": "Restore defaults",
        "settings.reset_confirm_body": "Restore all settings to defaults?",
        "settings.reset_feedback": "✓ Defaults restored",
        "position.title": "Adjust cue position",
        "position.hint": "Drag the cue where you like it · Saving resumes the rhythm (Esc to cancel)",
        "position.save": "Save position",
        "position.cancel": "Cancel",
        "position.saved_feedback": "✓ Position saved",
        "settings.blink_enabled": "Enable blink cues",
        "settings.blink_cycle_seconds": "Blink cycle",
        "settings.blink_cue_interval": "Cue interval",
        "settings.group_move": "🚶 Move Reminder",
        "settings.move_enabled": "Enable move reminders",
        "settings.move_interval": "Reminder interval",
        "settings.move_duration": "Suggested activity",
        "settings.group_visual": "🎨 Visual Cue",
        "settings.cue_skin": "Visual skin",
        "settings.skin_minimal": "Minimal Eye",
        "settings.skin_cartoon": "Cartoon Eyes",
        "settings.skin_character": "Eye Character",
        "settings.cue_intensity": "Intensity",
        "settings.intensity_quiet": "Quiet",
        "settings.intensity_standard": "Standard",
        "settings.intensity_prominent": "Prominent",
        "settings.cue_position": "Cue position",
        "settings.position_default": "Default (bottom center)",
        "settings.position_bottom_left": "Bottom left",
        "settings.position_bottom_right": "Bottom right",
        "settings.position_top_left": "Top left",
        "settings.position_top_right": "Top right",
        "settings.position_custom": "Custom (drag the cue)",
        "settings.warning_duration": "Pre-break warning",
        "settings.idle_threshold": "Idle threshold",
        "settings.natural_rest_threshold": "Natural rest threshold",
        "settings.postpone_duration": "Postpone duration",
        "settings.max_postpone": "Max postpones",
        "settings.enable_sound": "Play sound alert",
        "settings.enable_notification": "Show system notifications",
        "settings.auto_start": "Launch at startup",
        "settings.minimize_to_tray": "Minimize to tray",
        "settings.defer_on_fullscreen": "Defer alerts during fullscreen",
        # ---- Tray ----
        "tray.effective_usage": "Active: {time}",
        "tray.next_break": "Next break: {time}",
        "tray.pause_today": "Pause for Today",
        "tray.resume": "Resume Protection",
        "tray.open_app": "Open EyeRest",
        "tray.quit": "Exit",
        "tray.paused_30m": "⏸ Paused (30 min)",
        "tray.paused_today": "⏸ Paused (today)",
        "tray.pause_2h": "⏸ Pause 2 Hours",
        "tray.paused_2h": "⏸ Paused (2 hours)",
        "tray.game_mode": "🎮 Game / Meeting Mode",
        "tray.game_mode_active": "🎮 Game / Meeting Mode (paused)",
        # ---- Break window / warning popup ----
        "break.title_short": "Time for a Break",
        "break.title_long": "Long Eye-Use Break",
        "break.subtitle_short": "Relax for 20 seconds, look at something far away",
        "break.subtitle_long": "Get up and move for 5 minutes",
        "break.postpone_hint": "{count} postpones left",
        "break.postpone_max": "Max postpones reached",
        "break.warning_title": "⏰ Break Coming Up",
        "break.warning_subtitle_short": "A 20-second break starts soon",
        "break.warning_subtitle_long": "A 5-minute break starts soon",
        "break.warning_countdown": "Starting in {seconds}s",
        "break.warning_starting": "Starting...",
        # ---- Blink cue (V1.0 tier-1 rhythm) ----
        "blink.cue_hint": "Blink a few times",
        "blink.cue_hint_strong": "Eyes are tired — blink fully",
        # ---- Four rhythm cues (V1.0 final) ----
        "cue.blink": "Blink a few times",
        "cue.look_away": "Look 20 feet away for 20s",
        "cue.move": "Get up and move",
        "cue.deep": "Step away from the screen",
        "cue.move_hint": "Suggested: {minutes} min of activity",
        # ---- Duration formatting (BreakService.format_duration_label) ----
        "duration.seconds": "{seconds}s",
        "duration.minutes": "{minutes} min",
        "duration.minutes_seconds": "{minutes} min {seconds}s",
        "duration.hours": "{hours} hr",
        "duration.hours_minutes": "{hours} hr {minutes} min",
    },
}

#: 语言变化监听器类型：``callback(new_language)``
LanguageListener = Callable[[str], None]


class Translator:
    """翻译器：管理当前语言、键值翻译与语言变化监听器。"""

    def __init__(self) -> None:
        self._language: str = "zh"
        self._listeners: list[LanguageListener] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 语言管理
    # ------------------------------------------------------------------
    def get_language(self) -> str:
        """返回当前语言代码（``"zh"`` / ``"en"``）。"""
        return self._language

    def set_language(self, lang: str) -> None:
        """设置语言，语言变化时通知所有监听器。

        Args:
            lang: 语言代码 ``"zh"`` / ``"en"``。不支持的代码会被忽略并
                记录 warning；语言未变化时不触发通知。
        """
        if lang not in _STRINGS:
            logger.warning("不支持的语言代码: %r，已忽略", lang)
            return
        with self._lock:
            if lang == self._language:
                return
            self._language = lang
            listeners = list(self._listeners)
        logger.debug("界面语言切换: %s", lang)
        for callback in listeners:
            try:
                callback(lang)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "语言变化监听器执行失败: %s",
                    getattr(callback, "__name__", repr(callback)),
                )

    # ------------------------------------------------------------------
    # 翻译
    # ------------------------------------------------------------------
    def tr(self, key: str, **kwargs: Any) -> str:
        """翻译 key，支持 ``{placeholder}`` 格式化参数。

        Args:
            key: 点分命名的翻译键（如 ``"nav.dashboard"``）。
            **kwargs: 格式化参数（如 ``minutes=5``）。

        Returns:
            翻译后的文本；键缺失时返回 key 本身并记录 warning；
            格式化参数不匹配时返回未格式化的原文。
        """
        table = _STRINGS.get(self._language, _STRINGS["zh"])
        text = table.get(key)
        if text is None:
            logger.warning("i18n 缺失翻译键: %s (language=%s)", key, self._language)
            return key
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError):
                logger.warning(
                    "i18n 格式化参数不匹配: key=%s kwargs=%s", key, kwargs
                )
                return text
        return text

    # ------------------------------------------------------------------
    # 监听器管理
    # ------------------------------------------------------------------
    def add_listener(self, callback: LanguageListener) -> None:
        """注册语言变化监听器（重复注册会被忽略）。"""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: LanguageListener) -> None:
        """移除语言变化监听器（未注册时安全无操作）。"""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)


# ---------------------------------------------------------------------------
# 全局单例与模块级快捷函数
# ---------------------------------------------------------------------------
_translator = Translator()


def tr(key: str, **kwargs: Any) -> str:
    """模块级快捷翻译函数。"""
    return _translator.tr(key, **kwargs)


def get_translator() -> Translator:
    """返回全局 Translator 单例。"""
    return _translator


def set_language(lang: str) -> None:
    """设置全局界面语言。"""
    _translator.set_language(lang)


def get_language() -> str:
    """返回全局界面语言代码。"""
    return _translator.get_language()
