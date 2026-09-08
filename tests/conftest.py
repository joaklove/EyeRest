"""pytest 全局配置（在所有测试模块导入前生效）。

硬约束：**任何测试都不得写入 C 盘**。

部分测试会真实调用 ``app.utils.system.get_app_data_dir()`` /
``get_log_dir()``（如 logger、统计、通知相关用例）。开发模式下这些
函数会回落到 ``%APPDATA%/EyeRest``（C 盘）。因此在导入任何 app 模块
之前，先把数据目录重定向到项目 ``.build/test_data/``（G 盘）。

个别需要验证目录解析优先级的用例（test_app_data_dir.py）会自行
``patch.dict`` 或弹出该环境变量，不受影响。
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATA_DIR = _ROOT / ".build" / "test_data"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("EYEREST_DATA_DIR", str(_TEST_DATA_DIR))
