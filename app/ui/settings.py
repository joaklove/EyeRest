"""Settings 设置页（V0.5 减法重构：一屏搞定，反馈明确）。

设计要点：

* **紧凑双栏**：六个分组（眨眼 / 远眺 / 活动 / 长休息 / 视觉与声音 / 系统）
  排成两列，基本不需要滚动；不常用的高级项收进可折叠分组
* **防误触**：所有时间控件使用 :class:`NoScrollSpinBox`——鼠标滚轮**不会**
  改变数值，并显示 ``[-] [+]`` 按钮
* **保存反馈**：无改动时「保存设置」禁用；有改动才启用；保存后显示
  ``✓ 设置已保存`` 并重新禁用
* **恢复默认**：弹出确认框，确认后立即生效并持久化，显示 ``✓`` 反馈
* **位置设置**：预设位置点击即生效；选择「自定义」会打开位置编辑器，
  期间暂停所有护眼节奏

除语言外的改动都需点击「保存设置」才持久化；语言切换即时生效。
全部文案通过 :func:`app.i18n.tr` 翻译，语言切换时即时刷新。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.i18n import get_language, get_translator, set_language, tr
from app.services.break_service import BreakService
from app.utils.logger import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# 字段配置
# ---------------------------------------------------------------------------
# (key, 标签 i18n 键, 最小值, 最大值, 单位 i18n 键, 秒→UI 换算倍数)
_SPINBOX_FIELDS: list[tuple[str, str, int, int, str, int]] = [
    # 眨眼
    ("blink_cycle_seconds", "settings.blink_cycle_seconds", 30, 180, "common.unit_seconds", 1),
    ("blink_cue_interval", "settings.blink_cue_interval", 5, 30, "common.unit_seconds", 1),
    # 远眺
    ("short_work_duration", "settings.look_away_interval", 5, 60, "common.unit_minutes", 60),
    ("short_break_duration", "settings.look_away_duration", 5, 60, "common.unit_seconds", 1),
    # 活动
    ("move_interval", "settings.move_interval", 20, 120, "common.unit_minutes", 60),
    ("move_duration", "settings.move_duration", 1, 10, "common.unit_minutes", 60),
    # 长休息
    ("long_work_duration", "settings.deep_break_interval", 30, 180, "common.unit_minutes", 60),
    ("long_break_duration", "settings.deep_break_duration", 1, 30, "common.unit_minutes", 60),
    ("postpone_duration", "settings.postpone_duration", 1, 30, "common.unit_minutes", 60),
    ("max_postpone", "settings.max_postpone", 0, 5, "common.unit_times", 1),
    # 高级（默认折叠）
    ("warning_duration", "settings.warning_duration", 0, 60, "common.unit_seconds", 1),
    ("idle_threshold", "settings.idle_threshold", 30, 300, "common.unit_seconds", 1),
    ("natural_rest_threshold", "settings.natural_rest_threshold", 60, 600, "common.unit_seconds", 1),
]

# (key, 标签 i18n 键)
_CHECKBOX_FIELDS: list[tuple[str, str]] = [
    ("blink_enabled", "settings.blink_enabled"),
    ("look_away_enabled", "settings.look_away_enabled"),
    ("move_enabled", "settings.move_enabled"),
    ("deep_break_enabled", "settings.deep_break_enabled"),
    ("enable_sound", "settings.enable_sound"),
    ("enable_notification", "settings.enable_notification"),
    ("auto_start", "settings.auto_start"),
    ("minimize_to_tray", "settings.minimize_to_tray"),
    ("defer_on_fullscreen", "settings.defer_on_fullscreen"),
]

# (key, 标签 i18n 键, [(存储值, 显示文案 i18n 键)])
_COMBO_FIELDS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("cue_skin", "settings.cue_skin", [
        ("minimal", "settings.skin_minimal"),
        ("cartoon", "settings.skin_cartoon"),
        ("character", "settings.skin_character"),
    ]),
    ("cue_position", "settings.cue_position", [
        ("default", "settings.position_default"),
        ("bottom_left", "settings.position_bottom_left"),
        ("bottom_right", "settings.position_bottom_right"),
        ("top_left", "settings.position_top_left"),
        ("top_right", "settings.position_top_right"),
        ("custom", "settings.position_custom"),
    ]),
    ("cue_intensity", "settings.cue_intensity", [
        ("quiet", "settings.intensity_quiet"),
        ("standard", "settings.intensity_standard"),
        ("prominent", "settings.intensity_prominent"),
    ]),
    ("sound_scheme", "settings.sound_scheme", [
        ("breath", "settings.sound_breath"),
        ("wood", "settings.sound_wood"),
        ("glass", "settings.sound_glass"),
        ("nature", "settings.sound_nature"),
        ("bell", "settings.sound_bell"),
    ]),
]

#: 分组定义：(标题 i18n 键, 字段列表, 所在列 0/1, 是否可折叠)
_GROUP_DEFS: list[tuple[str, list[str], int, bool]] = [
    ("settings.group_blink", [
        "blink_enabled", "blink_cycle_seconds", "blink_cue_interval",
    ], 0, False),
    ("settings.group_look_away", [
        "look_away_enabled", "short_work_duration", "short_break_duration",
    ], 1, False),
    ("settings.group_move", [
        "move_enabled", "move_interval", "move_duration",
    ], 0, False),
    ("settings.group_deep_break", [
        "deep_break_enabled", "long_work_duration", "long_break_duration",
        "postpone_duration", "max_postpone",
    ], 1, False),
    ("settings.group_visual", [
        "cue_skin", "cue_position", "cue_intensity",
        "enable_sound", "sound_scheme",
    ], 0, False),
    ("settings.group_system", [
        "auto_start", "minimize_to_tray", "defer_on_fullscreen",
        "enable_notification",
    ], 1, False),
    ("settings.group_advanced", [
        "warning_duration", "idle_threshold", "natural_rest_threshold",
    ], 0, True),
]

_LANGUAGE_ITEMS: list[tuple[str, str]] = [
    ("中文", "zh"),
    ("English", "en"),
]


class NoScrollSpinBox(QSpinBox):
    """禁用滚轮的 SpinBox。

    设置页滚动时鼠标滚轮极易误改数值（例如 20 分钟变 21 分钟）。
    这里直接忽略滚轮事件，数值只能通过 ``[-]/[+]`` 按钮或键盘修改。
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """忽略滚轮事件，防止误触改数值。"""
        event.ignore()


class SettingsPage(QWidget):
    """Settings 设置页。"""

    #: 设置保存后发射，携带本次保存的设置字典
    settings_changed = Signal(dict)
    #: 用户在视觉分组选择「自定义」位置
    position_custom_requested = Signal()
    #: 用户选择了预设位置（点击即生效）
    position_preset_requested = Signal(str)
    #: 用户切换音效方案（用于试听）
    sound_scheme_requested = Signal(str)

    def __init__(
        self,
        break_service: BreakService,
        parent: QWidget | None = None,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = break_service
        #: 外部提示回调（例如位置保存成功后的浮动提示）
        self._notifier = notifier
        self._spinboxes: dict[str, QSpinBox] = {}
        self._checkboxes: dict[str, QCheckBox] = {}
        self._combos: dict[str, QComboBox] = {}
        self._volume_slider: QSlider | None = None
        self._volume_value_label: QLabel | None = None
        self._spin_labels: dict[str, QLabel] = {}
        self._combo_labels: dict[str, QLabel] = {}
        self._groups: list[tuple[QGroupBox, str]] = []
        self._title_label: QLabel | None = None
        self._save_button: QPushButton | None = None
        self._reset_button: QPushButton | None = None
        self._status_label: QLabel | None = None
        self._language_combo: QComboBox | None = None
        self._language_label: QLabel | None = None
        self._advanced_group: QGroupBox | None = None
        self._sound_widgets: list[QWidget] = []
        self._modified = False

        # 状态提示自动隐藏定时器
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_status)

        self.setup_ui()
        self.load_settings()
        get_translator().add_listener(self._on_language_changed)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def setup_ui(self) -> None:
        """构建设置页 UI（双栏紧凑布局）。"""
        self.setStyleSheet(_PAGE_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # 标题行（标题 + 状态提示）
        header = QHBoxLayout()
        self._title_label = QLabel(tr("settings.title"), self)
        self._title_label.setStyleSheet("font-size: 19px; font-weight: bold; color: #212121;")
        header.addWidget(self._title_label)
        header.addStretch(1)
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("font-size: 13px; color: #2E7D32; font-weight: bold;")
        header.addWidget(self._status_label)
        root.addLayout(header)

        # 双栏分组区（放在滚动区内，窗口很小时仍可滚动）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget(scroll)
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        row_by_col = {0: 0, 1: 0}
        for title_key, keys, column, collapsible in _GROUP_DEFS:
            group = self._build_group(title_key, keys, content, collapsible)
            row = row_by_col[column]
            grid.addWidget(group, row, column)
            row_by_col[column] = row + 1

        # 语言选择放在系统分组下方（第 1 列末尾）
        grid.addWidget(self._build_language_group(content), row_by_col[1], 1)
        grid.setRowStretch(max(row_by_col.values()) + 1, 1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # 底部按钮栏
        root.addLayout(self._build_button_bar())

    def _build_button_bar(self) -> QHBoxLayout:
        """构建底部按钮栏（恢复默认 / 保存设置）。"""
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self._reset_button = QPushButton(tr("settings.reset"), self)
        self._reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_button.setStyleSheet(
            "QPushButton { background-color: #9e9e9e; color: white; "
            "border: none; border-radius: 5px; padding: 8px 18px; font-size: 13px; }"
            "QPushButton:hover { background-color: #757575; }"
        )
        self._reset_button.clicked.connect(self._on_reset)
        bar.addWidget(self._reset_button)

        bar.addStretch(1)

        self._save_button = QPushButton(tr("settings.save"), self)
        self._save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_button.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "border: none; border-radius: 5px; padding: 8px 26px; font-size: 13px; "
            "font-weight: bold; }"
            "QPushButton:hover { background-color: #1565c0; }"
            "QPushButton:disabled { background-color: #c7c7c7; color: #f5f5f5; }"
        )
        self._save_button.clicked.connect(self.save_settings)
        bar.addWidget(self._save_button)
        return bar

    def _group_style(self) -> str:
        return (
            "QGroupBox { font-weight: bold; font-size: 13px; color: #333; "
            "border: 1px solid #e2e2e2; border-radius: 8px; margin-top: 10px; "
            "padding: 10px 12px 8px 12px; background-color: #ffffff; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )

    def _build_group(
        self,
        title_key: str,
        keys: list[str],
        parent: QWidget,
        collapsible: bool = False,
    ) -> QGroupBox:
        """构建一个设置分组。"""
        group = QGroupBox(tr(title_key), parent)
        group.setStyleSheet(self._group_style())
        if collapsible:
            group.setCheckable(True)
            group.setChecked(False)
            self._advanced_group = group
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)

        for key in keys:
            if key == "sound_scheme":
                self._add_sound_rows(form, group)
                continue
            spin_def = next((d for d in _SPINBOX_FIELDS if d[0] == key), None)
            if spin_def is not None:
                self._add_spinbox_row(form, spin_def, group)
                continue
            check_def = next((d for d in _CHECKBOX_FIELDS if d[0] == key), None)
            if check_def is not None:
                self._add_checkbox_row(form, check_def, group)
                continue
            combo_def = next((d for d in _COMBO_FIELDS if d[0] == key), None)
            if combo_def is not None:
                self._add_combo_row(form, combo_def, group)

        self._groups.append((group, title_key))
        return group

    def _build_language_group(self, parent: QWidget) -> QGroupBox:
        """构建语言选择分组（即时生效）。"""
        group = QGroupBox(tr("settings.group_language"), parent)
        group.setStyleSheet(self._group_style())
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)

        self._language_label = QLabel(tr("settings.language"), group)
        self._language_combo = QComboBox(group)
        for display, code in _LANGUAGE_ITEMS:
            self._language_combo.addItem(display, code)
        self._language_combo.currentIndexChanged.connect(self._on_language_combo_changed)
        form.addRow(self._language_label, self._language_combo)
        return group

    def _add_spinbox_row(
        self,
        form: QFormLayout,
        field_def: tuple[str, str, int, int, str, int],
        parent: QWidget,
    ) -> None:
        """添加一个禁用滚轮的时间/数量输入行。"""
        key, label_key, minimum, maximum, unit_key, _multiplier = field_def
        spin = NoScrollSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setSuffix(f" {tr(unit_key)}")
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.PlusMinus)
        spin.setFixedWidth(112)
        spin.valueChanged.connect(self._on_value_changed)
        self._spinboxes[key] = spin
        label = QLabel(tr(label_key), parent)
        self._spin_labels[key] = label
        form.addRow(label, spin)

    def _add_checkbox_row(
        self,
        form: QFormLayout,
        field_def: tuple[str, str],
        parent: QWidget,
    ) -> None:
        """添加一个开关行。"""
        key, label_key = field_def
        check = QCheckBox(tr(label_key), parent)
        check.stateChanged.connect(self._on_value_changed)
        if key == "auto_start":
            check.stateChanged.connect(self._on_auto_start_changed)
        if key == "enable_sound":
            # 声音开关变化时立即灰掉/启用声音相关控件
            check.stateChanged.connect(self._on_sound_toggled)
        self._checkboxes[key] = check
        form.addRow(check)

    def _add_combo_row(
        self,
        form: QFormLayout,
        field_def: tuple[str, str, list[tuple[str, str]]],
        parent: QWidget,
    ) -> None:
        """添加一个下拉选择行。"""
        key, label_key, items = field_def
        combo = QComboBox(parent)
        for value, display_key in items:
            combo.addItem(tr(display_key), value)
        combo.setFixedWidth(150)
        combo.currentIndexChanged.connect(self._on_value_changed)
        if key == "cue_position":
            combo.currentIndexChanged.connect(self._on_position_combo_changed)
        if key == "sound_scheme":
            combo.currentIndexChanged.connect(self._on_sound_scheme_changed)
        self._combos[key] = combo
        label = QLabel(tr(label_key), parent)
        self._combo_labels[key] = label
        form.addRow(label, combo)

    def _add_sound_rows(self, form: QFormLayout, parent: QWidget) -> None:
        """添加音效方案行 + 音量行（关闭提示音时整组灰掉）。"""
        combo_def = next(d for d in _COMBO_FIELDS if d[0] == "sound_scheme")
        self._add_combo_row(form, combo_def, parent)
        self._sound_widgets.append(self._combos["sound_scheme"])

        # 音量
        volume_widget = QWidget(parent)
        volume_layout = QHBoxLayout(volume_widget)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(8)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal, volume_widget)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setSingleStep(5)
        self._volume_slider.setFixedWidth(110)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._volume_value_label = QLabel("35%", volume_widget)
        self._volume_value_label.setFixedWidth(38)
        self._volume_value_label.setStyleSheet("color: #666; font-size: 12px;")

        volume_layout.addWidget(self._volume_slider)
        volume_layout.addWidget(self._volume_value_label)
        volume_layout.addStretch(1)

        volume_label = QLabel(tr("settings.sound_volume"), volume_widget)
        form.addRow(volume_label, volume_widget)
        self._sound_widgets.extend([self._volume_slider, self._volume_value_label, volume_label])

    # ------------------------------------------------------------------
    # 数据加载 / 保存
    # ------------------------------------------------------------------
    def load_settings(self) -> None:
        """从 BreakService 加载设置到 UI。"""
        settings = self._service.get_all_settings()

        for key, spin in self._spinboxes.items():
            stored = settings.get(key, BreakService.DEFAULT_SETTINGS.get(key, 0))
            field_def = next((d for d in _SPINBOX_FIELDS if d[0] == key), None)
            multiplier = field_def[5] if field_def else 1
            spin.blockSignals(True)
            spin.setValue(self._seconds_to_ui(stored, multiplier))
            spin.blockSignals(False)

        for key, check in self._checkboxes.items():
            value = bool(settings.get(key, BreakService.DEFAULT_SETTINGS.get(key, False)))
            check.blockSignals(True)
            check.setChecked(value)
            check.blockSignals(False)

        for key, combo in self._combos.items():
            stored = str(settings.get(key, BreakService.DEFAULT_SETTINGS.get(key, "")))
            combo.blockSignals(True)
            index = combo.findData(stored)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

        # 音量（0~100）
        if self._volume_slider is not None:
            volume = settings.get("sound_volume", BreakService.DEFAULT_SETTINGS["sound_volume"])
            self._volume_slider.blockSignals(True)
            self._volume_slider.setValue(int(round(float(volume) * 100)))
            self._volume_slider.blockSignals(False)
            self._update_volume_label()

        # 语言
        stored_lang = str(settings.get("language", "zh"))
        if stored_lang not in ("zh", "en"):
            stored_lang = "zh"
        if self._language_combo is not None:
            self._language_combo.blockSignals(True)
            index = self._language_combo.findData(stored_lang)
            self._language_combo.setCurrentIndex(index if index >= 0 else 0)
            self._language_combo.blockSignals(False)
        if get_language() != stored_lang:
            set_language(stored_lang)

        self._apply_sound_enabled_state()
        self._set_modified(False)

    def save_settings(self) -> dict[str, Any]:
        """保存 UI 设置到 BreakService，并给出 ``✓`` 反馈。"""
        to_save: dict[str, Any] = {}

        for key, spin in self._spinboxes.items():
            field_def = next((d for d in _SPINBOX_FIELDS if d[0] == key), None)
            multiplier = field_def[5] if field_def else 1
            to_save[key] = self._ui_to_seconds(spin.value(), multiplier)

        for key, check in self._checkboxes.items():
            to_save[key] = check.isChecked()

        for key, combo in self._combos.items():
            to_save[key] = combo.currentData()

        if self._volume_slider is not None:
            to_save["sound_volume"] = round(self._volume_slider.value() / 100.0, 2)

        changed = self._service.save_settings(to_save)
        self._set_modified(False)
        if changed:
            self._show_status(tr("settings.saved_feedback"))
            self.settings_changed.emit(changed)
        else:
            self._show_status(tr("settings.no_changes"))
        _log.info("设置页保存完成，变更 %d 项", len(changed))
        return changed

    # ------------------------------------------------------------------
    # 反馈
    # ------------------------------------------------------------------
    def _show_status(self, text: str) -> None:
        """显示绿色状态提示（2.5 秒后自动消失）。"""
        if self._status_label is None:
            return
        self._status_label.setText(text)
        self._status_timer.start(2500)

    def _clear_status(self) -> None:
        """清除状态提示。"""
        if self._status_label is not None:
            self._status_label.setText("")

    def show_message(self, text: str) -> None:
        """对外接口：显示一条反馈（如「✓ 提示位置已保存」）。"""
        self._show_status(text)
        if self._notifier is not None:
            try:
                self._notifier(text)
            except Exception:  # noqa: BLE001
                _log.exception("外部提示回调失败")

    # ------------------------------------------------------------------
    # 恢复默认
    # ------------------------------------------------------------------
    def _on_reset(self) -> None:
        """恢复默认设置（确认后立即生效并持久化）。"""
        answer = QMessageBox.question(
            self,
            tr("settings.reset_confirm_title"),
            tr("settings.reset_confirm_body"),
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.RestoreDefaults,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.RestoreDefaults:
            return
        self._service.reset_to_defaults()
        # 默认值立即写入配置（不只是删除键），保证"恢复默认"后确实持久化
        self._service.save_settings(dict(BreakService.DEFAULT_SETTINGS))
        self.load_settings()
        # 同步开机自启状态（load_settings 期间信号被阻塞）
        auto_start = self._checkboxes.get("auto_start")
        if auto_start is not None:
            self._sync_auto_start(auto_start.isChecked())
        self._show_status(tr("settings.reset_feedback"))
        _log.info("设置已恢复默认值并立即生效")

    # ------------------------------------------------------------------
    # 交互回调
    # ------------------------------------------------------------------
    def _on_value_changed(self, *_args: Any) -> None:
        """控件值变化 → 标记为已修改。"""
        self._set_modified(True)

    def _on_auto_start_changed(self, state: int) -> None:
        """开机自启开关实时同步注册表。"""
        self._sync_auto_start(state == Qt.CheckState.Checked.value)

    def _sync_auto_start(self, enabled: bool) -> None:
        from app.windows.startup import StartupManager

        if not StartupManager.toggle(bool(enabled)):
            _log.warning("开机自启 %s 失败", "启用" if enabled else "禁用")

    def _on_sound_toggled(self, _state: int) -> None:
        """声音开关：关闭时灰掉方案与音量，开启时启用。"""
        self._apply_sound_enabled_state()

    def _apply_sound_enabled_state(self) -> None:
        """按当前开关状态启用/禁用声音相关控件。"""
        check = self._checkboxes.get("enable_sound")
        enabled = bool(check.isChecked()) if check is not None else False
        for widget in self._sound_widgets:
            widget.setEnabled(enabled)

    def _on_sound_scheme_changed(self) -> None:
        """切换音效方案：试听一次并标记为已修改。"""
        combo = self._combos.get("sound_scheme")
        if combo is None:
            return
        scheme = combo.currentData()
        if scheme and self._checkboxes.get("enable_sound") is not None:
            if self._checkboxes["enable_sound"].isChecked():
                self.sound_scheme_requested.emit(str(scheme))
        self._set_modified(True)

    def _on_volume_changed(self, value: int) -> None:
        """音量滑块变化：更新百分比标签并标记修改。"""
        self._update_volume_label()
        self._set_modified(True)

    def _update_volume_label(self) -> None:
        if self._volume_slider is None or self._volume_value_label is None:
            return
        self._volume_value_label.setText(f"{self._volume_slider.value()}%")

    def _on_position_combo_changed(self) -> None:
        """位置选择变化：预设立即生效；自定义打开位置编辑器。"""
        combo = self._combos.get("cue_position")
        if combo is None or not combo.signalsBlocked():
            pass
        if combo is None:
            return
        value = combo.currentData()
        if value == "custom":
            self.position_custom_requested.emit()
        elif value:
            # 预设位置：点击即生效（立即写入配置，不必等保存）
            self._service.set_setting("cue_position", str(value))
            self.position_preset_requested.emit(str(value))

    def _on_language_combo_changed(self, _index: int) -> None:
        """语言切换即时生效并持久化。"""
        if self._language_combo is None:
            return
        code = self._language_combo.currentData()
        if not code:
            return
        if code != get_language():
            set_language(code)
        if code != self._service.get_setting("language", "zh"):
            self._service.set_setting("language", code)

    # ------------------------------------------------------------------
    # 修改标记
    # ------------------------------------------------------------------
    def _set_modified(self, modified: bool) -> None:
        """更新修改标记与保存按钮可用性。"""
        self._modified = modified
        if self._title_label is not None:
            base = tr("settings.title")
            self._title_label.setText(f"{base} *" if modified else base)
        if self._save_button is not None:
            self._save_button.setEnabled(modified)

    def is_modified(self) -> bool:
        """返回当前是否有未保存的修改。"""
        return self._modified

    # ------------------------------------------------------------------
    # 语言切换
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        """语言变化时刷新全部静态文本。"""
        base = tr("settings.title")
        if self._title_label is not None:
            self._title_label.setText(f"{base} *" if self._modified else base)
        if self._save_button is not None:
            self._save_button.setText(tr("settings.save"))
        if self._reset_button is not None:
            self._reset_button.setText(tr("settings.reset"))
        if self._language_label is not None:
            self._language_label.setText(tr("settings.language"))
        for group, key in self._groups:
            group.setTitle(tr(key))
        for key, label_key, items in _COMBO_FIELDS:
            if key in self._combo_labels:
                self._combo_labels[key].setText(tr(label_key))
            if key in self._combos:
                for i, (_value, display_key) in enumerate(items):
                    self._combos[key].setItemText(i, tr(display_key))
        for key, label_key, _mn, _mx, unit_key, _mult in _SPINBOX_FIELDS:
            if key in self._spin_labels:
                self._spin_labels[key].setText(tr(label_key))
            if key in self._spinboxes:
                self._spinboxes[key].setSuffix(f" {tr(unit_key)}")
        for key, label_key in _CHECKBOX_FIELDS:
            if key in self._checkboxes:
                self._checkboxes[key].setText(tr(label_key))

    def _on_language_changed(self, lang: str) -> None:
        """语言变化监听回调。"""
        self.retranslate_ui()

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """移除语言监听（幂等）。"""
        get_translator().remove_listener(self._on_language_changed)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """页面关闭时移除语言监听。"""
        self.cleanup()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 单位换算
    # ------------------------------------------------------------------
    @staticmethod
    def _seconds_to_ui(seconds: int, multiplier: int) -> int:
        """存储值(秒) → UI 值。"""
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return 0
        if multiplier <= 1:
            return seconds
        return max(1, round(seconds / multiplier))

    @staticmethod
    def _ui_to_seconds(ui_value: int, multiplier: int) -> int:
        """UI 值 → 存储值(秒)。"""
        if multiplier <= 1:
            return int(ui_value)
        return int(ui_value) * multiplier


_PAGE_STYLE = """
QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #CFCFCF; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #B0B0B0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 0; }
QCheckBox { spacing: 8px; font-size: 13px; color: #424242; padding: 2px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px;
    border: 1.5px solid #BDBDBD; background-color: #FFFFFF; }
QCheckBox::indicator:checked { background-color: #4CAF50; border-color: #4CAF50;
    image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMiAxMiI+PHBvbHlsaW5lIHBvaW50cz0iMiw2IDUsOSAxMCwzIiBzdHlsZT0iZmlsbDpub25lO3N0cm9rZToiI2ZmZmZmZjtzdHJva2Utd2lkdGg6IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg==); }
QCheckBox:disabled { color: #BDBDBD; }
QCheckBox::indicator:disabled { background-color: #F5F5F5; border-color: #E0E0E0; }
QComboBox { padding: 4px 8px; border: 1px solid #D5D5D5; border-radius: 5px;
    font-size: 13px; background-color: #FFFFFF; min-height: 20px; }
QComboBox:disabled { background-color: #F5F5F5; color: #B0B0B0; }
QSpinBox { padding: 3px 6px; border: 1px solid #D5D5D5; border-radius: 5px;
    font-size: 13px; background-color: #FFFFFF; min-height: 20px; }
QSlider::groove:horizontal { height: 4px; background: #E0E0E0; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px;
    background: #1976d2; }
QSlider:disabled::handle:horizontal { background: #BDBDBD; }
QLabel { font-size: 13px; color: #424242; }
"""
