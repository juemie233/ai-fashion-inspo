"""批量高级编辑测试：四类规则、dry-run 预览、冲突转合并、历史与回滚。"""

from app.database import async_session
from app.services.tag_history_service import rollback_history


def _create_tag(client, name: str, category: str = "free") -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def _tag_names(client) -> list[str]:
    return [t["name"] for g in client.get("/api/tags").json() for t in g["tags"]]


def test_regex_replace_dry_run_and_execute(client):
    """正则查找替换：dry-run 预览与执行结果一致；撞名自动转合并。"""
    _create_tag(client, "白色毛衣", "item_type")
    _create_tag(client, "黑色毛衣", "item_type")
    target = _create_tag(client, "毛衣", "item_type")  # 已存在的合并目标

    rule = {
        "type": "regex_replace",
        "pattern": "^(白色|黑色)毛衣$",
        "replacement": "毛衣",
        "scope": {"category": "item_type"},
    }
    # dry-run：两条都判定为冲突合并
    r = client.post("/api/tags/batch-edit", json={"dry_run": True, "rules": [rule]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["dry_run"] is True
    assert data["summary"]["merged"] == 2
    for item in data["preview"]:
        assert item["action"] == "merge"
        assert item["conflict"] is True
        assert item["target"]["id"] == target["id"]

    # 执行
    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": [rule]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["dry_run"] is False
    assert data["summary"]["merged"] == 2
    assert data["batch_id"]

    names = _tag_names(client)
    assert names.count("毛衣") == 1  # 合并后只剩目标一个
    assert "白色毛衣" not in names and "黑色毛衣" not in names

    # 历史：merge 记录共享 batch_id
    history = client.get("/api/tags/history", params={"batch_id": data["batch_id"]}).json()
    assert len(history["items"]) == 2
    assert all(h["operation"] == "merge" for h in history["items"])


async def test_regex_merge_creates_target_when_missing(client):
    """regex_merge 目标不存在 → 降级为改名（创建目标）。"""
    _create_tag(client, "白色毛衣", "item_type")
    _create_tag(client, "半身裙", "item_type")

    rule = {
        "type": "regex_merge",
        "pattern": "^(.+)毛衣$",
        "target_template": "$1",
        "scope": {"search": "毛衣"},
    }
    r = client.post("/api/tags/batch-edit", json={"dry_run": True, "rules": [rule]})
    data = r.json()
    assert data["summary"]["renamed"] == 1
    assert data["preview"][0]["action"] == "rename"
    assert data["preview"][0]["to"] == "白色"

    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": [rule]})
    assert r.status_code == 200
    names = _tag_names(client)
    assert "白色" in names and "白色毛衣" not in names


async def test_affix_and_normalize_rules(client):
    """前后缀增删 + 格式归一化规则生效。"""
    _create_tag(client, "简约", "style")
    _create_tag(client, "Ａ字裙", "item_type")  # 全角 Ａ

    r = client.post(
        "/api/tags/batch-edit",
        json={
            "dry_run": True,
            "rules": [
                {"type": "affix", "mode": "add_suffix", "text": "风",
                 "scope": {"category": "style"}},
                {"type": "normalize", "ops": ["fullwidth_to_halfwidth", "trim"],
                 "scope": {"search": "字裙"}},
            ],
        },
    )
    data = r.json()
    assert data["summary"]["renamed"] == 2

    r = client.post(
        "/api/tags/batch-edit",
        json={
            "dry_run": False,
            "rules": [
                {"type": "affix", "mode": "add_suffix", "text": "风",
                 "scope": {"category": "style"}},
                {"type": "normalize", "ops": ["fullwidth_to_halfwidth", "trim"],
                 "scope": {"search": "字裙"}},
            ],
        },
    )
    assert r.status_code == 200
    names = _tag_names(client)
    assert "简约风" in names and "A字裙" in names


async def test_batch_edit_writes_history_and_rollback(client):
    """执行写 batch_edit 历史；回滚恢复原名称。"""
    tag = _create_tag(client, "批编甲")
    rule = {
        "type": "affix",
        "mode": "remove_suffix",
        "text": "甲",
        "scope": {"tag_ids": [tag["id"]]},
    }
    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": [rule]})
    data = r.json()
    assert "批编" in _tag_names(client)

    history = client.get("/api/tags/history", params={"batch_id": data["batch_id"]}).json()
    assert history["total"] == 1
    row = history["items"][0]
    assert row["operation"] == "batch_edit"
    assert row["before"][str(tag["id"])]["name"] == "批编甲"
    assert row["after"][str(tag["id"])]["name"] == "批编"

    async with async_session() as db:
        await rollback_history(db, row["id"])
    assert "批编甲" in _tag_names(client)


def test_batch_edit_invalid_regex_400(client):
    """正则无效 → 400，不执行任何修改。"""
    rule = {
        "type": "regex_replace",
        "pattern": "([未闭合",
        "replacement": "x",
        "scope": {},
    }
    r = client.post("/api/tags/batch-edit", json={"dry_run": False, "rules": [rule]})
    assert r.status_code == 400
    assert "正则表达式无效" in r.json()["detail"]


def test_batch_edit_empty_rules_400(client):
    """无规则 → 400。"""
    r = client.post("/api/tags/batch-edit", json={"dry_run": True, "rules": []})
    assert r.status_code == 400


def test_batch_edit_intrabatch_collision(client):
    """批内撞名：两条规则让两个标签改成同名 → 后者合并进前者。"""
    a = _create_tag(client, "白T恤", "item_type")
    _create_tag(client, "黑T恤", "item_type")
    rule = {
        "type": "regex_replace",
        "pattern": "^(白|黑)T恤$",
        "replacement": "T恤",
        "scope": {"category": "item_type"},
    }
    r = client.post("/api/tags/batch-edit", json={"dry_run": True, "rules": [rule]})
    data = r.json()
    assert data["summary"]["merged"] == 1
    assert data["summary"]["renamed"] == 1
    # 先改的「白T恤」→ 改名 T恤；后到的「黑T恤」→ 合并进 T恤
    merged_item = next(i for i in data["preview"] if i["action"] == "merge")
    assert merged_item["target"]["id"] == a["id"]
