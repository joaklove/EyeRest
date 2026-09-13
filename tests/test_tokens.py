"""设计 token 真源的一致性守卫。

这些断言不是"测试常量等于常量"，而是守三类**真实踩过的坑**：

1. 遮罩 alpha 写成 Qt 惯用的 0-255 整数 —— QSS 合法、**CSS 非法**，浏览器会把
   alpha clamp 成 1，设计板里遮罩变纯黑，设计源与实现静默漂移。
2. 按钮几何回到手写字面值（历史上存在 r6 / r8 / pad 10×26 / pad 8×18 三套）。
3. 已修过的硬编码色被重新写回代码（`#1976d2` 等）。
4. 真卡与编辑预览各自维护一份配色 —— 改了一边忘了另一边，同一张卡出现两种样子。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from app.ui.theme import tokens

ROOT = Path(__file__).resolve().parents[1]

#: 历史上出现过、已改为 token 驱动的硬编码色，不得重新出现
LEGACY_LITERALS = ("#1976d2", "#1565c0", "#eaf4ff", "rgba(120, 124, 134")


class TestOverlayPalette(unittest.TestCase):
    """遮罩调色板（PositionEditor）。"""

    def test_alpha_is_css_valid(self) -> None:
        """alpha 必须是 0-1 小数：0-255 整数在 CSS 侧会被 clamp 成 1。"""
        for name, value in (
            ("OVERLAY_SCRIM", tokens.OVERLAY_SCRIM),
            ("OVERLAY_TIP_BG", tokens.OVERLAY_TIP_BG),
        ):
            m = re.fullmatch(
                r"rgba\((\d+), (\d+), (\d+), ([\d.]+)\)", value
            )
            self.assertIsNotNone(m, f"{name} 不是规范 rgba() 字符串：{value!r}")
            alpha = float(m.group(4))  # type: ignore[union-attr]
            self.assertGreater(alpha, 0.0, f"{name} alpha 不能为 0")
            self.assertLessEqual(alpha, 1.0, f"{name} alpha 必须 <= 1（CSS 口径）")

    def test_string_and_components_agree(self) -> None:
        """字符串与分量同源，不允许各自维护。"""
        self.assertEqual(tokens.OVERLAY_SCRIM, tokens.rgba(tokens.OVERLAY_SCRIM_RGBA))
        self.assertEqual(tokens.OVERLAY_TIP_BG, tokens.rgba(tokens.OVERLAY_TIP_BG_RGBA))

    def test_to_qcolor_roundtrip(self) -> None:
        """to_qcolor 还原出的分量与 token 一致（QColor 不认 CSS rgba() 语法）。"""
        for rgba_value in (tokens.OVERLAY_SCRIM_RGBA, tokens.OVERLAY_TIP_BG_RGBA):
            color = tokens.to_qcolor(rgba_value)
            self.assertTrue(color.isValid())
            r, g, b, a = rgba_value
            got_r, got_g, got_b, got_a = color.getRgbF()
            self.assertAlmostEqual(got_r, r / 255.0, places=3)
            self.assertAlmostEqual(got_g, g / 255.0, places=3)
            self.assertAlmostEqual(got_b, b / 255.0, places=3)
            self.assertAlmostEqual(got_a, a, places=3)


class TestCueCardTokens(unittest.TestCase):
    """提示卡配色（真卡 + 编辑预览共用同一份）。"""

    def test_alpha_is_css_valid(self) -> None:
        """alpha 必须是 0-1 小数（QSS 惯用的 240 / 90 在 CSS 里会被 clamp）。"""
        for name, value in (
            ("CUE_CARD_BG", tokens.CUE_CARD_BG),
            ("CUE_CARD_BORDER", tokens.CUE_CARD_BORDER),
        ):
            m = re.fullmatch(r"rgba\((\d+), (\d+), (\d+), ([\d.]+)\)", value)
            self.assertIsNotNone(m, f"{name} 不是规范 rgba() 字符串：{value!r}")
            alpha = float(m.group(4))  # type: ignore[union-attr]
            self.assertGreater(alpha, 0.0, f"{name} alpha 不能为 0")
            self.assertLessEqual(alpha, 1.0, f"{name} alpha 必须 <= 1（CSS 口径）")

    def test_string_and_components_agree(self) -> None:
        self.assertEqual(tokens.CUE_CARD_BG, tokens.rgba(tokens.CUE_CARD_BG_RGBA))
        self.assertEqual(
            tokens.CUE_CARD_BORDER, tokens.rgba(tokens.CUE_CARD_BORDER_RGBA)
        )

    def test_preview_has_no_private_palette(self) -> None:
        """编辑预览不得自持配色 —— 只能引用 visual_cue 的共享样式表。

        回归背景：预览曾持有 ``rgba(28, 32, 44, 235)`` 深色底 +
        ``rgba(120, 200, 255, 90)`` 蓝边，暖色改版后与真卡不一致。
        """
        src = (ROOT / "app/ui/visual_cue_preview.py").read_text(encoding="utf-8")
        self.assertIn("cue_card_stylesheet", src, "预览未引用共享样式表")
        self.assertNotIn("rgba(", src, "预览里出现私有的 rgba() 配色")
        self.assertNotIn("cuePreviewCard", src, "预览仍用私有对象名，共享样式表会失效")

    def test_popup_uses_shared_stylesheet(self) -> None:
        src = (ROOT / "app/ui/visual_cue.py").read_text(encoding="utf-8")
        self.assertIn("def cue_card_stylesheet", src, "共享样式表函数缺失")
        self.assertNotIn("rgba(255, 250, 242", src, "真卡配色未走 token")


class TestButtonGeometry(unittest.TestCase):
    """按钮几何统一（2026-09-13 裁定：r16 + pad 12×24）。"""

    def test_button_radius_is_16(self) -> None:
        self.assertEqual(tokens.RADIUS_BUTTON, 16)

    def test_button_padding_is_12_by_24(self) -> None:
        self.assertEqual(tokens.BUTTON_PAD_V, 12)
        self.assertEqual(tokens.BUTTON_PAD_H, 24)
        # 必须是间距体系内的值，不能是新的魔法数
        self.assertIn(tokens.BUTTON_PAD_V, (tokens.SPACE_1, tokens.SPACE_2, tokens.SPACE_3,
                                            tokens.SPACE_4, tokens.SPACE_5, tokens.SPACE_6,
                                            tokens.SPACE_8, tokens.SPACE_10))
        self.assertIn(tokens.BUTTON_PAD_H, (tokens.SPACE_1, tokens.SPACE_2, tokens.SPACE_3,
                                            tokens.SPACE_4, tokens.SPACE_5, tokens.SPACE_6,
                                            tokens.SPACE_8, tokens.SPACE_10))

    def test_source_uses_tokens_not_literals(self) -> None:
        """代码里的按钮/遮罩样式不得残留历史硬编码色。

        注：这里只扫**颜色**字面值 —— 圆角/内边距是否真的走 token，改由
        ``test_position_editor`` / ``test_settings_page`` 读控件样式表校验，
        因为同为 ``border-radius`` 的组合框、滑块、小尺寸"试听"胶囊本来
        就有各自的合法取值，用文本扫描会误判。
        """
        for rel in ("app/ui/position_editor.py", "app/ui/settings.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for literal in LEGACY_LITERALS:
                self.assertNotIn(
                    literal, src, f"{rel} 残留硬编码 {literal}，应改为 token"
                )


if __name__ == "__main__":
    unittest.main()
