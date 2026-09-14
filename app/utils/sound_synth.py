"""提示音合成（纯 Python，无需任何音频资源文件）。

V0.5 提供五套轻提示音，V0.6.4 起增加三套**打击乐（钵 / 木鱼 / 钟）**，
全部在首次使用时合成到数据目录 ``sounds/`` 下，作为 16bit 单声道 WAV 播放：

===============  ======================================  ==========
方案             听感                                    默认用途
===============  ======================================  ==========
``wood``         轻微木质敲击 ``tick``，极短极轻          轻量确认
``glass``        柔和玻璃音，清晰但无警报感               远眺
``breath``       呼吸 ``whoosh``，与呼吸式动画统一        默认方案
``nature``       短促鸟鸣，让人联想到「离开屏幕一下」     远眺 / 活动
``bell``         柔和铃音，单次 soft bell                 深度休息
``bowl``         颂钵，缓慢拍频 + 悠长余韵                冥想 / 深度休息
``woodfish``     木鱼，短促干脆的「笃」                  节拍 / 眨眼
``temple_bell``  寺钟，低沉 modal bell + 极长余音         深度休息 / 收束
===============  ======================================  ==========

两类声音的取向不同，但原则一致 —— **不打断用户**：

* 轻提示音（前五套）：短、轻、无警报感
* 打击乐（后三套）：有明确音高与余韵，但**不带催促感**（不用高频、不用
  急促重复），靠共鸣而非音量进入注意

实现要点：打击乐用**多模态叠加 + 每模态独立衰减时间常数**。真实乐器的高频
模态衰减快、低频模态拖得久，用统一包络会立刻听起来像电子音。
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

#: 采样率（22.05kHz 对提示音足够，文件体积约为 44.1kHz 的一半）
SAMPLE_RATE = 22050

#: 全部方案的标识（顺序即在设置页下拉里的顺序）
SCHEMES: tuple[str, ...] = (
    "breath",
    "wood",
    "glass",
    "nature",
    "bell",
    "bowl",
    "woodfish",
    "temple_bell",
)


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


# ---------------------------------------------------------------------------
# 打击乐（钵 / 木鱼 / 钟）的通用工具
# ---------------------------------------------------------------------------
def _modal_sum(
    duration: float,
    partials: list[tuple[float, float, float]],
    attack: float = 0.004,
) -> list[float]:
    """多模态叠加：每个模态自带 (频率, 幅度, 衰减时间常数)。

    真实打击乐器的每个模态衰减速度不同 —— 高频衰得快、低频拖得久。
    用统一包络（现有 ``_render_bell`` 的做法）会立刻听起来像电子音。

    Args:
        duration: 总时长（秒）。
        partials: ``[(freq_hz, amplitude, tau_seconds), ...]``。
        attack: 起振渐入时长（秒）。木鱼这种敲击用接近 0，钵要稍长。

    Returns:
        采样序列（未归一化，峰值取决于幅度之和）。
    """
    frames = int(SAMPLE_RATE * duration)
    out = [0.0] * frames
    for freq, amp, tau in partials:
        omega = 2.0 * math.pi * freq / SAMPLE_RATE
        # 每模态只算一次 exp，之后逐帧递推，避免逐帧调用 math.exp
        per_frame = math.exp(-1.0 / (max(1e-6, tau) * SAMPLE_RATE))
        ramp_frames = max(0, int(attack * SAMPLE_RATE))
        gain = amp
        for i in range(frames):
            ramp = 1.0 if i >= ramp_frames or ramp_frames == 0 else i / ramp_frames
            out[i] += gain * math.sin(omega * i) * ramp
            gain *= per_frame
    return out


def _strike_noise(
    frames: int,
    amplitude: float,
    tau: float,
    cutoff: float,
    seed: int,
) -> list[float]:
    """敲击瞬态：极短的低通噪声（模拟槌子接触的那一下）。"""
    rng = random.Random(seed)
    raw = [rng.random() * 2.0 - 1.0 for _ in range(frames)]
    filtered = _one_pole_lowpass(raw, cutoff)
    return [
        value * amplitude * math.exp(-(i / SAMPLE_RATE) / max(1e-6, tau))
        for i, value in enumerate(filtered)
    ]


def _fade_tail(samples: list[float], seconds: float = 0.12) -> list[float]:
    """末尾淡出。

    长衰减的音若在 duration 处直接截断（``exp(-t/tau)`` 还没归零）会发出
    「咔」的爆音。钟的 hum 模态 tau=5s、总长 5.5s，截断时仍有约 33% 幅度。
    """
    total = len(samples)
    n = min(total, int(SAMPLE_RATE * seconds))
    for i in range(n):
        x = (i + 1) / n
        samples[total - n + i] *= 0.5 * (1.0 + math.cos(math.pi * x))
    return samples


def _normalize(samples: list[float], peak: float) -> list[float]:
    """按实测峰值归一化到指定峰值（长音的响度不靠手调系数）。"""
    current = max((abs(v) for v in samples), default=0.0) or 1.0
    scale = peak / current
    return [v * scale for v in samples]


def _render_bowl(duration: float = 4.5) -> list[float]:
    """颂钵：缓慢**拍频** + 非谐泛音 + 长衰减。

    标志性听感来自拍频 —— 两个相差约 1 Hz 的分音相互干涉，产生类似"呼吸"
    的缓慢强弱起伏。这正是颂钵用于放松的声学原因，缺了就只是"一声金属响"。
    """
    f0 = 216.0
    partials = [
        (f0, 1.00, 3.4),
        (f0 * 1.006, 0.92, 3.2),   # 与基频差 ≈1.3 Hz → 缓慢拍频
        (f0 * 2.71, 0.40, 2.2),
        (f0 * 2.727, 0.35, 2.0),   # 高模态也成对，起伏更细密
        (f0 * 5.18, 0.17, 1.2),
        (f0 * 8.32, 0.06, 0.6),
    ]
    # 钵是"擦"响的，不是敲的：起振要慢（90ms），几乎没有瞬态噪声。
    # 峰值压到 0.44 —— 4.5s 的长音在同等峰值下感知响度远高于短音，
    # 不压的话用户切换方案会听到明显的响度跳变。
    out = _modal_sum(duration, partials, attack=0.09)
    return _fade_tail(_normalize(out, 0.44), seconds=0.3)


def _render_woodfish(duration: float = 0.30) -> list[float]:
    """木鱼：短促干脆的「笃」，木质共鸣 + 击槌瞬态。

    与既有 ``wood``（196/392 Hz，偏闷、极短）区分：木鱼共鸣中心更高、更脆、
    有明确音高，尾音很短 —— 干、短、有颗粒感。
    """
    f0 = 610.0
    partials = [
        (f0 * 0.82, 0.55, 0.075),
        (f0, 1.00, 0.055),
        (f0 * 1.53, 0.45, 0.038),
        (f0 * 2.41, 0.22, 0.022),
        (f0 * 3.60, 0.08, 0.012),
    ]
    out = _modal_sum(duration, partials, attack=0.0015)
    strike = _strike_noise(len(out), 0.30, 0.006, 4500.0, seed=90210)
    mixed = [a + b for a, b in zip(out, strike)]
    return _fade_tail(_normalize(mixed, 0.50), seconds=0.03)


def _render_temple_bell(duration: float = 5.5) -> list[float]:
    """寺钟：钟体模态（hum / prime / tierce / quint / nominal）+ 极长余音。

    模态比例取自真实钟体的**非谐**值，不是整数倍 —— 这是"钟"与"风铃"的
    区别所在。``hum`` 比基频低八度且衰减最慢，钟"余音绕梁"的厚度来自它。
    """
    prime = 155.6
    partials = [
        (prime * 0.50, 0.55, 5.0),   # hum，最慢
        (prime * 1.00, 0.72, 3.4),   # prime（听感上的基音）
        (prime * 1.19, 0.40, 2.6),   # tierce，小三度 —— 决定大钟/小钟色彩
        (prime * 1.50, 0.28, 2.0),   # quint
        (prime * 2.00, 0.46, 1.8),   # nominal
        (prime * 2.50, 0.16, 1.2),   # superquint
        (prime * 4.00, 0.10, 0.7),   # octave nominal
    ]
    out = _modal_sum(duration, partials, attack=0.006)
    strike = _strike_noise(len(out), 0.16, 0.06, 3000.0, seed=5150)
    mixed = [a + b for a, b in zip(out, strike)]
    return _fade_tail(_normalize(mixed, 0.46), seconds=0.35)


_RENDERERS = {
    "wood": _render_wood,
    "glass": _render_glass,
    "breath": _render_breath,
    "nature": _render_nature,
    "bell": _render_bell,
    "bowl": _render_bowl,
    "woodfish": _render_woodfish,
    "temple_bell": _render_temple_bell,
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
