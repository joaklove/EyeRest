"""JSON 设置存储（V0.5 减法重构：替代原 SQLite SettingRepository）。

V1 不再引入数据库，所有用户配置持久化到数据目录下的 ``config.json``。
存储特点：

- **原子写入**：先写临时文件再 ``os.replace``，避免崩溃/断电写入半截 JSON
- **宽松读取**：文件损坏时回退到默认值并记录日志，绝不因此崩溃启动
- **类型无关**：值以 JSON 原生类型存储（int/bool/str/float），读取方负责类型

典型用法::

    store = get_settings_store()
    store.set("blink_cycle_seconds", 60)
    value = store.get("blink_cycle_seconds")
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.config import defaults
from app.utils.logger import get_logger

_log = get_logger(__name__)


class JsonSettingsStore:
    """基于单个 JSON 文件的设置存储。

    Args:
        path: 配置文件路径；默认使用数据目录下的 ``config.json``。
    """

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = get_config_path()
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()
        # 首次启动落盘一个空配置文件：让用户看得到、改得动 config.json；
        # 空对象语义 = 无任何覆盖，全部走内置默认值。
        if not self._path.exists():
            self._save()

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        """配置文件路径。"""
        return self._path

    # ------------------------------------------------------------------
    # 基础读写
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """读取设置；不存在时返回 ``default``。"""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """写入单个设置并立即持久化。"""
        self._data[key] = value
        self._save()

    def delete(self, key: str) -> None:
        """删除单个设置（使其回退到默认值）并持久化。"""
        if key in self._data:
            del self._data[key]
            self._save()

    def update(self, values: dict[str, Any]) -> None:
        """批量写入设置，只落盘一次。"""
        if not values:
            return
        self._data.update(values)
        self._save()

    def all(self) -> dict[str, Any]:
        """返回全部已存储设置的副本。"""
        return dict(self._data)

    def reset(self) -> None:
        """清空全部设置（下次读取回退到内置默认值）。"""
        self._data = {}
        self._save()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _load(self) -> None:
        """从磁盘加载配置；文件缺失或损坏时安全回退为空配置。"""
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    self._data = loaded
                else:
                    _log.warning("配置文件格式异常（非对象），已回退默认: %s", self._path)
                    self._data = {}
        except json.JSONDecodeError:
            _log.exception("配置文件解析失败，已回退默认: %s", self._path)
            self._data = {}
        except OSError:
            _log.exception("配置文件读取失败，已回退默认: %s", self._path)
            self._data = {}

    def _save(self) -> None:
        """原子写入配置文件。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 同目录创建临时文件，确保 os.replace 在同一分区原子生效
            fd, tmp_name = tempfile.mkstemp(
                prefix=".config-", suffix=".tmp", dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, self._path)
            except Exception:
                # 清理残留临时文件，避免污染数据目录
                try:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError:
            _log.exception("配置文件写入失败: %s", self._path)


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_store: JsonSettingsStore | None = None


def get_config_path() -> Path:
    """返回配置文件路径（数据目录下 ``config.json``）。"""
    from app.utils.system import get_app_data_dir

    return Path(get_app_data_dir()) / defaults.CONFIG_FILENAME


def get_settings_store() -> JsonSettingsStore:
    """返回全局设置存储单例。"""
    global _store
    if _store is None:
        _store = JsonSettingsStore()
    return _store


def reset_settings_store() -> None:
    """重置单例（测试用）。"""
    global _store
    _store = None
