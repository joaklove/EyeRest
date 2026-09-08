"""提示音服务（V0.5：五套方案 + 不抢焦点播放）。

设计要点：

* **音效本地合成**：首次使用时由 :mod:`app.utils.sound_synth` 生成 WAV
  到数据目录 ``sounds/``，不需要打包任何音频资源
* **不抢焦点**：用 ``QSoundEffect`` 播放，不弹窗、不激活窗口、不阻塞输入
* **低打扰**：Blink 不在每次 Cue 出声，只在每个 Blink Cycle 结束时响一次
* **强度联动**：安静 = 静音；标准 = 正常音量；明显 = 稍大

播放层级与音量系数（见 :mod:`app.config.defaults`）::

    blink_cycle  0.5   最轻
    look_away    0.8
    move         0.8
    deep_break   1.0   稍明显
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import defaults
from app.utils.logger import get_logger

logger = get_logger(__name__)

# QSoundEffect 需要 Qt；导入失败时降级为"无声但不报错"
try:  # pragma: no cover - 依赖运行环境
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect

    _QT_SOUND_AVAILABLE = True
except Exception:  # noqa: BLE001
    QSoundEffect = None  # type: ignore[assignment]
    QUrl = None  # type: ignore[assignment]
    _QT_SOUND_AVAILABLE = False


#: 播放层级 → 音量系数
_KIND_GAIN: dict[str, float] = {
    "blink": defaults.SOUND_GAIN_BLINK,
    "blink_cycle": defaults.SOUND_GAIN_BLINK,
    "look_away": defaults.SOUND_GAIN_LOOK_AWAY,
    "move": defaults.SOUND_GAIN_MOVE,
    "deep_break": defaults.SOUND_GAIN_DEEP_BREAK,
}

#: 提醒强度 → 音量系数（安静 = 完全静音）
_INTENSITY_GAIN: dict[str, float] = {
    "quiet": 0.0,
    "standard": 1.0,
    "prominent": 1.25,
}


class SoundService:
    """提示音服务。

    Args:
        sounds_dir: 音效文件目录；默认使用数据目录下 ``sounds/``。
        enabled: 是否启用提示音。
        scheme: 音效方案（``wood``/``glass``/``breath``/``nature``/``bell``）。
        volume: 基础音量 0.0~1.0。
        intensity: 提醒强度（``quiet``/``standard``/``prominent``）。
    """

    SCHEMES: tuple[str, ...] = ("breath", "wood", "glass", "nature", "bell")

    def __init__(
        self,
        sounds_dir: Path | str | None = None,
        enabled: bool | None = None,
        scheme: str | None = None,
        volume: float | None = None,
        intensity: str = "standard",
    ) -> None:
        if sounds_dir is None:
            from app.utils.system import get_app_data_dir

            sounds_dir = Path(get_app_data_dir()) / defaults.SOUNDS_DIRNAME
        self._dir = Path(sounds_dir)
        self._enabled = bool(
            defaults.ENABLE_SOUND if enabled is None else enabled
        )
        self._scheme = scheme or defaults.SOUND_SCHEME
        self._volume = float(defaults.SOUND_VOLUME if volume is None else volume)
        self._intensity = intensity
        self._effects: dict[str, Any] = {}
        self._available = _QT_SOUND_AVAILABLE
        if not self._available:
            logger.warning("QtMultimedia 不可用，提示音功能已禁用")

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def is_available(self) -> bool:
        """当前环境是否可以播放声音。"""
        return self._available

    @property
    def scheme(self) -> str:
        """当前音效方案。"""
        return self._scheme

    @property
    def enabled(self) -> bool:
        """提示音是否启用。"""
        return self._enabled

    # ------------------------------------------------------------------
    # 设置更新
    # ------------------------------------------------------------------
    def update_settings(
        self,
        enabled: bool | None = None,
        scheme: str | None = None,
        volume: float | None = None,
        intensity: str | None = None,
    ) -> None:
        """热更新声音设置（设置页保存后调用）。"""
        if enabled is not None:
            self._enabled = bool(enabled)
        if scheme:
            self._scheme = scheme
            self._effects.clear()  # 换方案后旧缓存失效
        if volume is not None:
            self._volume = max(0.0, min(1.0, float(volume)))
        if intensity:
            self._intensity = intensity
        logger.info(
            "提示音设置已更新: enabled=%s scheme=%s volume=%.2f intensity=%s",
            self._enabled,
            self._scheme,
            self._volume,
            self._intensity,
        )

    def effective_volume(self, kind: str = "look_away") -> float:
        """计算某类提醒的实际音量（0 表示不出声）。"""
        if not self._enabled:
            return 0.0
        intensity_gain = _INTENSITY_GAIN.get(self._intensity, 1.0)
        if intensity_gain <= 0.0:
            return 0.0  # 安静模式：静音
        kind_gain = _KIND_GAIN.get(kind, 1.0)
        return max(0.0, min(1.0, self._volume * kind_gain * intensity_gain))

    # ------------------------------------------------------------------
    # 播放
    # ------------------------------------------------------------------
    def play(self, kind: str = "look_away") -> bool:
        """播放指定层级的提示音。

        Args:
            kind: ``blink_cycle`` / ``look_away`` / ``move`` / ``deep_break``。

        Returns:
            是否真的播放了（关闭/静音/不可用时返回 False）。
        """
        volume = self.effective_volume(kind)
        if volume <= 0.0 or not self._available:
            return False
        effect = self._get_effect(self._scheme)
        if effect is None:
            return False
        try:
            effect.setVolume(volume)
            effect.play()
            logger.debug("播放提示音: kind=%s scheme=%s vol=%.2f", kind, self._scheme, volume)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("播放提示音失败")
            return False

    def preview(self, scheme: str | None = None) -> bool:
        """试听音效（设置页切换方案时调用，忽略"安静"强度）。"""
        if not self._available:
            return False
        target = scheme or self._scheme
        effect = self._get_effect(target)
        if effect is None:
            return False
        try:
            effect.setVolume(max(0.05, min(1.0, self._volume or 0.35)))
            effect.play()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("试听音效失败")
            return False

    # ------------------------------------------------------------------
    # 音效文件
    # ------------------------------------------------------------------
    def ensure_sounds(self) -> None:
        """确保五套音效文件都已生成（缺失才合成）。"""
        from app.utils import sound_synth

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("创建音效目录失败: %s", self._dir)
            return
        for scheme in self.SCHEMES:
            path = self._dir / f"{scheme}.wav"
            if path.exists():
                continue
            try:
                sound_synth.write_wav(path, scheme)
                logger.debug("已合成音效: %s", path.name)
            except Exception:  # noqa: BLE001
                logger.exception("合成音效失败: %s", scheme)

    def sound_path(self, scheme: str) -> Path:
        """返回指定方案对应的 WAV 路径。"""
        return self._dir / f"{scheme}.wav"

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _get_effect(self, scheme: str) -> Any:
        """获取（必要时创建并加载）指定方案的 QSoundEffect。"""
        if not self._available:
            return None
        cached = self._effects.get(scheme)
        if cached is not None:
            return cached
        path = self.sound_path(scheme)
        if not path.exists():
            self.ensure_sounds()
        if not path.exists():
            logger.warning("音效文件缺失: %s", path)
            return None
        try:
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            self._effects[scheme] = effect
            return effect
        except Exception:  # noqa: BLE001
            logger.exception("加载音效失败: %s", path)
            return None
