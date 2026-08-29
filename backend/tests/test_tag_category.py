"""标签类别体系优化测试：AI 提取类别映射、迁移重分类规则、颜色剥离治理、
健康度扫描类别级统计。"""

import importlib.util
import pathlib

from app.database import async_session
from app.services.ai_tag_saver import iter_extracted_tags
from app.services.tag_color_strip import (
    _strip_color_prefix,
    apply_color_strip,
    build_color_prefixes,
    dry_run_color_strip,
)
from app.services.tag_health import scan_tag_health

# ── 迁移模块按路径加载（alembic versions 文件名含中文） ──
_MIG_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "alembic" / "versions"
    / "k5l6m7n8o9p0_标签类别体系优化_重命名与_body_part_重分类.py"
)
_spec = importlib.util.spec_from_file_location("mig_category_optimize", _MIG_PATH)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)


def _names_by_category(data: dict) -> dict[str, list[str]]:
    """把 iter_extracted_tags 的输出按类别聚合成 {category: [names]}。"""
    result: dict[str, list[str]] = {}
    for name, category, _conf in iter_extracted_tags(data):
        result.setdefault(category, []).append(name)
    return result


# ============ AI 提取类别映射 ============


def test_new_prompt_keys_mapped_to_new_categories():
    """新提示词键 design_detail / material 归入对应新类别。"""
    data = {
        "design_detail": ["泡泡袖", "荷叶边"],
        "material": ["针织", "蕾丝"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("design_detail") == ["泡泡袖", "荷叶边"]
    assert by_cat.get("material") == ["针织", "蕾丝"]


def test_legacy_keys_keep_backward_compat():
    """旧键兼容：wear_style→body_part（遗留），PascalCase 三键→snake_case 类别。"""
    data = {
        "wear_style": ["V领"],
        "Atmosphere": ["清新"],
        "Expression": ["微笑"],
        "Leg_Posture": ["交叉腿"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("body_part") == ["V领"]
    assert by_cat.get("atmosphere") == ["清新"]
    assert by_cat.get("expression") == ["微笑"]
    assert by_cat.get("leg_posture") == ["交叉腿"]


def test_default_prompt_contains_new_dimensions():
    """默认提示词包含新维度键名、颜色约束与长度约束（TAG_NAME_MAX_LENGTH 一致）。"""
    from app.config import _DEFAULT_AI_ANALYSIS_PROMPT

    assert '"design_detail"' in _DEFAULT_AI_ANALYSIS_PROMPT
    assert '"material"' in _DEFAULT_AI_ANALYSIS_PROMPT
    assert '"atmosphere"' in _DEFAULT_AI_ANALYSIS_PROMPT
    # 颜色不得写入单品名 + 复用优先原则
    assert "颜色一律写在" in _DEFAULT_AI_ANALYSIS_PROMPT
    assert "复用优先" in _DEFAULT_AI_ANALYSIS_PROMPT
    # 长度约束与旧键向后兼容说明
    assert "12" in _DEFAULT_AI_ANALYSIS_PROMPT
    assert "wear_style" in _DEFAULT_AI_ANALYSIS_PROMPT


# ============ 迁移重分类规则 ============


def test_migration_classify_material():
    """含面料词的 body_part 标签 → material。"""
    classify = _mig._classify_body_part
    assert classify("针织面料") == "material"
    assert classify("透肉") == "material"
    assert classify("弹性面料") == "material"
    assert classify("漆皮") == "material"
    assert classify("雪纺") == "material"


def test_migration_classify_design_detail():
    """含款式词的 body_part 标签 → design_detail（面料词优先级更高）。"""
    classify = _mig._classify_body_part
    assert classify("高腰设计") == "design_detail"
    assert classify("无口袋") == "design_detail"
    assert classify("尖头") == "design_detail"
    assert classify("泡泡袖") == "design_detail"
    # 命中多条规则时按 material → design_detail → fit 取第一个
    assert classify("针织泡泡袖") == "material"


def test_migration_classify_fit_and_unmatched():
    """含版型词的 → fit；都不命中的留在 body_part（返回 None）。"""
    classify = _mig._classify_body_part
    assert classify("贴身剪裁") == "fit"
    assert classify("紧身") == "fit"
    assert classify("A字版型") == "fit"
    assert classify("oversize") == "fit"
    assert classify("纯色") is None
    # 领结含款式词「领」→ design_detail；完全无关键词命中的留在 body_part
    assert classify("粉色领结") == "design_detail"
    assert classify("网红同款") is None


# ============ 颜色剥离治理 ============


def test_strip_color_prefix():
    """颜色前缀剥离：最长前缀优先；无命中/整名即颜色时返回原名。"""
    # 长词优先：「米白色」先于「白色」
    new, color = _strip_color_prefix("米白色短裙", ["白色", "米白色"])
    assert new == "短裙" and color == "米白色"
    new, color = _strip_color_prefix("白色短裙", ["白色", "米白色"])
    assert new == "短裙" and color == "白色"
    # 标签本身就是颜色词 → 不剥离
    new, color = _strip_color_prefix("白色", ["白色"])
    assert new == "白色" and color is None
    # 无颜色前缀
    new, color = _strip_color_prefix("尖头细跟高跟鞋", ["白色"])
    assert new == "尖头细跟高跟鞋" and color is None


def _create_tag(client, name: str, category: str) -> dict:
    r = client.post("/api/tags", json={"name": name, "category": category})
    assert r.status_code == 201, r.text
    return r.json()


def _link(client, insp_id: str, names: list[str]) -> None:
    r = client.post(f"/api/inspirations/{insp_id}/tags", json={"names": names})
    assert r.status_code == 200, r.text


async def test_color_strip_dry_run_and_apply(client, upload):
    """dry-run 只统计不写库；apply 合并撞名标签、重指向关联并补建颜色关联。"""
    # 目标标签（无颜色前缀）+ 两个带颜色前缀的标签 + 颜色标签
    target = _create_tag(client, "衬衫", "item_type")
    white = _create_tag(client, "白色衬衫", "item_type")
    black = _create_tag(client, "黑色衬衫", "item_type")
    _create_tag(client, "白色", "color")
    _create_tag(client, "黑色", "color")
    # 无颜色前缀的其它类别标签（不参与 item_type 治理）
    _create_tag(client, "米白色", "color")

    insp1 = upload().json()["id"]
    insp2 = upload().json()["id"]
    _link(client, insp1, ["白色衬衫", "衬衫"])  # insp1 已关联目标 → 双关联去重
    _link(client, insp2, ["黑色衬衫"])  # insp2 仅关联源 → 重指向

    # dry-run：不写库
    r = client.post("/api/tags/color-strip/dry-run", json={"category": "item_type"})
    assert r.status_code == 200, r.text
    dry = r.json()
    assert dry["merged"] == 2
    assert dry["renamed"] == 0
    assert dry["links_repointed"] == 1  # 仅 insp2 的关联需重指向
    assert dry["color_links_added"] == 2  # insp1←白色、insp2←黑色
    # 样例包含全部 2 条计划动作
    assert {s["action"] for s in dry["samples"]} == {"merge"}
    # dry-run 不写库：标签数不变
    groups = client.get("/api/tags").json()
    names_before = {t["name"] for g in groups for t in g["tags"]}
    assert {"白色衬衫", "黑色衬衫", "衬衫"} <= names_before

    # apply：合并 + 颜色关联补建
    r = client.post("/api/tags/color-strip/apply", json={"category": "item_type"})
    assert r.status_code == 200, r.text
    applied = r.json()
    assert applied["merged"] == 2
    assert applied["links_repointed"] == 1
    assert applied["color_links_added"] == 2
    assert applied["batch_id"]

    groups = client.get("/api/tags").json()
    tag_map = {
        t["name"]: t for g in groups for t in g["tags"]
    }
    # 源标签已删除，目标标签保留
    assert "白色衬衫" not in tag_map and "黑色衬衫" not in tag_map
    assert "衬衫" in tag_map

    # 关联已重指向且补建了颜色关联
    r = client.get(f"/api/inspirations/{insp1}")
    tags1 = {t["tag"]["name"] for t in r.json()["tags"]}
    assert "衬衫" in tags1 and "白色衬衫" not in tags1 and "白色" in tags1
    r = client.get(f"/api/inspirations/{insp2}")
    tags2 = {t["tag"]["name"] for t in r.json()["tags"]}
    assert "衬衫" in tags2 and "黑色衬衫" not in tags2 and "黑色" in tags2

    # 补建的颜色关联来源为 ai_generated（直接查关联表校验）
    from sqlalchemy import select

    from app.models.tag import InspirationTag, Tag

    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag.inspiration_id, InspirationTag.source)
                .join(Tag, Tag.id == InspirationTag.tag_id)
                .where(Tag.category == "color")
            )
        ).all()
    color_links = {iid: source for iid, source in rows}
    assert color_links[insp1] == "ai_generated"
    assert color_links[insp2] == "ai_generated"

    # 幂等：再跑一次无变化
    r = client.post("/api/tags/color-strip/apply", json={"category": "item_type"})
    assert r.json()["merged"] == 0
    assert r.json()["renamed"] == 0

    # 操作历史已记录（rename/merge 批次）
    r = client.get(
        "/api/tags/history", params={"batch_id": applied["batch_id"]}
    )
    assert r.status_code == 200
    assert r.json()["total"] >= 1


def test_color_strip_rename_and_skip(client, upload):
    """无撞名时走改名路径；跨类别撞名跳过。"""
    _create_tag(client, "白色", "color")
    _create_tag(client, "白色尖头细跟高跟鞋", "item_type")  # → 改名
    _create_tag(client, "衬衫", "body_part")  # 跨类别占用名
    _create_tag(client, "白色衬衫", "item_type")  # 目标被跨类别占用 → 跳过

    r = client.post("/api/tags/color-strip/dry-run", json={"category": "item_type"})
    assert r.status_code == 200, r.text
    dry = r.json()
    assert dry["renamed"] == 1
    assert dry["merged"] == 0
    assert dry["cross_category_skipped"] >= 1

    r = client.post("/api/tags/color-strip/apply", json={"category": "item_type"})
    applied = r.json()
    assert applied["renamed"] == 1

    groups = client.get("/api/tags").json()
    names = {t["name"] for g in groups for t in g["tags"]}
    assert "尖头细跟高跟鞋" in names and "白色尖头细跟高跟鞋" not in names
    # item_type 里的「尖头细跟高跟鞋」保留，颜色类别里的同名颜色词不受影响
    cats = {
        t["name"]: g["category"] for g in groups for t in g["tags"]
    }
    assert cats["尖头细跟高跟鞋"] == "item_type"


async def test_build_color_prefixes_longest_first(client):
    """颜色前缀词表按长度降序（长词优先匹配）。"""
    _create_tag(client, "米白色", "color")
    _create_tag(client, "白色", "color")
    async with async_session() as db:
        prefixes = await build_color_prefixes(db)
    assert prefixes.index("米白色") < prefixes.index("白色")
    assert prefixes == sorted(prefixes, key=len, reverse=True)


# ============ 健康度扫描类别级统计 ============


async def test_scan_category_stats(client, upload):
    """扫描结果含 category_stats：各指标口径正确。"""
    # style：2 个标签（法式 1 次使用 → 长尾，孤儿风格未使用）
    _create_tag(client, "法式", "style")
    _create_tag(client, "孤儿风格", "style")
    # item_type：2 个标签（短裙 1 次使用为长尾；衬衫 3 次使用非长尾）
    _create_tag(client, "短裙", "item_type")
    _create_tag(client, "衬衫", "item_type")
    insp1 = upload().json()["id"]
    insp2 = upload().json()["id"]
    insp3 = upload().json()["id"]
    _link(client, insp1, ["法式", "短裙", "衬衫"])
    _link(client, insp2, ["衬衫"])
    _link(client, insp3, ["衬衫"])

    async with async_session() as db:
        result = await scan_tag_health(db, duplicate_threshold=0.75)

    stats = result["category_stats"]
    assert stats["style"]["total"] == 2
    assert stats["style"]["used"] == 1
    assert stats["style"]["unused"] == 1
    assert stats["style"]["long_tail_rate"] == 0.5  # 法式恰好 1 次使用
    assert stats["style"]["top_share"] == 1.0  # 唯一使用的标签占全部使用次数

    assert stats["item_type"]["total"] == 2
    assert stats["item_type"]["used"] == 2
    assert stats["item_type"]["unused"] == 0
    assert stats["item_type"]["long_tail_rate"] == 0.5  # 仅短裙（1 次）属长尾
    assert stats["item_type"]["top_share"] == 0.75  # 衬衫 3 / 共 4 次

    assert set(stats) >= {"style", "item_type"}
