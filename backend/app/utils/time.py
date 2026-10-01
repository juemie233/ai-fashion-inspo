"""时间工具：统一的 UTC 时间生成函数。

SQLite 的 DATETIME 列约定存 naive UTC 时间，因此项目各处的
``utcnow`` 实现完全一致（去 tzinfo 的当前 UTC 时间）。此前该函数
散落在 models / services / routers / worker 等 6 处，现收敛至此。
"""

from datetime import datetime, UTC


def utcnow() -> datetime:
    """返回当前 UTC 时间（naive datetime，用于 SQLite DATETIME 列）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def format_utc(dt: datetime | None) -> str | None:
    """将 naive UTC datetime 格式化为带 Z 后缀的 ISO 8601 字符串；None 返回 None。

    与项目各处的 `%Y-%m-%dT%H:%M:%SZ` 序列化口径统一收敛至此，避免多文件重复。
    """
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（**带 +00:00 时区**）。

    注意与 :func:`format_utc` 的口径不同（后者是 ``...Z``）：人脸模块的结果载荷
    （``photo_results.updated_at``）一直用 ``datetime.now(timezone.utc).isoformat()``，
    这里只是把两份相同实现收敛到一处，**刻意不改格式**——换口径属于行为变更。
    """
    return datetime.now(UTC).isoformat()
