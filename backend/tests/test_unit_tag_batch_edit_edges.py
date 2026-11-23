"""标签批量高级编辑（tag_batch_edit）的直连边缘单测。

既有 tests/test_tag_batch_edit.py 走 API 覆盖了「正则替换 / 正则合并降级改名 /
前后缀 / 历史回滚」的主路径；本文件改**直接 await 服务函数**（portal 线程不计覆盖），
补齐规则引擎的纯函数分支、scope 并集、批内撞名转合并、执行期合并失败汇总、
空规则/非法正则的错误类型，以及 dry-run 不落库。
"""

import re

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.tag import Tag
from app.models.tag_history import TagHistory
from app.services import tag_batch_edit as svc


# ═══════════════════════════════════════════════════════════════
#  纯函数：归一化 / 前后缀 / 模板展开
# ═══════════════════════════════════════════════════════════════


def test_normalize_name_ops_matrix():
    """fullwidth_to_halfwidth / trim / dedup_chars 各自生效且按顺序叠加。"""
    assert svc._normalize_name("ＡＢＣ１２３", ["fullwidth_to_halfwidth"]) == "ABC123"
    assert svc._normalize_name("  白色  ", ["trim"]) == "白色"
    assert svc._normalize_name("白白白色", ["dedup_chars"]) == "白色"
    # 去重后再去空白：顺序体现「管道」语义
    assert svc._normalize_name("  白 白 色 ", ["trim", "dedup_chars"]) == "白 白 色"
    assert svc._normalize_name("无操作", []) == "无操作"
    assert svc._normalize_name("未知操作", ["not-an-op"]) == "未知操作"


def test_apply_affix_modes_and_empty_text():
    """四种前后缀模式；text 为空/不匹配时原样返回（不能误删字符）。"""
    assert svc._apply_affix("毛衣", "add_prefix", "白色") == "白色毛衣"
    assert svc._apply_affix("毛衣", "add_suffix", "穿搭") == "毛衣穿搭"
    assert svc._apply_affix("白色毛衣", "remove_prefix", "白色") == "毛衣"
    assert svc._apply_affix("白色毛衣", "remove_suffix", "毛衣") == "白色"
    # 空文本：加/删都应是恒等
    for mode in ("add_prefix", "remove_prefix", "add_suffix", "remove_suffix"):
        assert svc._apply_affix("白色", mode, "") == "白色"
    # 前后缀不匹配：不能截断
    assert svc._apply_affix("白色", "remove_prefix", "黑色") == "白色"
    assert svc._apply_affix("白色", "remove_suffix", "毛衣") == "白色"
    assert svc._apply_affix("白色", "unknown-mode", "x") == "白色"


def test_expand_target_template_syntaxes():
    """合并目标模板兼容 $1 / ${1} / Python \\1 与空模板。"""
    m = re.search(r"^(.+)毛衣$", "白色毛衣")
    assert m is not None
    assert svc._expand_target_template("$1", m) == "白色"
    assert svc._expand_target_template("${1}", m) == "白色"
    assert svc._expand_target_template(r"\1", m) == "白色"
    assert svc._expand_target_template("", m) == ""


# ═══════════════════════════════════════════════════════════════
#  scope / 规则管道
# ═══════════════════════════════════════════════════════════════


async def _tag(name: str, category: str = "item_type", source: str = "seed") -> Tag:
    async with async_session() as db:
        tag = Tag(name=name, category=category, source=source)
        db.add(tag)
        await db.commit()
        return tag


async def test_resolve_scope_union(client):
    """tag_ids / category / source / search 四种 scope 取并集。"""
    white = await _tag("白色毛衣", "item_type", "seed")
    black = await _tag("黑色毛衣", "item_type", "ai_generated")
    skirt = await _tag("半身裙", "category_other", "ai_generated")

    async with async_session() as db:
        by_ids = await svc._resolve_scope_tags(db, {"tag_ids": [white.id]})
        by_category = await svc._resolve_scope_tags(db, {"category": "item_type"})
        by_source = await svc._resolve_scope_tags(db, {"source": "ai_generated"})
        by_search = await svc._resolve_scope_tags(db, {"search": "裙"})
        everything = await svc._resolve_scope_tags(db, {})

    assert [t.id for t in by_ids] == [white.id]
    assert {t.id for t in by_category} == {white.id, black.id}
    assert {t.id for t in by_source} == {black.id, skirt.id}
    assert [t.id for t in by_search] == [skirt.id]
    assert len(everything) == 3


def test_evaluate_rules_scope_skip_and_merge_terminates_pipeline():
    """不在规则 scope 内跳过；regex_merge 命中后终止管道；空/同名目标视为无效果。"""
    rules = [
        {"type": "affix", "mode": "add_suffix", "text": "√"},
        {"type": "regex_merge", "pattern": r"^(.+)毛衣$", "target_template": "$1"},
    ]
    # tag 1 只在第一条规则 scope 内 → 第二条不参与
    assert svc._evaluate_rules("白色毛衣", 1, rules, [{1}, {2}]) == ("白色毛衣√", "rename")
    # tag 2 命中合并规则（且先过了第一条 scope 外的规则）→ merge 目标「白色」
    assert svc._evaluate_rules("黑色毛衣", 2, rules, [{1}, {2}]) == ("黑色", "merge")

    # 模板展开为空 / 目标就是自己 → 无效果
    empty_target = [{"type": "regex_merge", "pattern": r"^(.+)毛衣$", "target_template": ""}]
    assert svc._evaluate_rules("白色毛衣", 1, empty_target, [{1}]) == (None, None)
    same_target = [
        {"type": "regex_merge", "pattern": r"^(.+毛衣)$", "target_template": "$1"}
    ]
    assert svc._evaluate_rules("白色毛衣", 1, same_target, [{1}]) == (None, None)

    # regex_merge 不命中 → continue 到下一条规则（改名仍生效）
    fallback = [
        {"type": "regex_merge", "pattern": r"^(.+)裤$", "target_template": "$1"},
        {"type": "affix", "mode": "add_prefix", "text": "长"},
    ]
    assert svc._evaluate_rules("白色毛衣", 1, fallback, [{1}, {1}]) == (
        "长白色毛衣",
        "rename",
    )


def test_build_plans_intrabatch_collision_and_skip():
    """批内撞名：后到者转合并；目标不存在时降级改名；无变化走 skip。"""
    tags = [Tag(id=1, name="白色", category="c"), Tag(id=2, name="白色毛衣", category="c")]
    # 规则把「白色毛衣」改成「白色」→ 与已有标签撞名 → merge
    plans = svc._build_plans(
        tags,
        [{"type": "regex_replace", "pattern": "毛衣$", "replacement": ""}],
        [{1, 2}],
        {"白色": 1, "白色毛衣": 2},
    )
    by_id = {p["tag_id"]: p for p in plans}
    assert by_id[1]["action"] == "skip"
    assert by_id[2]["action"] == "merge"
    assert by_id[2]["target_id"] == 1 and by_id[2]["conflict"] is True

    # regex_merge 目标已存在（但不在 scope 内）→ 同样 merge
    plans = svc._build_plans(
        [Tag(id=3, name="黑色毛衣", category="c")],
        [{"type": "regex_merge", "pattern": r"^(.+)毛衣$", "target_template": "$1"}],
        [{3}],
        {"黑色": 9},
    )
    assert plans == [
        {
            "tag_id": 3,
            "from": "黑色毛衣",
            "action": "merge",
            "to": "黑色",
            "target_id": 9,
            "conflict": True,
        }
    ]

    # regex_merge 目标不存在 → 降级 rename
    plans = svc._build_plans(
        [Tag(id=4, name="灰色毛衣", category="c")],
        [{"type": "regex_merge", "pattern": r"^(.+)毛衣$", "target_template": "$1"}],
        [{4}],
        {"灰色毛衣": 4},
    )
    assert [(p["action"], p["to"], p["target_id"]) for p in plans] == [("rename", "灰色", None)]


# ═══════════════════════════════════════════════════════════════
#  主入口：dry-run / 执行 / 错误汇总
# ═══════════════════════════════════════════════════════════════


async def test_batch_edit_no_rules_raises_value_error(client):
    async with async_session() as db:
        with pytest.raises(ValueError, match="至少提供一条规则"):
            await svc.batch_edit_tags(db, [])


async def test_batch_edit_invalid_regex_raises_value_error(client):
    async with async_session() as db:
        with pytest.raises(ValueError, match="正则表达式无效"):
            await svc.batch_edit_tags(
                db, [{"type": "regex_replace", "pattern": "([", "replacement": ""}]
            )


async def test_batch_edit_dry_run_does_not_write(client):
    await _tag("白色毛衣")
    async with async_session() as db:
        result = await svc.batch_edit_tags(
            db,
            [{"type": "affix", "mode": "add_suffix", "text": "√", "scope": {"search": "毛衣"}}],
            dry_run=True,
        )

    assert result["dry_run"] is True
    assert result["summary"] == {"renamed": 1, "merged": 0, "skipped": 0, "errors": 0}
    assert result["preview"] == [
        {
            "tag_id": result["preview"][0]["tag_id"],
            "from": "白色毛衣",
            "to": "白色毛衣√",
            "action": "rename",
            "conflict": False,
            "target": None,
        }
    ]
    async with async_session() as db:
        assert await db.get(Tag, result["preview"][0]["tag_id"]) is not None
        names = [t.name for t in (await db.execute(select(Tag))).scalars()]
        assert names == ["白色毛衣"]  # dry-run 不落库
        history = (await db.execute(select(TagHistory))).scalars().all()
        assert history == []


async def test_batch_edit_execute_writes_history(client):
    """执行：改名落库 + 一条 batch_edit 历史（共享 batch_id）。"""
    await _tag("白色毛衣")
    await _tag("黑色毛衣")

    async with async_session() as db:
        result = await svc.batch_edit_tags(
            db,
            [{"type": "affix", "mode": "add_prefix", "text": "新款_", "scope": {"search": "毛衣"}}],
            dry_run=False,
        )

    assert result["dry_run"] is False
    assert result["batch_id"].startswith("batch-edit-")
    assert result["summary"] == {"renamed": 2, "merged": 0, "skipped": 0, "errors": 0}
    assert result["errors"] == []

    async with async_session() as db:
        names = sorted(
            t.name for t in (await db.execute(select(Tag))).scalars()
        )
        assert names == ["新款_白色毛衣", "新款_黑色毛衣"]
        history = (await db.execute(select(TagHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].operation == "batch_edit"
        assert history[0].batch_id == result["batch_id"]


async def test_batch_edit_merge_failure_collected_not_raised(client, monkeypatch):
    """执行期单条合并失败：写进 errors 并计入 summary.errors，其余照常返回。"""
    await _tag("白色")
    source = await _tag("白色毛衣")

    import app.services.tag_crud as tag_crud

    async def _boom(_db, _source_id, _target_id, batch_id=None):
        raise RuntimeError("合并炸了")

    monkeypatch.setattr(tag_crud, "merge_tags", _boom)

    async with async_session() as db:
        result = await svc.batch_edit_tags(
            db,
            [
                {
                    "type": "regex_replace",
                    "pattern": "毛衣$",
                    "replacement": "",
                    "scope": {"search": "毛衣"},
                }
            ],
            dry_run=False,
        )

    assert result["summary"]["errors"] == 1
    assert result["errors"] == [{"tag_id": source.id, "message": "合并炸了"}]
    # 源标签未被删除（合并失败不阻断流程）
    async with async_session() as db:
        assert await db.get(Tag, source.id) is not None
