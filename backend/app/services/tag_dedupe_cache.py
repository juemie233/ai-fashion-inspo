"""标签重复扫描缓存。

标签数据极少变更，结果可长期缓存。缓存 key = SHA256(tags_last_modified + threshold)。
"""

import hashlib
from typing import Any

# 简单内存缓存（单实例足够，无需 Redis）
_cache: dict[str, dict[str, Any]] = {}


def compute_cache_key(tags_last_modified: str, threshold: float) -> str:
    """基于标签最后修改时间和阈值生成缓存 key。"""
    raw = f"{tags_last_modified}:{threshold:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_cached(key: str) -> dict[str, Any] | None:
    """读取缓存，命中返回 {pairs, total, computed_at}，未命中返回 None。"""
    entry = _cache.get(key)
    if entry:
        return dict(entry)
    return None


def set_cached(key: str, pairs: list[dict], total: int) -> None:
    """写入缓存。"""
    from datetime import datetime, timezone
    _cache[key] = {
        "pairs": pairs,
        "total": total,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def clear_all() -> None:
    """清除全部缓存（标签变更时调用）。"""
    _cache.clear()
