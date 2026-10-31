"""人物详情页增强（P0/P1）的服务与接口用例。

覆盖 docs/人物详情页增强方案.md 里四项「数据面」能力：

1. **资料卡**（A1）：从 f2 用户库离线补 IP 属地 + 粉丝/作品/获赞 + 签名（简介）；
2. **人脸候选**（A3a/A3b）：已匹配素材 + 未匹配人脸聚类；
3. **素材筛选与作品分组**（B1/B2）：筛选口径必须与分页分母一致；
4. **洞察与相似**（B3/B4/B5）：近 12 个月新增、质量构成、标签余弦相似。
"""

from __future__ import annotations

import sqlite3

import numpy as np

from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration
from app.models.person import Blogger
from app.services import blogger_enrichment_service as enrich
from app.services.person import insights as insights_svc
from app.services.person import services as person_services
from app.services.person.face_candidates import list_face_candidates

blogger_service = person_services.blogger_service


def _upload(client, make_image, size=(64, 64), **form) -> str:
    data, ctype = make_image(size=size)
    resp = client.post("/api/inspirations", files={"file": ("m.jpg", data, ctype)}, data=form)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _link(client, inspiration_id: str, blogger_id: int) -> None:
    resp = client.post(
        f"/api/inspirations/{inspiration_id}/bloggers", json={"person_ids": [blogger_id]}
    )
    assert resp.status_code == 200, resp.text


def _create_blogger(client, name: str, **extra) -> dict:
    payload = {"name": name, "platform": "douyin", **extra}
    resp = client.post("/api/bloggers", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── A1 资料卡 ──


def _fake_f2_db(tmp_path, rows: list[tuple]) -> object:
    """造一个只有 user_info_web 的 f2 用户库（列与真实库一致）。"""
    db_path = tmp_path / "douyin_users.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        create table user_info_web (
            sec_user_id text, nickname text, ip_location text, signature text,
            follower_count integer, following_count integer, aweme_count integer,
            total_favorited integer
        )
        """
    )
    conn.executemany(
        "insert into user_info_web values (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_load_f2_profiles_includes_stats_and_signature(tmp_path):
    """资料卡字段必须读出来（签名 + 四个统计数），不能只读 IP 属地。"""
    from scripts.f2_fetch import load_f2_profiles

    _fake_f2_db(
        tmp_path,
        [("sec-1", "甲", "IP属地：浙江", " 极简穿搭｜通勤 ", 12345, 67, 890, 456789)],
    )
    profiles = load_f2_profiles(tmp_path)

    assert profiles["sec-1"]["ip_location"] == "浙江"
    assert profiles["sec-1"]["signature"] == "极简穿搭｜通勤"
    assert profiles["sec-1"]["follower_count"] == 12345
    assert profiles["sec-1"]["aweme_count"] == 890
    assert profiles["sec-1"]["total_favorited"] == 456789


def test_load_f2_profiles_falls_back_when_columns_missing(tmp_path):
    """旧版 f2 库没有统计列：只回填昵称/属地，不能整批报错。"""
    from scripts.f2_fetch import load_f2_profiles

    db_path = tmp_path / "douyin_users.db"
    conn = sqlite3.connect(db_path)
    conn.execute("create table user_info_web (sec_user_id text, nickname text, ip_location text)")
    conn.execute("insert into user_info_web values ('sec-2','乙','IP属地：江苏')")
    conn.commit()
    conn.close()

    profiles = load_f2_profiles(tmp_path)

    assert profiles["sec-2"]["ip_location"] == "江苏"
    assert "follower_count" not in profiles["sec-2"]


async def test_backfill_profile_fills_stats_bio_and_keeps_existing(client):
    """只补空缺：IP/简介已有值不动，统计数总是刷新并记录快照时间。"""
    blogger = _create_blogger(
        client, "资料补齐", platform_user_id="sec-fill", ip_location="上海", bio="我自己的简介"
    )
    profiles = {
        "sec-fill": {
            "nickname": "资料补齐",
            "ip_location": "IP属地：浙江",
            "signature": "抖音签名原文",
            "follower_count": 999,
            "following_count": 8,
            "aweme_count": 7,
            "total_favorited": 6666,
        }
    }

    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        result = await enrich.backfill_douyin_profile_from_f2(db, row, profiles)
        await db.refresh(row)
        assert result["status"] == "updated"
        # 已有 IP / 简介不被覆盖（手填优先）
        assert row.ip_location == "上海"
        assert row.bio == "我自己的简介"
        assert row.bio_source is None
        # 统计数刷新 + 快照时间
        assert row.follower_count == 999
        assert row.aweme_count == 7
        assert row.stats_synced_at is not None
        assert "follower_count" in result["updated_fields"]


async def test_backfill_profile_writes_signature_as_bio_source(client):
    """简介为空时才写抖音签名，并标记来源（前端据此显示「抖音签名」标签）。"""
    blogger = _create_blogger(client, "签名回填", platform_user_id="sec-bio")
    profiles = {
        "sec-bio": {
            "nickname": "签名回填",
            "ip_location": "",
            "signature": "签名即简介",
            "follower_count": 1,
            "following_count": None,
            "aweme_count": None,
            "total_favorited": None,
        }
    }

    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        await enrich.backfill_douyin_profile_from_f2(db, row, profiles)
        await db.refresh(row)

    assert row.bio == "签名即简介"
    assert row.bio_source == "f2_signature"


async def test_backfill_profile_skips_without_uid_or_profile(client):
    """缺 sec_user_id / f2 库里没有该账号：返回 skipped 且不写库。"""
    no_uid = _create_blogger(client, "没有账号 ID")
    async with async_session() as db:
        row = await db.get(Blogger, no_uid["id"])
        result = await enrich.backfill_douyin_profile_from_f2(db, row, {})
        assert result["status"] == "skipped"
        assert "sec_user_id" in result["reason"]

    with_uid = _create_blogger(client, "库里有没资料", platform_user_id="sec-none")
    async with async_session() as db:
        row = await db.get(Blogger, with_uid["id"])
        result = await enrich.backfill_douyin_profile_from_f2(db, row, {})
        assert result["status"] == "skipped"
        assert "f2 用户库里没有这个账号" in result["reason"]


async def test_backfill_profile_reports_nothing_to_fill(client, monkeypatch):
    """账号在库里但一个字段都没有：skipped 且提示「没有可回填的资料」。"""
    blogger = _create_blogger(client, "空资料账号", platform_user_id="sec-empty")
    profiles = {
        "sec-empty": {
            "nickname": "空资料账号",
            "ip_location": "",
            "signature": "",
            "follower_count": None,
            "following_count": None,
            "aweme_count": None,
            "total_favorited": None,
        }
    }
    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        result = await enrich.backfill_douyin_profile_from_f2(db, row, profiles)

    assert result["status"] == "skipped"
    assert "没有可回填的资料" in result["reason"]


def test_enrich_profile_endpoint_returns_blogger_and_rejects_model(client, monkeypatch):
    """接口：抖音博主可补（返回刷新后的 blogger）；模特 400；不存在 404。"""
    blogger = _create_blogger(client, "接口补齐", platform_user_id="sec-api")

    def _fake_map():
        return {
            "sec-api": {
                "nickname": "接口补齐",
                "ip_location": "IP属地：浙江",
                "signature": "签名",
                "follower_count": 42,
                "following_count": 1,
                "aweme_count": 2,
                "total_favorited": 3,
            }
        }

    monkeypatch.setattr(
        "app.services.blogger_enrichment_service.load_f2_profile_map", _fake_map
    )

    resp = client.post(f"/api/bloggers/{blogger['id']}/enrich-profile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["blogger"]["follower_count"] == 42
    assert body["blogger"]["bio"] == "签名"
    assert body["blogger"]["bio_source"] == "f2_signature"

    resp = client.post("/api/bloggers/999999/enrich-profile")
    assert resp.status_code == 404


# ── B1/B2 作品分组与筛选 ──


async def test_list_works_groups_by_platform_id(client, make_image):
    """同一作品的 6 张图归一组；无作品 ID 的散图自成一组。"""
    blogger = _create_blogger(client, "作品分组")
    ids = []
    for _ in range(6):
        iid = _upload(client, make_image)
        _link(client, iid, blogger["id"])
        ids.append(iid)
    loose = _upload(client, make_image)
    _link(client, loose, blogger["id"])

    async with async_session() as db:
        for idx, iid in enumerate(ids):
            row = await db.get(Inspiration, iid)
            row.source_platform_id = f"f2:7300000000000000001#image{idx + 1}"
        await db.commit()

    async with async_session() as db:
        result = await blogger_service.list_works(db, blogger["id"], page=1, size=10)

    assert result is not None
    assert result["total"] == 2  # 一组作品 + 一组散图
    assert result["total_items"] == 7
    grouped = {w["work_id"]: w for w in result["works"]}
    assert grouped["f2:7300000000000000001"]["count"] == 6
    assert grouped[None]["count"] == 1
    # 预览项带路径（前端要铺缩略图）
    assert grouped["f2:7300000000000000001"]["cover"]["file_path"]


async def test_list_inspirations_filters_narrow_total(client, make_image):
    """筛选必须同时作用于 total（否则分页分母与列表对不上）。"""
    blogger = _create_blogger(client, "筛选口径")
    good = _upload(client, make_image)
    bad = _upload(client, make_image)
    for iid in (good, bad):
        _link(client, iid, blogger["id"])
    async with async_session() as db:
        (await db.get(Inspiration, good)).quality_status = "approved"
        (await db.get(Inspiration, bad)).quality_status = "rejected"
        (await db.get(Inspiration, bad)).rating = 5
        await db.commit()

    async with async_session() as db:
        approved = await blogger_service.list_inspirations(
            db, blogger["id"], 1, 20, "newest", quality_status="approved"
        )
        rated = await blogger_service.list_inspirations(
            db, blogger["id"], 1, 20, "newest", rating_min=5
        )
        all_items = await blogger_service.list_inspirations(db, blogger["id"], 1, 20, "newest")

    assert approved["total"] == 1
    assert approved["items"][0]["inspiration_id"] == good
    assert rated["total"] == 1
    assert rated["items"][0]["inspiration_id"] == bad
    assert all_items["total"] == 2


# ── B3/B4 洞察 ──


async def test_person_insights_monthly_and_quality(client, make_image):
    """近 12 个月按月补零、质量四态计数、首末素材时间。"""
    blogger = _create_blogger(client, "洞察")
    for _ in range(3):
        iid = _upload(client, make_image)
        _link(client, iid, blogger["id"])
    async with async_session() as db:
        from sqlalchemy import select as sa_select

        rows = (
            await db.execute(
                sa_select(Inspiration).where(Inspiration.deleted_at.is_(None))
            )
        ).scalars().all()
        # 三种状态都显式写：上传默认可能已是 approved，只改两条会让计数飘
        for row, status in zip(rows, ("approved", "rejected", "pending"), strict=False):
            row.quality_status = status
        await db.commit()

    async with async_session() as db:
        result = await insights_svc.person_insights(
            db, blogger_service.link_model, blogger_service._link_id_col(), blogger["id"]
        )

    assert result["total"] == 3
    assert len(result["monthly"]) == 12
    assert result["quality"]["approved"] == 1
    assert result["quality"]["rejected"] == 1
    assert result["quality"]["pending"] == 1
    assert result["quality"]["unreviewed"] == 0
    assert result["first_added"] and result["last_added"]


async def test_similar_persons_requires_shared_tags(client, make_image):
    """标签余弦：共同标签 ≥3 才推荐；不共同的人不出现。"""
    from app.models.tag import Tag

    a = _create_blogger(client, "相似甲")
    b = _create_blogger(client, "相似乙")
    c = _create_blogger(client, "不相似丙")

    async with async_session() as db:
        tags = []
        for name in ("极简", "通勤", "阔腿裤", "不相关"):
            tag = Tag(name=name, category="style", source="ai_generated")
            db.add(tag)
            tags.append(tag)
        await db.commit()
        tag_ids = [t.id for t in tags]

    for blogger_id, names in ((a["id"], range(3)), (b["id"], range(3)), (c["id"], [3])):
        iid = _upload(client, make_image)
        _link(client, iid, blogger_id)
        async with async_session() as db:
            from app.models.tag import InspirationTag

            for idx in names:
                db.add(
                    InspirationTag(
                        inspiration_id=iid, tag_id=tag_ids[idx], confidence=0.9, source="ai"
                    )
                )
            await db.commit()

    async with async_session() as db:
        similar = await insights_svc.similar_persons(
            db, blogger_service.model, blogger_service.link_model, blogger_service._link_id_col(), a["id"]
        )

    ids = [s["id"] for s in similar]
    assert b["id"] in ids
    assert c["id"] not in ids  # 只有 0 个共同标签
    target = next(s for s in similar if s["id"] == b["id"])
    assert target["shared_tags"] == 3
    assert target["similarity"] > 0.5


# ── A3 人脸候选 ──


async def _add_detection(db, inspiration_id: str, embedding: np.ndarray, **extra):
    row = InspirationFaceDetection(
        inspiration_id=inspiration_id,
        face_index=0,
        embedding=embedding.astype(np.float32).tobytes(),
        det_score=extra.pop("det_score", 0.9),
        **extra,
    )
    db.add(row)
    return row


async def test_face_candidates_matched_and_cluster(client, make_image):
    """已匹配素材按置信度返回；未匹配的同一个人脸聚成一簇（≥3 出现）。"""
    blogger = _create_blogger(client, "人脸候选")
    base = np.random.default_rng(0).normal(size=512).astype(np.float32)
    base /= np.linalg.norm(base)

    # 3 张素材：同一张脸（略微扰动，模拟同一个人不同角度）
    for _ in range(3):
        iid = _upload(client, make_image)
        _link(client, iid, blogger["id"])
        async with async_session() as db:
            noise = np.random.default_rng(1).normal(scale=0.01, size=512).astype(np.float32)
            await _add_detection(db, iid, base + noise)
            await db.commit()

    # 1 张素材：已匹配到本人
    matched_id = _upload(client, make_image)
    _link(client, matched_id, blogger["id"])
    async with async_session() as db:
        other = np.random.default_rng(9).normal(size=512).astype(np.float32)
        await _add_detection(
            db,
            matched_id,
            other / np.linalg.norm(other),
            matched_blogger_id=blogger["id"],
            match_status="confirmed",
        )
        await db.commit()

    async with async_session() as db:
        result = await list_face_candidates(db, blogger["id"])

    assert len(result["matched"]) == 1
    assert result["matched"][0]["inspiration_id"] == matched_id
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert cluster["size"] == 3
    assert len(cluster["inspiration_ids"]) == 3


async def test_face_candidates_empty_for_unknown_person(client):
    """没有素材/检测时返回空结构（前端显示空态，而不是 500）。"""
    blogger = _create_blogger(client, "无素材")
    async with async_session() as db:
        result = await list_face_candidates(db, blogger["id"])
    assert result == {
        "matched": [],
        "clusters": [],
        "candidate_faces": 0,
        "face_dim": 512,
    }


# ── 审查修复的回归用例 ──


async def test_face_candidates_includes_legacy_null_status(client, make_image):
    """回归：`match_status` 为 NULL 的历史检测也算「已匹配本人」。

    曾经的写法是 ``match_status.in_((None, "confirmed"))``——SQL 里
    ``IN (NULL, 'confirmed')`` 对 NULL 行恒为 UNKNOWN，历史数据会被静默漏掉
    （本库实测存在 1 条）。
    """
    blogger = _create_blogger(client, "历史空状态")
    iid = _upload(client, make_image)
    _link(client, iid, blogger["id"])
    async with async_session() as db:
        emb = np.random.default_rng(3).normal(size=512).astype(np.float32)
        await _add_detection(
            db,
            iid,
            emb / np.linalg.norm(emb),
            matched_blogger_id=blogger["id"],
            match_status=None,
        )
        await db.commit()

    async with async_session() as db:
        result = await list_face_candidates(db, blogger["id"])

    assert [m["inspiration_id"] for m in result["matched"]] == [iid]


async def test_face_candidates_excludes_marked_not_matched(client, make_image):
    """被人脸复核标成「不匹配」的检测不能当注册素材（否则用错脸注册）。"""
    blogger = _create_blogger(client, "被否掉的脸")
    iid = _upload(client, make_image)
    _link(client, iid, blogger["id"])
    async with async_session() as db:
        emb = np.random.default_rng(4).normal(size=512).astype(np.float32)
        await _add_detection(
            db,
            iid,
            emb / np.linalg.norm(emb),
            matched_blogger_id=blogger["id"],
            match_status="confirmed",
            match_excluded=True,
        )
        await db.commit()

    async with async_session() as db:
        result = await list_face_candidates(db, blogger["id"])
    assert result["matched"] == []


async def test_backfill_keeps_existing_stats_when_f2_value_missing(client):
    """回归：f2 某字段为 NULL 时不能把已同步到的统计值抹掉（数据回退）。"""
    blogger = _create_blogger(client, "统计不丢", platform_user_id="sec-partial")
    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        row.follower_count = 1234
        row.aweme_count = 56
        await db.commit()

    profiles = {
        "sec-partial": {
            "nickname": "统计不丢",
            "ip_location": "",
            "signature": "",
            "follower_count": None,  # f2 这一列没有值
            "following_count": 7,
            "aweme_count": None,
            "total_favorited": None,
        }
    }
    async with async_session() as db:
        row = await db.get(Blogger, blogger["id"])
        result = await enrich.backfill_douyin_profile_from_f2(db, row, profiles)
        await db.refresh(row)

    assert result["status"] == "updated"
    assert row.follower_count == 1234  # 不被 None 覆盖
    assert row.aweme_count == 56
    assert row.following_count == 7  # 有值的照常写
    assert "follower_count" not in (result["updated_fields"] or [])


async def test_face_matched_filter_ignores_pending_and_excluded(client, make_image):
    """「人脸已匹配」筛选只认确认/历史结果：AI 候选与被否掉的脸不算。"""
    blogger = _create_blogger(client, "筛选人脸口径")
    confirmed = _upload(client, make_image)
    pending = _upload(client, make_image)
    excluded = _upload(client, make_image)
    for iid in (confirmed, pending, excluded):
        _link(client, iid, blogger["id"])

    async with async_session() as db:
        base_emb = np.random.default_rng(5).normal(size=512).astype(np.float32)
        base_emb /= np.linalg.norm(base_emb)
        await _add_detection(
            db, confirmed, base_emb, matched_blogger_id=blogger["id"], match_status="confirmed"
        )
        await _add_detection(
            db, pending, base_emb, matched_blogger_id=blogger["id"], match_status="pending"
        )
        await _add_detection(
            db,
            excluded,
            base_emb,
            matched_blogger_id=blogger["id"],
            match_status="confirmed",
            match_excluded=True,
        )
        await db.commit()

    async with async_session() as db:
        result = await blogger_service.list_inspirations(
            db, blogger["id"], 1, 20, "newest", face_matched=True
        )

    assert result["total"] == 1
    assert result["items"][0]["inspiration_id"] == confirmed


# ── 随机浏览 ──


async def test_random_inspirations_only_own_materials(client, make_image):
    """随机只在该人物自己的素材里抽（不能串到别人的素材）。"""
    mine = _create_blogger(client, "随机的我")
    other = _create_blogger(client, "别人")
    my_ids = set()
    for _ in range(3):
        iid = _upload(client, make_image)
        _link(client, iid, mine["id"])
        my_ids.add(iid)
    other_id = _upload(client, make_image)
    _link(client, other_id, other["id"])

    async with async_session() as db:
        result = await blogger_service.random_inspirations(db, mine["id"], size=30)

    returned = {item["inspiration_id"] for item in result["items"]}
    assert returned == my_ids
    assert other_id not in returned
    assert result["total"] == 3


async def test_random_inspirations_respects_filters(client, make_image):
    """筛选条件同样生效（在筛选结果里随机），total 也随之变化。"""
    blogger = _create_blogger(client, "随机筛选")
    approved = _upload(client, make_image)
    rejected = _upload(client, make_image)
    for iid in (approved, rejected):
        _link(client, iid, blogger["id"])
    async with async_session() as db:
        (await db.get(Inspiration, approved)).quality_status = "approved"
        (await db.get(Inspiration, rejected)).quality_status = "rejected"
        await db.commit()

    async with async_session() as db:
        result = await blogger_service.random_inspirations(
            db, blogger["id"], size=5, quality_status="rejected"
        )

    assert result["total"] == 1
    assert [i["inspiration_id"] for i in result["items"]] == [rejected]


async def test_random_inspirations_size_limit_and_unknown_person(client, make_image):
    """size 生效（抽到的不重复）；人物不存在返回 None（路由层 404）。"""
    blogger = _create_blogger(client, "随机条数")
    ids = set()
    for _ in range(5):
        iid = _upload(client, make_image)
        _link(client, iid, blogger["id"])
        ids.add(iid)

    async with async_session() as db:
        result = await blogger_service.random_inspirations(db, blogger["id"], size=3)
        missing = await blogger_service.random_inspirations(db, 999999, size=3)

    assert len(result["items"]) == 3
    assert len({i["inspiration_id"] for i in result["items"]}) == 3
    assert {i["inspiration_id"] for i in result["items"]} <= ids
    assert missing is None


def test_random_endpoint_returns_items_and_404(client, make_image):
    """接口：正常返回 {person, items, total}；不存在 404；size 越界 422。"""
    blogger = _create_blogger(client, "随机接口")
    iid = _upload(client, make_image)
    _link(client, iid, blogger["id"])

    resp = client.get(f"/api/bloggers/{blogger['id']}/random", params={"size": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["inspiration_id"] == iid
    assert body["person"]["id"] == blogger["id"]

    assert client.get("/api/bloggers/999999/random").status_code == 404
    assert (
        client.get(f"/api/bloggers/{blogger['id']}/random", params={"size": 31}).status_code
        == 422
    )
