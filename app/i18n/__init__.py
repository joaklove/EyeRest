"""i18n 模块：提供 ``tr()`` 翻译函数与语言切换接口。

用法::

    from app.i18n import tr, set_language, get_language, get_translator

    tr("nav.dashboard")                      # -> "主页" / "Dashboard"
    tr("common.postpone_minutes", minutes=5)  # -> "延迟 5 分钟" / "Postpone 5 min"
    set_language("en")                       # 切换语言并通知所有监听器
"""

from app.i18n.translator import (
    SUPPORTED_LANGUAGES,
    Translator,
    get_language,
    get_translator,
    set_language,
    tr,
)

__all__ = [
    "SUPPORTED_LANGUAGES",
    "Translator",
    "get_language",
    "get_translator",
    "set_language",
    "tr",
]
