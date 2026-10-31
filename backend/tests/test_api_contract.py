"""前后端字段契约快照：后端响应字段一旦改名/删除，这里先红。

**为什么需要**：`vue-tsc` 只校验前端自己写的类型——后端把 `total` 改成 `count`，
前端 mock 不变、类型检查照过、测试照绿，线上表现是「列表共 0 条」这类静默错误，
要等到用户点开页面才发现。这条用例把「接口响应字段」变成**显式契约**：

1. 从 `app.openapi()` 取关键接口的响应字段集合（跟真实 app 同一份定义，不手写）；
2. 与 `tests/fixtures/api_contract.json` 里提交的快照逐字段比对；
3. 字段增删都会失败：**改名/删除**要同步改前端并在提交信息里说明（快照随之更新）。

只收录**前端实际消费**的接口（列表/详情/任务/标签/合集/出图/人物），不是全量——
全量快照会因每个新接口都要更新而失去意义。

更新快照（改完后端并已同步前端时）：

    cd backend && UPDATE_API_CONTRACT=1 py -3.12 -m pytest tests/test_api_contract.py
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

SNAPSHOT = Path(__file__).parent / "fixtures" / "api_contract.json"

# 关键接口：method + path → 只关心 200 响应的字段集合
# path 用 OpenAPI 里的模板形式（{inspiration_id} 等）
WATCHED: list[tuple[str, str]] = [
    ("get", "/api/inspirations"),
    ("get", "/api/inspirations/{inspiration_id}"),
    ("get", "/api/inspirations/trash"),
    ("get", "/api/tasks"),
    ("get", "/api/tasks/{task_id}"),
    ("get", "/api/tags"),
    ("get", "/api/search"),
    ("get", "/api/collections"),
    ("get", "/api/bloggers"),
]

# 前端在消费、但后端**没有 response_model**（返回自由 dict）的接口。
# 这些接口的响应字段无法从 OpenAPI 校验——列在这里让缺口可见，而不是假装测过了。
# 补上 response_model 后把它从本清单移到 WATCHED 即可获得契约保护。
UNMODELED_ALLOWLIST: set[tuple[str, str]] = {
    ("get", "/api/tags/top"),
    ("get", "/api/search/suggestions"),
    ("get", "/api/generate/runs"),
    ("get", "/api/generate/runs/{run_id}"),
    ("get", "/api/admin/stats"),
    ("get", "/api/tags/stats"),
    ("get", "/api/ai/quality-stats"),
}


def _schema() -> dict[str, Any]:
    from app.main import app

    return app.openapi()


def _resolve(spec: dict[str, Any], node: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 $ref 展开成对象 schema；自由 dict / 无 properties 的一律返回 None。"""
    if not node:
        return None
    if "$ref" in node:
        name = node["$ref"].split("/")[-1]
        return _resolve(spec, spec["components"]["schemas"].get(name))
    properties = node.get("properties")
    if isinstance(properties, dict) and properties:
        return node
    return None


def _response_fields(spec: dict[str, Any], method: str, path: str) -> list[str]:
    """取某接口 200 响应体的**顶层字段名**（分页对象取 items/total/…，数组取元素字段）。

    解析不出来（`additionalProperties: true` 的自由 dict、或没有 response_model）时返回 []。
    这类接口无法做字段契约检查，由 `UNMODELED_ALLOWLIST` 显式登记为缺口。
    """
    operation = spec["paths"].get(path, {}).get(method)
    assert operation, f"OpenAPI 里找不到 {method.upper()} {path}（接口被删或改名了？）"

    content = operation.get("responses", {}).get("200", {}).get("content", {})
    media = content.get("application/json") or next(iter(content.values()), None)
    assert media, f"{method.upper()} {path} 没有 200 JSON 响应定义"
    schema = media.get("schema") or {}

    # 数组响应：字段在 items 里
    if schema.get("type") == "array" or "items" in schema:
        node = _resolve(spec, schema.get("items"))
    else:
        node = _resolve(spec, schema)

    if not node:
        return []
    return sorted(node["properties"].keys())


def _collect() -> dict[str, list[str]]:
    spec = _schema()
    out: dict[str, list[str]] = {}
    for method, path in WATCHED:
        out[f"{method.upper()} {path}"] = _response_fields(spec, method, path)
    return out


def test_api_contract_matches_snapshot() -> None:
    """关键接口的响应字段必须与快照一致（增删都要显式确认）。"""
    current = _collect()

    if os.environ.get("UPDATE_API_CONTRACT") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"已更新契约快照（{len(current)} 个接口）")

    assert SNAPSHOT.exists(), (
        f"缺少契约快照 {SNAPSHOT}；先跑 "
        "`UPDATE_API_CONTRACT=1 py -3.12 -m pytest tests/test_api_contract.py` 生成"
    )
    saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))

    assert set(current) == set(saved), (
        "监控的接口集合变了（新增/删除了 WATCHED 条目），请更新快照：\n"
        f"  仅当前有: {sorted(set(current) - set(saved))}\n"
        f"  仅快照有: {sorted(set(saved) - set(current))}"
    )

    diffs: list[str] = []
    for endpoint in sorted(current):
        now, before = set(current[endpoint]), set(saved[endpoint])
        removed = sorted(before - now)
        added = sorted(now - before)
        if removed:
            diffs.append(
                f"  {endpoint} 少了字段 {removed} —— 前端若有引用会静默拿不到值"
                "（改名算删除 + 新增），请同步改前端并更新快照"
            )
        if added:
            diffs.append(f"  {endpoint} 新增字段 {added}（确认前端需要后更新快照）")

    assert not diffs, "接口响应字段与契约快照不一致：\n" + "\n".join(diffs)


def test_watched_endpoints_all_resolve() -> None:
    """监控清单里的每个接口都能解析出字段（防止加了个没有响应模型的接口）。"""
    spec = _schema()
    for method, path in WATCHED:
        fields = _response_fields(spec, method, path)
        assert fields, f"{method.upper()} {path} 没解析出响应字段（是否缺少 response_model？）"


def test_unmodeled_allowlist_is_accurate() -> None:
    """缺口清单必须准确：这些接口当前确实没有响应模型。

    一旦某个接口补上了 response_model（好事），这条就会失败，提示把它移进 WATCHED
    以获得字段契约保护——避免「补了模型但契约测试仍然没覆盖」的假安全感。
    """
    spec = _schema()
    actually_typed = []
    for method, path in UNMODELED_ALLOWLIST:
        try:
            fields = _response_fields(spec, method, path)
        except AssertionError:
            fields = []
        if fields:
            actually_typed.append(f"{method.upper()} {path}")

    assert not actually_typed, (
        "这些接口已经有响应模型了，请从 UNMODELED_ALLOWLIST 移到 WATCHED："
        + "、".join(actually_typed)
    )
