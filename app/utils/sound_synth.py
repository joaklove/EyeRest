"""提示音合成（纯 Python，无需任何音频资源文件）。

V0.5 提供五套提示音方案，全部在首次使用时合成到数据目录
``sounds/`` 下，作为 16bit 单声道 WAV 播放：

============  ======================================  ==========
方案          听感                                    默认用途
============  ======================================  ==========
``wood``      轻微木质敲击 ``tick``，极短极轻          轻量确认
``glass``     柔和玻璃音，清晰但无警报感               远眺
``breath``    呼吸 ``whoosh``，与呼吸式动画统一        默认方案
``nature``    短促鸟鸣，让人联想到「离开屏幕一下」     远眺 / 活动
``bell``      柔和铃音，单次 soft bell                 深度休息
============  ======================================  ==========

所有声音都遵循同一原则：**短、轻、无警报感**，不打断用户。
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

#: 采样率（22.05kHz 对提示音足够，文件体积约为 44.1kHz 的一半）
SAMPLE_RATE = 22050

#: 五套方案的标识
SCHEMES: tuple[str, ...] = ("wood", "glass", "breath", "nature", "bell")


def _sine(freq: float, t: float) -> float:
    """正弦波采样值。"""
    return math.sin(2.0 * math.pi * freq * t)


def _exp_decay(t: float, duration: float, power: float = 3.0) -> float:
    """指数衰减包络（0~1）。"""
    if duration <= 0:
        return 0.0
    x = max(0.0, min(1.0, t / duration))
    return (1.0 - x) ** power


def _attack_decay(t: float, duration: float, attack_ratio: float = 0.25) -> float:
    """先渐入再衰减的包络（呼吸音用）。"""
    if duration <= 0:
        return 0.0
    x = max(0.0, min(1.0, t / duration))
    attack = max(1e-6, attack_ratio)
    if x < attack:
        return x / attack
    return (1.0 - (x - attack) / (1.0 - attack)) ** 2.0


def _one_pole_lowpass(samples: list[float], cutoff: float) -> list[float]:
    """一阶低通滤波（把白噪声变柔和）。"""
    alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)
    out: list[float] = []
    prev = 0.0
    for value in samples:
        prev += alpha * (value - prev)
        out.append(prev)
    return out


def _render_wood(duration: float = 0.12) -> list[float]:
    """木质轻响：低频敲击 + 极短噪声瞬态。"""
    frames = int(SAMPLE_RATE * duration)
    rng = random.Random(20260908)
    out: list[float] = []
    for i in range(frames):
        t = i / SAMPLE_RATE
        env = _exp_decay(t, duration, power=5.0)
        body = _sine(196.0, t) * 0.7 + _sine(392.0, t) * 0.25
        click = (rng.random() * 2.0 - 1.0) * 0.25 * _exp_decay(t, 0.02, power=2.0)
        out.append((body + click) * env * 0.5)
    return out


def _render_glass(duration: float = 0.45) -> list[float]:
    """玻璃轻音：两个高音正弦叠加，快速衰减。"""
    frames = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(frames):
        t = i / SAMPLE_RATE
        env = _exp_decay(t, duration, power=2.5)
        tone = (
            _sine(1174.7, t) * 0.6
            + _sine(1760.0, t) * 0.3
            + _sine(2637.0, t) * 0.1
        )
        out.append(tone * env * 0.42)
    return out


def _render_breath(duration: float = 0.9) -> list[float]:
    """呼吸音：低通白噪声 + 渐入渐出包络（whoosh）。"""
    frames = int(SAMPLE_RATE * duration)
    rng = random.Random(11223344)
    noise = [rng.random() * 2.0 - 1.0 for _ in range(frames)]
    # 两级低通，得到柔和的气流声
    smoothed = _one_pole_lowpass(_one_pole_lowpass(noise, 700.0), 500.0)
    out: list[float] = []
    for i, value in enumerate(smoothed):
        t = i / SAMPLE_RATE
        env = _attack_decay(t, duration, attack_ratio=0.3)
        # 叠一点低频，让声音更"厚"
        out.append((value * 1.6 + _sine(140.0, t) * 0.12) * env * 0.55)
    return out


def _render_nature(duration: float = 0.35) -> list[float]:
    """自然单音：短促鸟鸣（频率上扬后回落 + 轻微颤音）。"""
    frames = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(frames):
        t = i / SAMPLE_RATE
        x = t / duration
        # 频率轨迹：2200 → 3100 → 2500 Hz
        if x < 0.4:
            freq = 2200.0 + (3100.0 - 2200.0) * (x / 0.4)
        else:
            freq = 3100.0 - (3100.0 - 2500.0) * ((x - 0.4) / 0.6)
        vibrato = 1.0 + 0.03 * _sine(28.0, t)
        env = _attack_decay(t, duration, attack_ratio=0.12)
        out.append(_sine(freq * vibrato, t) * env * 0.35)
    return out


def _render_bell(duration: float = 1.3) -> list[float]:
    """柔和铃音：基频 + 两个非整数倍泛音，长衰减。"""
    frames = int(SAMPLE_RATE * duration)
    out: list[float] = []
    for i in range(frames):
        t = i / SAMPLE_RATE
        env = _exp_decay(t, duration, power=2.2)
        tone = (
            _sine(659.3, t) * 0.55
            + _sine(987.8, t) * 0.25
            + _sine(1318.5, t) * 0.12
            + _sine(1975.5, t) * 0.06
        )
        out.append(tone * env * 0.42)
    return out


_RENDERERS = {
    "wood": _render_wood,
    "glass": _render_glass,
    "breath": _render_breath,
    "nature": _render_nature,
    "bell": _render_bell,
}


def render(scheme: str) -> list[float]:
    """渲染指定方案的采样序列（-1.0 ~ 1.0）。"""
    func = _RENDERERS.get(scheme)
    if func is None:
        raise ValueError(f"未知音效方案: {scheme}")
    return func()


def write_wav(path: Path | str, scheme: str) -> Path:
    """把指定方案合成为 WAV 文件并返回路径。"""
    samples = render(scheme)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for value in samples:
        clamped = max(-1.0, min(1.0, float(value)))
        frames += struct.pack("<h", int(clamped * 32767))
    with wave.open(str(target), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return target
