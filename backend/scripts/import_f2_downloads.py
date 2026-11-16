"""f2 抖音下载目录 → 素材库的兼容入口。

实现已迁到应用侧 ``app.services.f2``；本文件只保留旧 CLI 路径与符号转发，
方便 ``python -m scripts.import_f2_downloads`` 和既有测试继续工作。
"""

import sys

from app.services.f2 import *  # noqa: F401,F403
from app.services.f2 import _run_with_timeout  # noqa: F401
from scripts.f2_cli import main

if __name__ == "__main__":
    sys.exit(main())
