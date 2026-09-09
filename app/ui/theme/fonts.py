"""品牌字体加载：站酷快乐体（OFL 开源协议，可免费商用）。

在 QApplication 创建后、首窗口显示前调用 :func:`load_fonts`。
加载成功后 tokens.FONT_DISPLAY 会被设置为字体的真实 family 名，
各页面标题/Hero 直接引用 tokens.FONT_DISPLAY 即可。
"""

from __future__ import annotations

from PySide6.QtGui import QFontDatabase

from app.utils.logger import get_logger
from app.utils.system import get_resource_path

logger = get_logger(__name__)

_FONT_FILE = "app/assets/fonts/ZCOOLKuaiLe-Regular.ttf"
_loaded = False


def load_fonts() -> None:
    """加载品牌字体到 Qt 字体数据库（幂等，失败静默降级系统字体）。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    _load()


def _load() -> None:
    from app.ui.theme import tokens

    path = get_resource_path(_FONT_FILE)
    if not path.exists():
        logger.warning("品牌字体文件缺失: %s", path)
        return
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id < 0:
        logger.warning("品牌字体加载失败: %s", path)
        return
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning("品牌字体无可用 family: %s", path)
        return
    tokens.FONT_DISPLAY = families[0]
    logger.info("品牌字体已加载: %s", families[0])
