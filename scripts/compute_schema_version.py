"""输出后端 compute_schema_version() 的值，供前端构建时注入。

前端 web/vite.config.ts 在 dev 启动 / build 时调用本脚本，把 schema 版本自动
注入为前端全局常量 __SCHEMA_VERSION__，从而消除「后端改了 db_migrations /
API_CONTRACT_VERSION 但前端硬编码常量未同步」导致的前后端版本不一致问题。

用法（任意目录执行均可）：
    python scripts/compute_schema_version.py
"""

import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径（与 scripts/backfill_vectors.py 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db_migrations import compute_schema_version  # noqa: E402

if __name__ == "__main__":
    print(compute_schema_version())
