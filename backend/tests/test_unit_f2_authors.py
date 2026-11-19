"""f2 博主清单补充测试：白名单启用/未启用、未登记账号、素材数聚合与排序。

对应 ``app/services/scraper/f2_authors.py`` 的分支：f2 用户库为空、
白名单未启用（库里没有抖音博主）、未登记账号分组、全部已登记、按
``source_author`` 归一化聚合素材数（排除垃圾桶与非抖音来源）、排序稳定。
"""

import types

from app.database import async_session
from app.models.inspiration import Inspiration
from app.services import f2 as real_f2
from app.services.scraper import f2_authors
from app.utils.time import utcnow

AUTHORS = [
    {"sec_user_id": "sec-lixiang", "nickname": "里香1√", "aweme_count": 171},
    {"sec_user_id": "sec-wy", "nickname": "网易第五人格", "aweme_count": 42},
]


def _patch_f2(monkeypatch, tmp_path, authors, bloggers):
    """用替身模块顶替 ``app.services.f2``，避免读真实 f2 用户库。

    服务内部是 ``from app.services import f2`` 的运行时导入，因此除了改模块属性，
    更稳的做法是直接替换包属性——保证无论模块对象如何解析都命中替身。
    """
    fake = types.ModuleType("app.services.f2")
    fake.DEFAULT_F2_DIR = tmp_path
    fake.F2_AUTHOR_DB = real_f2.F2_AUTHOR_DB
    fake.load_f2_authors = lambda _dir: list(authors)
    fake.load_douyin_bloggers = lambda: bloggers
    # 匹配/归一化沿用真实实现（它们不读外部文件，是纯函数）
    fake.normalize_author = real_f2.normalize_author
    fake.match_authors = real_f2.match_authors
    monkeypatch.setattr("app.services.f2", fake)


def _bloggers(*rows):
    """按归一化昵称分组构造 load_douyin_bloggers 的返回结构。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(real_f2.normalize_author(row["name"]), []).append(row)
    return grouped


async def _add_material(**overrides) -> None:
    row = Inspiration(
        file_path=f"images/{overrides.get('source_author', 'x')}.jpg",
        media_type="image",
        source_type=overrides.pop("source_type", "douyin"),
        **overrides,
    )
    async with async_session() as db:
        db.add(row)
        await db.commit()


async def test_no_authors_returns_reason(tmp_path, monkeypatch):
    _patch_f2(monkeypatch, tmp_path, [], {})

    async with async_session() as db:
        body = await f2_authors.get_f2_authors(db)

    assert body["available"] is False
    assert body["registered"] == [] and body["unknown"] == []
    assert body["filter_active"] is False
    assert "用户库为空或不存在" in body["note"]
    assert body["f2_dir"] == str(tmp_path)


async def test_filter_inactive_all_accounts_registered(tmp_path, monkeypatch):
    """库里没有抖音博主：白名单不生效，全部账号都算「会被处理」。"""
    _patch_f2(monkeypatch, tmp_path, AUTHORS, {})

    async with async_session() as db:
        body = await f2_authors.get_f2_authors(db)

    assert body["filter_active"] is False
    assert body["registered_count"] == 2 and body["unknown_count"] == 0
    assert "白名单尚未生效" in body["note"]
    # 未启用白名单时 blogger_name 为 None，但行仍在 registered 里
    assert all(row["blogger_id"] is None for row in body["registered"])


async def test_unknown_accounts_split_and_material_counts(tmp_path, monkeypatch):
    """白名单生效：未登记账号进 unknown；素材数按归一化作者聚合且排除垃圾桶。"""
    bloggers = _bloggers(
        {"id": 302, "name": "里香", "platform_user_id": "sec-lixiang"},
    )
    _patch_f2(monkeypatch, tmp_path, AUTHORS, bloggers)
    await _add_material(source_author="里香1√")
    await _add_material(source_author="里香1√")
    await _add_material(source_author="里香1√", deleted_at=utcnow())  # 垃圾桶不计
    await _add_material(source_author="网易第五人格")
    await _add_material(source_author="网易第五人格", source_type="xiaohongshu")  # 非抖音不计
    await _add_material(source_author="")  # 归一化后为空，不参与聚合

    async with async_session() as db:
        body = await f2_authors.get_f2_authors(db)

    assert body["filter_active"] is True
    assert body["registered_count"] == 1 and body["unknown_count"] == 1
    registered = body["registered"][0]
    assert registered["nickname"] == "里香1√"
    assert registered["blogger_id"] == 302 and registered["blogger_name"] == "里香"
    assert registered["materials"] == 2
    assert registered["profile_url"] == "https://www.douyin.com/user/sec-lixiang"
    unknown = body["unknown"][0]
    assert unknown["nickname"] == "网易第五人格"
    assert unknown["materials"] == 1
    assert "未登记到博主库" in body["note"]


async def test_all_registered_note(tmp_path, monkeypatch):
    """全部账号都命中博主：note 说明白名单无遗漏。"""
    bloggers = _bloggers(
        {"id": 1, "name": "里香", "platform_user_id": "sec-lixiang"},
        {"id": 2, "name": "网易第五人格", "platform_user_id": "sec-wy"},
    )
    _patch_f2(monkeypatch, tmp_path, AUTHORS, bloggers)

    async with async_session() as db:
        body = await f2_authors.get_f2_authors(db)

    assert body["registered_count"] == 2 and body["unknown_count"] == 0
    assert body["note"] == "f2 用户库里的账号都已登记到博主库，白名单无遗漏"


async def test_sorting_by_materials_then_nickname(tmp_path, monkeypatch):
    """清单排序：素材多的在前；同素材数按昵称稳定升序。"""
    bloggers = _bloggers(
        {"id": 1, "name": "里香", "platform_user_id": "sec-lixiang"},
        {"id": 2, "name": "网易第五人格", "platform_user_id": "sec-wy"},
    )
    _patch_f2(monkeypatch, tmp_path, AUTHORS, bloggers)
    await _add_material(source_author="里香1√")  # 1 条
    await _add_material(source_author="网易第五人格")  # 1 条
    await _add_material(source_author="网易第五人格")

    async with async_session() as db:
        body = await f2_authors.get_f2_authors(db)

    # 网易第五人格 2 条 > 里香 1 条
    assert [row["nickname"] for row in body["registered"]] == ["网易第五人格", "里香1√"]


async def test_author_row_defaults_for_missing_fields():
    """_author_row：缺 sec_user_id 时 profile_url 为空；aweme_count 兜底为 0。"""
    row = f2_authors._author_row({"nickname": "无ID账号"}, None, 0)

    assert row["sec_user_id"] == ""
    assert row["profile_url"] == ""
    assert row["aweme_count"] == 0
    assert row["blogger_id"] is None and row["blogger_name"] is None
    assert f2_authors._author_sort_key({"materials": 2, "nickname": "b"}) == (-2, "b")
