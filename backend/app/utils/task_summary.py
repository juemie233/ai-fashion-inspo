"""任务 result 的列表摘要（2026-11-03 评审 P1-2/P2-6）。

问题：``/api/tasks`` 列表原先取 ``substr(result, 1, 8192)``，对超长 JSON 前缀
``json.loads`` 必然失败 → 147 条历史任务的标题/完成摘要整条变空（摘要字段本身
很小，但整条 result 解析不了）。

方案：写入时生成 ``result_summary`` 小 JSON（模型 before_insert/before_update
监听器），列表只读摘要列；详情接口仍返回完整 result。摘要规则：
- result 本身 ≤ 预算：原样保存（行为与旧版完全一致）；
- 超预算：递归截断字符串/列表/字典，保留前端的标题与摘要字段；
- 仍超预算：按顶层 key 体积从大到小丢弃非优先字段，保证可解析且足够小。
"""

from __future__ import annotations

import json
from typing import Any

"""摘要 JSON 的字符预算（与旧列表接口的 8KB 前缀上限一致）。"""
SUMMARY_LIMIT = 8192
"""单个字符串字段最多保留的字符数。"""
STRING_LIMIT = 500
"""列表最多保留的元素数。"""
LIST_LIMIT = 10
"""字典最多保留的 key 数。"""
DICT_LIMIT = 40

"""前端任务标题/完成摘要/运行阶段文案确实读取的顶层字段（超预算时优先保留）。"""
PRIORITY_KEYS = frozenset(
    {
        "done",
        "text_done",
        "image_done",
        "text_failed",
        "image_skipped",
        "text_skipped",
        "files_deleted",
        "freed_bytes",
        "groups_processed",
        "deleted_count",
        "approved",
        "rejected",
        "pending",
        "failed",
        "ai_generated",
        "updated",
        "douyin_updated",
        "douyin_ids_filled",
        "skipped",
        "fetch_mode",
        "notices",
        "stage",
        "current",
        "count",
        "processed",
        "total",
        "download_progress",
        "collection",
        "fetch",
        "collect_progress",
        "plan",
        "import",
        "bloggers",
        "selection_required",
        "scope_empty",
        "message",
        "results",
        "generated",
        "errors",
    }
)


def _trim(value: Any, depth: int = 0) -> Any:
    """递归截断 JSON 值，保留结构与摘要字段。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= STRING_LIMIT else value[:STRING_LIMIT] + "…"
    if isinstance(value, list):
        return [_trim(item, depth + 1) for item in value[:LIST_LIMIT]]
    if isinstance(value, dict):
        trimmed: dict[str, Any] = {}
        for key, item in list(value.items())[:DICT_LIMIT]:
            trimmed[str(key)] = _trim(item, depth + 1)
        return trimmed
    return str(value)[:STRING_LIMIT]


def _serialized_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def summarize_result(
    result: dict[str, Any] | None, *, limit: int = SUMMARY_LIMIT
) -> dict[str, Any] | None:
    """生成列表用 result 摘要；空结果返回 None。"""
    if not isinstance(result, dict) or not result:
        return None
    if _serialized_len(result) <= limit:
        return result
    trimmed = _trim(result)
    if _serialized_len(trimmed) <= limit:
        return trimmed
    # 仍超预算：从体积最大的非优先 key 开始丢，优先字段一个都不丢
    keep = dict(trimmed)
    while _serialized_len(keep) > limit:
        candidates = [key for key in keep if key not in PRIORITY_KEYS]
        if not candidates:
            break
        drop = max(candidates, key=lambda key: _serialized_len(keep[key]))
        keep.pop(drop, None)
    if _serialized_len(keep) <= limit:
        return keep
    # 优先字段自己就超预算（极罕见）：只保留一个可解析的截断标记，避免列表 500
    return {"_truncated": True, "message": "结果过大，完整内容见任务详情"}
