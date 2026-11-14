"""博主路由（`routers/bloggers.py`）的批量资料/头像/分组/洞察接口测试。

这些接口是人物管理页的「一键补全 / 合并 / 洞察」入口，覆盖面较散但每个都有明确的
输入校验或输出映射，出错的表现是**静默少字段**（前端按钮消失或显示了错误的头像）：

- 人物组头像兜底：主账号没头像时要用组内账号的（否则折叠行显示占位图）；
- 补全/补头像任务：非法 id 要 422，无可补对象要 400（而不是返回 task_id=null）；
- 分组操作：blogger_id / target / group_id 类型校验，响应原样透传；
- 抖音专属接口（资料卡 / 主页解析 / 人脸候选）对非抖音博主要 400，避免前端白等；
- 相似博主：只保留「包含当前博主」的同名分组，供前端一键合并。

本文件直接调用 handler 并 patch 服务层（不启动任务、不联网）。
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import bloggers as bloggers_router
from app.services import blogger_enrichment_service
from app.services.person_service import blogger_service


class _FakeDb:
    async def refresh(self, _obj):
        return None


class _FakeUpload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class TestParseIds:
    def test_missing_ids_required_vs_optional(self):
        with pytest.raises(HTTPException) as exc_info:
            bloggers_router._parse_ids(None)
        assert exc_info.value.status_code == 422
        assert bloggers_router._parse_ids(None, required=False) == []

    def test_invalid_and_valid(self):
        for body in ({"ids": "x"}, {"ids": [1, "2"]}, {"ids": [1.0]}):
            with pytest.raises(HTTPException) as exc_info:
                bloggers_router._parse_ids(body)
            assert exc_info.value.status_code == 422
        assert bloggers_router._parse_ids({"ids": [1, 2]}) == [1, 2]


class TestListBloggers:
    async def test_group_avatar_falls_back_to_member(self, monkeypatch):
        async def _items(_db, **_kwargs):
            return (
                [
                    {
                        "id": 1,
                        "avatar_path": None,
                        "group_members": [{"avatar_path": "member.png"}],
                    },
                    {"id": 2, "avatar_path": "own.png", "group_members": []},
                    {"id": 3, "avatar_path": None, "group_members": []},
                ],
                3,
            )

        monkeypatch.setattr(blogger_service, "list_items", _items)

        result = await bloggers_router.list_bloggers(page=2, size=10, db=None)

        assert result["total"] == 3
        assert result["page"] == 2
        assert result["items"][0]["avatar_path"] == "member.png"
        assert result["items"][1]["avatar_path"] == "own.png"  # 自己有的优先
        assert result["items"][2]["avatar_path"] is None


class TestEnrichmentEndpoints:
    async def test_enrich_missing_profile(self, client, monkeypatch):
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.enrich_missing_profile({"blogger_ids": "bad"}, db=None)
        assert exc_info.value.status_code == 422

        import app.services.task_runners.enrich_blogger_profile as module

        async def _no_task(_db, _ids):
            return None, 0

        monkeypatch.setattr(module, "create_enrich_blogger_profile_task", _no_task)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.enrich_missing_profile({"blogger_ids": None}, db=None)
        assert exc_info.value.status_code == 400

        async def _ok(_db, _ids):
            return SimpleNamespace(id=7), 5

        monkeypatch.setattr(module, "create_enrich_blogger_profile_task", _ok)
        result = await bloggers_router.enrich_missing_profile({"blogger_ids": [1]}, db=None)
        assert result == {"task_id": 7, "total": 5, "message": "本次将补全 5 位博主"}

    async def test_fill_missing_avatars(self, client, monkeypatch):
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.fill_missing_avatars({"blogger_ids": [1, 2.5]}, db=None)
        assert exc_info.value.status_code == 422

        import app.services.task_runners.blogger_avatar_fill as module

        async def _no_task(_db, _ids, _overwrite):
            return None, 0

        monkeypatch.setattr(module, "create_avatar_fill_task", _no_task)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.fill_missing_avatars({}, db=None)
        assert exc_info.value.status_code == 400

        captured: dict = {}

        async def _ok(_db, ids, overwrite):
            captured.update(ids=ids, overwrite=overwrite)
            return SimpleNamespace(id=9), 3

        monkeypatch.setattr(module, "create_avatar_fill_task", _ok)
        result = await bloggers_router.fill_missing_avatars(
            {"blogger_ids": [1, 2], "overwrite": True}, db=None
        )
        assert captured == {"ids": [1, 2], "overwrite": True}
        assert result["task_id"] == 9

    async def test_missing_profile_and_skip_management(self, monkeypatch):
        async def _missing(_db):
            return [
                SimpleNamespace(
                    id=1, name="甲", platform="douyin", xhs_id=None, platform_user_id="u1"
                )
            ]

        async def _mark(_db, ids, reason):
            assert reason == "确定性无法获取"
            return len(ids)

        async def _unskip(_db, ids):
            return len(ids)

        async def _list(_db):
            return [{"blogger_id": 1, "reason": "x"}]

        monkeypatch.setattr(blogger_enrichment_service, "list_missing_profile_bloggers", _missing)
        monkeypatch.setattr(blogger_enrichment_service, "mark_skipped", _mark)
        monkeypatch.setattr(blogger_enrichment_service, "unskip", _unskip)
        monkeypatch.setattr(blogger_enrichment_service, "list_skipped", _list)

        result = await bloggers_router.missing_profile_bloggers(db=None)
        assert result["total"] == 1
        assert result["items"][0]["platform_user_id"] == "u1"

        assert await bloggers_router.enrich_skip(
            {"blogger_ids": [1, 2], "reason": "确定性无法获取"}, db=None
        ) == {"skipped": 2}
        assert await bloggers_router.enrich_unskip({"blogger_ids": [1]}, db=None) == {
            "unskipped": 1
        }
        assert await bloggers_router.enrich_skips(db=None) == {
            "items": [{"blogger_id": 1, "reason": "x"}],
            "total": 1,
        }

        with pytest.raises(HTTPException):
            await bloggers_router.enrich_skip({"blogger_ids": "bad"}, db=None)
        with pytest.raises(HTTPException):
            await bloggers_router.enrich_unskip({}, db=None)


class TestGroupEndpoints:
    async def test_link_validation_and_delegation(self, monkeypatch):
        import app.services.person.person_groups as groups

        async def _link(_db, blogger_id, target, group_id):
            return {"group_id": group_id or 3, "blogger_id": blogger_id, "target": target}

        monkeypatch.setattr(groups, "link_bloggers", _link)

        for body in (
            {},
            {"blogger_id": "1"},
            {"blogger_id": 1, "target_blogger_id": "2"},
            {"blogger_id": 1, "group_id": "3"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await bloggers_router.link_bloggers_group_api(body, db=None)
            assert exc_info.value.status_code == 422, body

        result = await bloggers_router.link_bloggers_group_api(
            {"blogger_id": 1, "target_blogger_id": 2}, db=None
        )
        assert result["target"] == 2

    async def test_unlink_and_set_primary(self, monkeypatch):
        import app.services.person.person_groups as groups

        async def _unlink(_db, blogger_id):
            return {"unlinked": blogger_id}

        async def _primary(_db, group_id, blogger_id):
            return {"group_id": group_id, "primary": blogger_id}

        monkeypatch.setattr(groups, "unlink_blogger", _unlink)
        monkeypatch.setattr(groups, "set_primary_blogger", _primary)

        assert await bloggers_router.unlink_blogger_group_api({"blogger_id": 4}, db=None) == {
            "unlinked": 4
        }
        with pytest.raises(HTTPException):
            await bloggers_router.unlink_blogger_group_api({}, db=None)

        assert await bloggers_router.set_primary_blogger_api(
            9, {"blogger_id": 2}, db=None
        ) == {"group_id": 9, "primary": 2}
        with pytest.raises(HTTPException):
            await bloggers_router.set_primary_blogger_api(9, {}, db=None)

    async def test_duplicate_names(self, monkeypatch):
        import app.services.person.batch as batch

        async def _groups(_db):
            return [{"key": "甲", "items": [{"id": 1}, {"id": 2}]}]

        monkeypatch.setattr(batch, "duplicate_name_groups", _groups)

        assert await bloggers_router.duplicate_names_api(db=None) == {
            "groups": [{"key": "甲", "items": [{"id": 1}, {"id": 2}]}],
            "total": 1,
        }


class TestDetailEndpoints:
    async def test_get_blogger_merges_style_profile(self, monkeypatch):
        async def _get(_db, _id):
            return SimpleNamespace(id=1, name="甲")

        async def _count(_db, _id):
            return 42

        async def _profile(_db, _id):
            return {"tags": ["法式"]}

        monkeypatch.setattr(blogger_service, "get", _get)
        monkeypatch.setattr(blogger_service, "count_inspirations", _count)
        monkeypatch.setattr(blogger_service, "style_profile", _profile)
        monkeypatch.setattr(
            blogger_service, "_to_dict", lambda blogger, count: {"id": blogger.id, "count": count}
        )

        result = await bloggers_router.get_blogger(1, db=None)
        assert result == {"id": 1, "count": 42, "style_profile": {"tags": ["法式"]}}

    async def test_resolve_douyin_only(self, monkeypatch):
        async def _xhs(_db, _id):
            return SimpleNamespace(id=1, platform="xiaohongshu")

        monkeypatch.setattr(blogger_service, "get", _xhs)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.resolve_douyin_profile_api(1, db=None)
        assert exc_info.value.status_code == 400

        async def _douyin(_db, _id):
            return SimpleNamespace(id=1, platform="douyin")

        async def _resolve(_db, _blogger):
            return {"ok": True, "source": "f2"}

        monkeypatch.setattr(blogger_service, "get", _douyin)
        monkeypatch.setattr(blogger_enrichment_service, "resolve_douyin_profile", _resolve)
        assert await bloggers_router.resolve_douyin_profile_api(1, db=None) == {
            "ok": True,
            "source": "f2",
        }

    async def test_face_candidates_platform_guard(self, monkeypatch):
        async def _weibo(_db, _id):
            return SimpleNamespace(id=1, platform="weibo")

        monkeypatch.setattr(blogger_service, "get", _weibo)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.blogger_face_candidates(1, db=None)
        assert exc_info.value.status_code == 400

        async def _douyin(_db, _id):
            return SimpleNamespace(id=1, platform="douyin")

        import app.services.person.face_candidates as fc

        async def _candidates(_db, blogger_id):
            return {"matched": [], "clusters": [], "candidate_faces": 0, "face_dim": 512}

        monkeypatch.setattr(blogger_service, "get", _douyin)
        monkeypatch.setattr(fc, "list_face_candidates", _candidates)
        result = await bloggers_router.blogger_face_candidates(1, db=None)
        assert result["face_dim"] == 512

    async def test_enrich_blogger_profile(self, monkeypatch):
        async def _xhs(_db, _id):
            return SimpleNamespace(id=1, platform="xiaohongshu")

        monkeypatch.setattr(blogger_service, "get", _xhs)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.enrich_blogger_profile_api(1, db=None)
        assert exc_info.value.status_code == 400

        async def _douyin(_db, _id):
            return SimpleNamespace(id=1, platform="douyin")

        async def _backfill(_db, _blogger, _profiles):
            return {"status": "updated", "updated_fields": ["ip_location"]}

        async def _count(_db, _id):
            return 3

        monkeypatch.setattr(blogger_service, "get", _douyin)
        monkeypatch.setattr(blogger_service, "count_inspirations", _count)
        monkeypatch.setattr(
            blogger_service, "_to_dict", lambda blogger, count: {"id": blogger.id, "count": count}
        )
        monkeypatch.setattr(blogger_enrichment_service, "load_f2_profile_map", lambda: {})
        monkeypatch.setattr(
            blogger_enrichment_service, "backfill_douyin_profile_from_f2", _backfill
        )

        result = await bloggers_router.enrich_blogger_profile_api(1, db=_FakeDb())
        assert result["status"] == "updated"
        assert result["blogger"] == {"id": 1, "count": 3}


class TestListEndpoints:
    async def test_inspirations_and_random_not_found(self, monkeypatch):
        async def _none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(blogger_service, "list_inspirations", _none)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.blogger_inspirations(1, 1, 20, "newest", db=None)
        assert exc_info.value.status_code == 404

        async def _data(_db, blogger_id, page, size, sort, **_kw):
            return {"items": [], "total": 0, "page": page, "size": size}

        monkeypatch.setattr(blogger_service, "list_inspirations", _data)
        assert (await bloggers_router.blogger_inspirations(1, 2, 10, "oldest", db=None))["page"] == 2

        monkeypatch.setattr(blogger_service, "random_inspirations", _none)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.blogger_random_inspirations(1, db=None)
        assert exc_info.value.status_code == 404

        async def _rnd(_db, _id, **kwargs):
            return {"items": [], "total": 0, "size": kwargs.get("size")}

        monkeypatch.setattr(blogger_service, "random_inspirations", _rnd)
        # 直接调用 handler 时必须显式传 Query 参数：否则默认值是 Query(1) 对象
        result = await bloggers_router.blogger_random_inspirations(
            3,
            size=1,
            media_type=None,
            quality_status=None,
            tag_name=None,
            rating_min=None,
            favorite=False,
            face_matched=False,
            db=None,
        )
        assert result["size"] == 1

    async def test_works_and_insights(self, monkeypatch):
        async def _none(*_args, **_kwargs):
            return None

        monkeypatch.setattr(blogger_service, "list_works", _none)
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.blogger_works(1, db=None)
        assert exc_info.value.status_code == 404

        async def _works(_db, _id, page, size):
            return {"works": [], "total": 0, "page": page}

        import app.services.person.insights as insights

        async def _person_insights(_db, _link_model, _link_id_col, person_id):
            return {"person_id": person_id, "months": []}

        monkeypatch.setattr(blogger_service, "list_works", _works)
        monkeypatch.setattr(insights, "person_insights", _person_insights)
        monkeypatch.setattr(
            blogger_service,
            "get",
            lambda *_a, **_k: _async_return(SimpleNamespace(id=5)),
        )
        assert (await bloggers_router.blogger_insights(5, db=None))["person_id"] == 5

    async def test_similar_filters_duplicates_to_current(self, monkeypatch):
        import app.services.person.batch as batch
        import app.services.person.insights as insights

        async def _get(_db, _id):
            return SimpleNamespace(id=5)

        async def _similar(*_args, **_kwargs):
            return [{"id": 9, "score": 0.8}]

        async def _dupes(_db, _service):
            return [
                {"key": "甲", "items": [{"id": 5}, {"id": 6}]},
                {"key": "乙", "items": [{"id": 7}]},
            ]

        monkeypatch.setattr(blogger_service, "get", _get)
        monkeypatch.setattr(insights, "similar_persons", _similar)
        monkeypatch.setattr(batch, "duplicate_name_groups", _dupes)

        result = await bloggers_router.blogger_similar(5, db=None)
        assert result["similar"] == [{"id": 9, "score": 0.8}]
        assert len(result["duplicates"]) == 1  # 只保留包含当前博主的分组
        assert result["duplicates"][0]["key"] == "甲"


class TestRegisterFace:
    async def test_bad_inspiration_ids_json(self):
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.register_blogger_face_api(1, [], "不是JSON", db=None)
        assert exc_info.value.status_code == 422
        with pytest.raises(HTTPException) as exc_info:
            await bloggers_router.register_blogger_face_api(1, [], json.dumps({"a": 1}), db=None)
        assert exc_info.value.status_code == 422

    async def test_uploads_and_ids_forwarded(self, monkeypatch):
        captured: dict = {}

        async def _register(_db, blogger_id, images, ids):
            captured.update(blogger_id=blogger_id, images=images, ids=ids)
            return {"registered": True}

        monkeypatch.setattr(bloggers_router, "register_blogger_face", _register)

        result = await bloggers_router.register_blogger_face_api(
            7,
            [_FakeUpload(b"img-1"), _FakeUpload(b"")],
            json.dumps(["insp-1", "insp-2"]),
            db=None,
        )

        assert result == {"registered": True}
        assert captured["blogger_id"] == 7
        assert captured["images"] == [b"img-1"]  # 空文件被跳过
        assert captured["ids"] == ["insp-1", "insp-2"]


async def _async_return(value):
    return value
