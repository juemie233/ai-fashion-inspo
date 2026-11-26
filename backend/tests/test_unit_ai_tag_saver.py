"""ai_tag_saver 纯函数单测：标签提取迭代、跨大类近似去重。"""

from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag
from app.services.ai_tag_saver import iter_extracted_tags, save_tags


def _names_by_category(data: dict) -> dict[str, list[str]]:
    """把 iter_extracted_tags 的输出按类别聚合成 {category: [names]}。"""
    result: dict[str, list[str]] = {}
    for name, category, _conf in iter_extracted_tags(data):
        result.setdefault(category, []).append(name)
    return result


def test_atmosphere_dropped_when_similar_to_style():
    """氛围标签与风格标签近似时，只保留风格标签。"""
    data = {
        "style": ["甜美", "法式"],
        "Atmosphere": ["甜美风", "法式感", "浪漫"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["甜美", "法式"]
    # 甜美风 ≈ 甜美、法式感 ≈ 法式，均被去重；浪漫与风格无关则保留
    assert by_cat.get("atmosphere") == ["浪漫"]


def test_atmosphere_kept_when_no_style_overlap():
    """风格与氛围无近似时，氛围标签正常保留。"""
    data = {
        "style": ["通勤"],
        "Atmosphere": ["浪漫", "优雅"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["通勤"]
    assert by_cat.get("atmosphere") == ["浪漫", "优雅"]


def test_exact_duplicate_across_style_and_atmosphere():
    """完全相同的标签同时出现在风格和氛围时，氛围侧被丢弃。"""
    data = {
        "style": ["学院风"],
        "Atmosphere": ["学院风"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["学院风"]
    assert by_cat.get("atmosphere") is None


def test_no_atmosphere_key_is_safe():
    """响应缺少 Atmosphere 字段时不报错，风格标签正常提取。"""
    data = {"style": ["甜美"]}
    by_cat = _names_by_category(data)
    assert by_cat.get("style") == ["甜美"]
    assert by_cat.get("atmosphere") is None


def test_bare_hosiery_type_dropped():
    """袜类 type 无颜色前缀（裸词「丝袜」「过膝袜」）时丢弃 type。"""
    data = {
        "items": [
            {"type": "丝袜", "color": ""},
            {"type": "过膝袜", "color": ""},
            {"type": "黑色丝袜", "color": ""},
            {"type": "黑色过膝袜", "color": ""},
            {"type": "白丝", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    # 裸词与缺长度丝袜泛称（黑色丝袜）被丢弃；带长度/公认简称的保留
    assert by_cat.get("item_type") == ["黑色过膝袜", "白丝"]


def test_lengthless_silk_stocking_dropped():
    """「颜色+丝袜」但未写明长度（连裤/过膝/长筒等）的泛称不落为标签。"""
    data = {
        "items": [
            {"type": "黑色丝袜", "color": "黑色"},
            {"type": "黑色半透明肤色丝袜", "color": "黑色"},
            {"type": "肉色半透明肤色丝袜", "color": "肉色"},
            {"type": "哑光肤色丝袜", "color": "肤色"},
            {"type": "黑色连裤丝袜", "color": "黑色"},
            {"type": "黑色透肉连裤袜", "color": "黑色"},
            {"type": "黑丝连裤袜", "color": "黑色"},
        ]
    }
    by_cat = _names_by_category(data)
    # 无长度丝袜泛称全部丢弃；连裤袜等写明长度的保留
    assert by_cat.get("item_type") == ["黑色连裤丝袜", "黑色透肉连裤袜", "黑丝连裤袜"]
    # 被丢弃 type 的 color 字段照常产出
    assert "黑色" in by_cat.get("color", [])
    assert "肉色" in by_cat.get("color", [])


def test_hosiery_color_still_kept_when_type_dropped():
    """type 为裸袜词被丢弃时，同单品的 color 字段仍正常产出 color 标签。"""
    data = {
        "items": [
            {"type": "丝袜", "color": "黑色"},
            {"type": "过膝袜", "color": "白色"},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") is None
    assert by_cat.get("color") == ["黑色", "白色"]


def test_non_hosiery_item_types_unaffected():
    """非袜类单品不受裸词过滤影响。"""
    data = {
        "items": [
            {"type": "针织衫", "color": ""},
            {"type": "西装外套", "color": ""},
            {"type": "JK制服", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["针织衫", "西装外套", "JK制服"]


def test_bare_hosiery_dropped_from_material_key():
    """袜类裸词从 material 键漏出时同样被丢弃（如 "material": ["丝袜","皮革"]）。"""
    data = {
        "items": [{"type": "黑色过膝袜", "color": "黑色", "features": []}],
        "material": ["丝袜", "皮革"],
    }
    by_cat = _names_by_category(data)
    # 丝袜裸词不产出 material 标签；皮革等正常保留
    assert by_cat.get("material") == ["皮革"]
    assert by_cat.get("item_type") == ["黑色过膝袜"]


def test_bare_hosiery_dropped_from_features():
    """袜类裸词从 items.features（→body_part）漏出时同样被丢弃。"""
    data = {
        "items": [
            {"type": "白色长筒袜", "color": "白色",
             "features": ["长筒", "纯色", "丝袜"]},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["白色长筒袜"]
    # 「丝袜」裸词不产出 body_part；其它 feature 词保留
    assert by_cat.get("body_part") == ["长筒", "纯色"]


def test_bare_skirt_length_word_dropped():
    """裙类纯长度裸词（短裙/迷你短裙/超短裙）不落为 item_type。"""
    data = {
        "items": [
            {"type": "短裙", "color": ""},
            {"type": "迷你短裙", "color": ""},
            {"type": "超短裙", "color": ""},
            {"type": "黑色百褶短裙", "color": "黑色"},
            {"type": "格纹百褶短裙", "color": ""},
            {"type": "高腰包臀短裙", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    # 裸裙词丢弃；带颜色/款式/图案修饰的正常保留
    assert by_cat.get("item_type") == ["黑色百褶短裙", "格纹百褶短裙", "高腰包臀短裙"]


def test_skirt_like_words_not_bare_kept():
    """非纯长度裙词（连衣裙/半身裙/长裙等）不受裸裙词过滤影响。"""
    data = {
        "items": [
            {"type": "连衣裙", "color": ""},
            {"type": "半身裙", "color": ""},
            {"type": "长裙", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["连衣裙", "半身裙", "长裙"]


def test_bare_shoe_word_dropped_from_features():
    """鞋靴裸词从 items.features（→body_part）漏出时被丢弃（如 "features":["细跟","高跟鞋"]）。"""
    data = {
        "items": [
            {"type": "黑色尖头细跟高跟鞋", "color": "黑色",
             "features": ["细跟", "高跟鞋"]},
        ]
    }
    by_cat = _names_by_category(data)
    # 合规 type 保留；裸「高跟鞋」不产出 body_part 标签，细跟等细节保留
    assert by_cat.get("item_type") == ["黑色尖头细跟高跟鞋"]
    assert by_cat.get("body_part") == ["细跟"]


def test_bare_shoe_word_dropped_from_type():
    """type 为裸鞋词（无颜色，如「凉鞋」「乐福鞋」）时不落为 item_type。"""
    data = {
        "items": [
            {"type": "凉鞋", "color": ""},
            {"type": "乐福鞋", "color": "黑色"},
            {"type": "黑色乐福鞋", "color": "黑色"},
            {"type": "尖头细跟高跟凉鞋", "color": ""},
        ]
    }
    by_cat = _names_by_category(data)
    # 裸鞋词丢弃；带颜色/款式修饰的保留；同单品 color 照常产出
    assert by_cat.get("item_type") == ["黑色乐福鞋", "尖头细跟高跟凉鞋"]
    # 同一颜色被两个单品重复输出时只保留一次（snapshot 唯一约束需要）
    assert by_cat.get("color") == ["黑色"]


def test_bare_shoe_word_dropped_from_design_detail_and_material():
    """鞋靴裸词从 design_detail / material 键漏出时同样被丢弃。"""
    data = {
        "items": [{"type": "黑色高跟鞋", "color": "黑色", "features": []}],
        "design_detail": ["高跟鞋", "尖头"],
        "material": ["皮革", "高跟鞋"],
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("design_detail") == ["尖头"]
    assert by_cat.get("material") == ["皮革"]
    assert by_cat.get("item_type") == ["黑色高跟鞋"]


def test_null_type_and_features_do_not_crash_or_emit_none():
    """type/color/features 为 null 时不能崩溃，也不能写出名为 "None" 的标签。"""
    data = {
        "items": [
            {"type": None, "color": None, "features": None},
            {"type": "黑色过膝袜", "color": "黑色", "features": "纯色、长筒"},
        ]
    }
    by_cat = _names_by_category(data)
    assert "None" not in by_cat.get("item_type", [])
    assert by_cat.get("item_type") == ["黑色过膝袜"]
    assert by_cat.get("color") == ["黑色"]
    assert by_cat.get("body_part") == ["纯色", "长筒"]


def test_items_string_list_treated_as_item_type():
    data = {"items": ["针织开衫", "半身裙"]}
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["针织开衫", "半身裙"]


def test_duplicate_tags_are_deduped_across_items():
    data = {
        "items": [
            {"type": "黑色乐福鞋", "color": "黑色"},
            {"type": "黑色乐福鞋", "color": "黑色"},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["黑色乐福鞋"]
    assert by_cat.get("color") == ["黑色"]


def test_non_dict_input_is_safe():
    assert list(iter_extracted_tags(None)) == []
    assert list(iter_extracted_tags("bad")) == []


async def test_save_tags_empty_does_not_clear_previous_ai_tags(client):
    """解析失败/空结果不能清掉上一次 AI 标签。"""
    async with async_session() as db:
        db.add(
            Inspiration(
                id="saver-empty",
                source_type="douyin",
                file_path="images/2026-11/saver-empty.jpg",
                media_type="image",
            )
        )
        await db.commit()
        await save_tags(db, "saver-empty", {"style": ["法式"]})
        await db.commit()

    async with async_session() as db:
        assert await save_tags(db, "saver-empty", None) == 0
        await db.commit()

    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == "saver-empty"
                )
            )
        ).scalars().all()
    assert len(rows) == 1


def test_bare_hosiery_kept_when_has_color():
    """带颜色的袜类词（黑丝/黑色吊带袜）不被裸袜过滤误杀。"""
    data = {
        "items": [
            {"type": "黑色吊带袜", "color": "黑色", "features": ["蕾丝花边"]},
            {"type": "黑丝", "color": "", "features": []},
        ]
    }
    by_cat = _names_by_category(data)
    assert by_cat.get("item_type") == ["黑色吊带袜", "黑丝"]
