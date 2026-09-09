"""插画资产加载：SVG -> QIcon/QPixmap。

所有插画位于 ``app/assets/illustrations/``，打包后随 datas 落在
``_MEIPASS/app/assets/illustrations/``，经
:func:`app.utils.system.get_resource_path` 统一解析。

用法::

    from app.ui.theme import assets
    label.setPixmap(assets.pixmap("icon_blink", 28))
    btn.setIcon(assets.icon("nav_home", 18))
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from app.utils.system import get_resource_path

_ILLUSTRATION_DIR = "app/assets/illustrations"


@lru_cache(maxsize=64)
def _renderer(name: str) -> Optional[QSvgRenderer]:
    """按名称加载 SVG 渲染器（带缓存）；文件缺失返回 None。"""
    path = get_resource_path(f"{_ILLUSTRATION_DIR}/{name}.svg")
    if not path.exists():
        return None
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return None
    return renderer


def pixmap(name: str, size: int, device_ratio: float = 2.0) -> QPixmap:
    """渲染插画为高清 QPixmap（默认按 2x 设备像素渲染保证清晰度）。"""
    result = QPixmap(int(size * device_ratio), int(size * device_ratio))
    result.fill(Qt.GlobalColor.transparent)
    renderer = _renderer(name)
    if renderer is not None:
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
    result.setDevicePixelRatio(device_ratio)
    return result


def icon(name: str, size: int = 24) -> QIcon:
    """渲染插画为 QIcon（含多尺寸，菜单/按钮通用）。"""
    ico = QIcon()
    for px in (16, 24, 32, 48):
        ico.addPixmap(pixmap(name, px))
    return ico


def has(name: str) -> bool:
    """资产是否存在（测试与降级判断用）。"""
    return _renderer(name) is not None
