"""提示音合成守卫。

守的是几类**不报错、只是"不对"**的问题 —— 这类问题最难发现：

1. 新增音色只改了合成器，忘了同步设置页 —— 用户下拉里根本看不到
   （历史形态：合成器的 ``SCHEMES`` 与 UI 的 ``_COMBO_FIELDS`` 各写一份清单，
   靠人记得同步）。
2. 加了音色但漏写 i18n 文案 —— 下拉里直接显示 ``settings.sound_xxx`` 原始键。
3. 长衰减音在 duration 处被直接截断 —— 末帧不为零，播放结束有「咔」的爆音。
   钟的 hum 模态 tau=5s、总长 5.5s，若不淡出截断时仍残留约 33% 幅度。
4. 新音色峰值远高于既有音色 —— 用户在同一音量下滑块切换方案会听到响度跳变。

写文件一律落在 ``.build/test_data``（G 盘）—— 硬约束：测试不得写 C 盘。
"""

from __future__ import annotations

import unittest
import wave
from pathlib import Path

from app.utils import sound_synth

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / ".build" / "test_data" / "sound_synth_out"

#: 新增的打击乐（钵 / 木鱼 / 钟）
STRIKES = ("bowl", "woodfish", "temple_bell")

#: 峰值允许区间。下界防"合成出来几乎无声"，上界防响度跳变（既有方案
#: 实测 0.346~0.484，长音在同等峰值下感知更响，故刻意压在 0.5 以内）。
PEAK_MIN = 0.20
PEAK_MAX = 0.65


class TestSchemeRegistry(unittest.TestCase):
    """方案清单只能有一个真源。"""

    def test_every_scheme_renders(self) -> None:
        for scheme in sound_synth.SCHEMES:
            samples = sound_synth.render(scheme)
            self.assertGreater(len(samples), 0, f"{scheme} 渲染结果为空")

    def test_unknown_scheme_raises(self) -> None:
        with self.assertRaises(ValueError):
            sound_synth.render("no_such_scheme")

    def test_strikes_are_registered(self) -> None:
        """三套打击乐必须在册 —— 少一个说明注册漏了。"""
        for scheme in STRIKES:
            self.assertIn(scheme, sound_synth.SCHEMES)

    def test_service_shares_synth_registry(self) -> None:
        """SoundService 的方案清单必须是合成器那一份，不许另存一份。"""
        from app.services.sound_service import SoundService

        self.assertIs(
            SoundService.SCHEMES,
            sound_synth.SCHEMES,
            "SoundService.SCHEMES 应直接引用 sound_synth.SCHEMES",
        )

    def test_settings_page_lists_every_scheme(self) -> None:
        """设置页下拉必须列出全部方案，且顺序一致。"""
        from app.ui.settings import _COMBO_FIELDS

        options = next(d[2] for d in _COMBO_FIELDS if d[0] == "sound_scheme")
        self.assertEqual(
            [scheme for scheme, _ in options],
            list(sound_synth.SCHEMES),
            "设置页音效方案与合成器注册表不一致 —— 用户会看不到新音色",
        )


class TestI18nCoverage(unittest.TestCase):
    """每个方案都要有中英文案，否则界面显示原始键。"""

    def test_every_scheme_has_label_in_both_languages(self) -> None:
        from app.i18n.translator import _STRINGS

        for scheme in sound_synth.SCHEMES:
            key = f"settings.sound_{scheme}"
            for lang, table in _STRINGS.items():
                self.assertIn(key, table, f"[{lang}] 缺音效文案：{key}")
                self.assertNotEqual(
                    table[key].strip(),
                    key,
                    f"[{lang}] {key} 的值仍是键名本身",
                )

    def test_labels_are_unique(self) -> None:
        """文案不许重复 —— 两个音色同名，用户没法区分。"""
        from app.i18n.translator import _STRINGS

        for lang, table in _STRINGS.items():
            labels = [table[f"settings.sound_{s}"] for s in sound_synth.SCHEMES]
            self.assertEqual(
                len(labels), len(set(labels)), f"[{lang}] 存在重复的音效文案"
            )


class TestWaveform(unittest.TestCase):
    """波形本身的健康度。"""

    def test_tail_fades_to_silence(self) -> None:
        """末帧必须接近 0 —— 否则长衰减被截断会发出「咔」的爆音。"""
        for scheme in sound_synth.SCHEMES:
            samples = sound_synth.render(scheme)
            self.assertLess(
                abs(samples[-1]),
                0.01,
                f"{scheme} 末帧为 {samples[-1]:.4f}，未淡出（会有爆音）",
            )

    def test_peak_within_range(self) -> None:
        for scheme in sound_synth.SCHEMES:
            peak = max(abs(v) for v in sound_synth.render(scheme))
            self.assertGreaterEqual(peak, PEAK_MIN, f"{scheme} 峰值过低：{peak:.3f}")
            self.assertLessEqual(peak, PEAK_MAX, f"{scheme} 峰值过高：{peak:.3f}")

    def test_no_clipping(self) -> None:
        """16bit 有符号范围是 -32768~32767，超出即削波。"""
        for scheme in sound_synth.SCHEMES:
            peak = max(abs(v) for v in sound_synth.render(scheme))
            self.assertLessEqual(peak, 1.0, f"{scheme} 会削波：{peak:.3f}")

    def test_strikes_are_long_enough_to_ring(self) -> None:
        """钵与钟的价值在余韵 —— 太短说明模态衰减没生效。"""
        for scheme, min_seconds in (("bowl", 2.0), ("temple_bell", 3.0)):
            samples = sound_synth.render(scheme)
            seconds = len(samples) / sound_synth.SAMPLE_RATE
            self.assertGreaterEqual(
                seconds, min_seconds, f"{scheme} 仅 {seconds:.1f}s，余韵过短"
            )


class TestWavOutput(unittest.TestCase):
    """落盘格式（写盘位置限定在 G 盘）。"""

    def test_written_format(self) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for scheme in sound_synth.SCHEMES:
            path = sound_synth.write_wav(OUT_DIR / f"{scheme}.wav", scheme)
            with wave.open(str(path)) as handle:
                self.assertEqual(handle.getnchannels(), 1, scheme)
                self.assertEqual(handle.getsampwidth(), 2, scheme)
                self.assertEqual(handle.getframerate(), sound_synth.SAMPLE_RATE, scheme)
                self.assertEqual(
                    handle.getnframes(), len(sound_synth.render(scheme)), scheme
                )

    def test_header_size_matches_registry(self) -> None:
        """WAV 头里记录的帧数要对得上采样序列长度（防止半截写入）。"""
        path = sound_synth.write_wav(OUT_DIR / "probe.wav", "bowl")
        with wave.open(str(path)) as handle:
            self.assertEqual(handle.getnframes(), len(sound_synth.render("bowl")))


if __name__ == "__main__":
    unittest.main()
