"""时间工具：统一的 UTC 时间生成函数。

SQLite 的 DATETIME 列约定存 naive UTC 时间，因此项目各处的
``utcnow`` 实现完全一致（去 tzinfo 的当前 UTC 时间）。此前该函数
散落在 models / services / routers / worker 等 6 处，现收敛至此。
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime，用于 SQLite DATETIME 列）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
