"""EyeRest 全局 QSS（V0.6 暖色版）。

只写跨页通用的基础控件外观（QPushButton 默认态、QScrollBar、
QGroupBox/QToolTip 等）。各页面特有外观写在各自模块内。
"""

from __future__ import annotations

from . import tokens

GLOBAL_QSS = f"""
* {{
    font-family: {tokens.FONT_FAMILY};
    color: {tokens.TEXT_PRIMARY};
    background-color: transparent;
}}

QMainWindow, QWidget#centralWidget {{
    background-color: {tokens.BG_CANVAS};
}}

/* ---- QScrollBar（细窄暖色） ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {tokens.BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {tokens.TEXT_MUTED};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: none; height: 0;
}}
QScrollBar:horizontal {{
    background: transparent; height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {tokens.BORDER};
    border-radius: 5px; min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    background: none; width: 0;
}}

/* ---- QToolTip ---- */
QToolTip {{
    background-color: {tokens.TEXT_PRIMARY};
    color: {tokens.TEXT_INVERTED};
    border: none;
    border-radius: {tokens.RADIUS_SM};
    padding: 6px 10px;
    font-size: {tokens.CAPTION}px;
}}

/* ---- QMenu（系统托盘菜单） ---- */
QMenu {{
    background-color: {tokens.BG_SURFACE};
    border: 1px solid {tokens.BORDER};
    border-radius: {tokens.RADIUS_MD};
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 22px;
    border-radius: {tokens.RADIUS_SM};
    color: {tokens.TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background-color: {tokens.PRIMARY_SOFT};
    color: {tokens.PRIMARY_TEXT};
}}
QMenu::separator {{
    height: 1px;
    background: {tokens.BORDER_SOFT};
    margin: 4px 8px;
}}
"""
