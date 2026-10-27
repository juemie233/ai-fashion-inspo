"""垃圾桶**服务层**用例：补齐 `test_trash.py`（只走 API）盲区。

为什么需要这一层：2026-10-20 的覆盖率实测显示 `inspiration_trash.py` 仅 30%
（191 条语句里 134 条未覆盖）——API 用例只验证「界面看得见的结果」，
而这条链路的坑几乎都在服务层的顺序与对称性上：

- 软删除三字段必须**同真同假**（`deleted_at` / `trash_reason` / `trash_source`），
  每条状态转移后都用 `verify_trash_invariants` 兜一遍；
- 「先 DB 提交、后搬文件」，文件搬失败不能回滚软删除（恢复时按路径自愈）；
- 来源 URL 墓碑与恢复**对称**：移入时落墓碑（防重复采集）、恢复时必须解除，
  否则素材恢复后 URL 仍被永久拦截；
- 恢复会重新占用 `source_platform_id` 的唯一性（部分唯一索引只约束未删除素材），
  冲突必须转 409 而不是 500；
- 批量入口的审计要**聚合**（一条 batch_trash，而不是逐条留痕）。
"""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.audit import AuditLog
from app.models.inspiration import Inspiration, utcnow
from app.models.scraper import ScraperSeenURL
from app.services import inspiration_state, inspiration_trash


async def _load(db, insp_id: str) -> Inspiration:
    """按 ID 取素材行（不存在直接让用例失败，避免后续断言在 None 上炸得莫名其妙）。"""
    row = await db.get(Inspiration, insp_id)
    assert row is not None, f"素材 {insp_id} 不存在"
    return row


async def _audit_actions() -> list[tuple[str, int]]:
    """当前所有审计记录的 (action, count) 列表。"""
    async with async_session() as db:
        rows = (await db.execute(select(AuditLog.action, AuditLog.count))).all()
    return [(str(r[0]), int(r[1])) for r in rows]


async def _violations() -> list[dict]:
    """三字段同真同假的违规清单（空 = 健康）。"""
    async with async_session() as db:
        return await inspiration_state.verify_trash_invariants(db)


async def _set_fields(insp_id: str, **fields) -> None:
    """直接改库设置字段（造 source_url / source_platform_id 这类 API 不一定接收的列）。"""
    async with async_session() as db:
        insp = await _load(db, insp_id)
        for key, value in fields.items():
            setattr(insp, key, value)
        await db.commit()


async def _tombstone_count(url: str) -> int:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(ScraperSeenURL).where(ScraperSeenURL.source_url == url)
            )
        ).scalars().all()
    return len(rows)


async def test_trash_then_restore_moves_files_and_keeps_invariants(client, upload):
    """移入 → 恢复：文件来回搬、三字段同步、来源 URL 墓碑写与解对称。"""
    insp_id = upload().json()["id"]
    url = "https://www.xiaohongshu.com/explore/trash-unit-1"
    await _set_fields(insp_id, source_url=url)

    async with async_session() as db:
        original = (await _load(db, insp_id)).file_path
    assert (settings.storage_root / original).exists()

    async with async_session() as db:
        trashed = await inspiration_trash.trash_inspiration(db, insp_id, "重复")

    # 三字段同真（写入口径由 _mark_trashed 单点保证）
    assert trashed.deleted_at is not None
    assert trashed.trash_reason == "重复"
    assert trashed.trash_source == "manual"
    # 文件确实进了垃圾桶目录（trash/{id}{suffix}{ext}）
    assert trashed.file_path.startswith("trash/")
    assert not (settings.storage_root / original).exists()
    assert (settings.storage_root / trashed.file_path).exists()
    # 移入即落墓碑：采集器后续遇到同 URL 直接跳过
    assert await _tombstone_count(url) == 1
    assert await _violations() == []
    assert ("trash", 1) in await _audit_actions()

    async with async_session() as db:
        restored = await inspiration_trash.restore_inspiration(db, insp_id)

    # 三字段同假
    assert restored.deleted_at is None
    assert restored.trash_reason is None
    assert restored.trash_source is None
    # 文件回到媒体目录（按扩展名推断 images/…，文件名沿用素材 ID）
    assert restored.file_path.startswith("images/")
    assert (settings.storage_root / restored.file_path).exists()
    # 恢复解除墓碑：否则「恢复了的素材永远采不回来」
    assert await _tombstone_count(url) == 0
    assert await _violations() == []
    assert ("restore", 1) in await _audit_actions()


async def test_trash_rejects_missing_and_already_trashed(client, upload):
    """不存在的素材 404；已在垃圾桶的素材再次移入 409（不重复写墓碑/审计）。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as missing:
            await inspiration_trash.trash_inspiration(db, "not-exist", "重复")
    assert missing.value.status_code == 404

    insp_id = upload().json()["id"]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, insp_id, "重复")
    async with async_session() as db:
        with pytest.raises(HTTPException) as again:
            await inspiration_trash.trash_inspiration(db, insp_id, "重复")
    assert again.value.status_code == 409
    # 只有第一次的审计
    assert [a for a in await _audit_actions() if a[0] == "trash"] == [("trash", 1)]


async def test_restore_rejects_active_and_unknown(client, upload):
    """未在垃圾桶的素材恢复 409；不存在 404。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as unknown:
            await inspiration_trash.restore_inspiration(db, "not-exist")
    assert unknown.value.status_code == 404

    insp_id = upload().json()["id"]
    async with async_session() as db:
        with pytest.raises(HTTPException) as active:
            await inspiration_trash.restore_inspiration(db, insp_id)
    assert active.value.status_code == 409


async def test_restore_conflict_when_platform_id_taken(client, upload):
    """垃圾桶期间平台 ID 被新素材占用：恢复必须 409，且素材仍留在垃圾桶。"""
    trashed_id = upload(color=(10, 20, 30)).json()["id"]
    holder_id = upload(color=(40, 50, 60)).json()["id"]
    platform_id = "f2:1234567890123456789#image_1"

    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, trashed_id, "重复")
    # 垃圾桶里的素材不参与部分唯一索引，可以让两个素材同 ID
    await _set_fields(trashed_id, source_platform_id=platform_id)
    await _set_fields(holder_id, source_platform_id=platform_id)

    async with async_session() as db:
        with pytest.raises(HTTPException) as conflict:
            await inspiration_trash.restore_inspiration(db, trashed_id)
    assert conflict.value.status_code == 409
    assert "平台 ID" in str(conflict.value.detail)

    # 状态不变：仍在垃圾桶、三字段齐全（回滚不能留下半状态）
    async with async_session() as db:
        still = await _load(db, trashed_id)
        assert still.deleted_at is not None
        assert still.trash_reason == "重复"
    assert await _violations() == []


async def test_batch_trash_skips_bad_ids_and_writes_single_audit(client, upload):
    """批量移入：坏 ID 计入 skipped 不影响其余；审计只写一条汇总。"""
    ok_id = upload(color=(1, 2, 3)).json()["id"]
    already_id = upload(color=(4, 5, 6)).json()["id"]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, already_id, "重复")

    async with async_session() as db:
        result = await inspiration_trash.batch_trash_inspirations(
            db, [ok_id, "not-exist", already_id], "不喜欢"
        )

    assert result == {"trashed": 1, "skipped": 2}
    assert [a for a in await _audit_actions() if a[0] == "batch_trash"] == [
        ("batch_trash", 1)
    ]
    # 逐条路径不写单条 trash 审计（批量只留一条，避免淹没审计面板）
    assert [a for a in await _audit_actions() if a[0] == "trash"] == [
        ("trash", 1)  # 仅 setUp 里那条
    ]
    async with async_session() as db:
        assert (await _load(db, ok_id)).trash_reason == "不喜欢"


async def test_list_trash_filters_and_paginates(client, upload):
    """垃圾桶列表：只含已删除、按原因筛选、分页可用。"""
    ids = [upload(color=(i * 10, 0, 0)).json()["id"] for i in range(1, 4)]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, ids[0], "重复")
        await inspiration_trash.trash_inspiration(db, ids[1], "质量差")
        await inspiration_trash.trash_inspiration(db, ids[2], "质量差")

    async with async_session() as db:
        items, total = await inspiration_trash.list_trash(db)
        assert total == 3
        assert len(items) == 3

        filtered, filtered_total = await inspiration_trash.list_trash(db, reason="质量差")
        assert filtered_total == 2
        assert {i.trash_reason for i in filtered} == {"质量差"}

        page2, page2_total = await inspiration_trash.list_trash(db, page=2, size=2)
        assert page2_total == 3
        assert len(page2) == 1


async def test_purge_trash_removes_rows_files_and_audits(client, upload):
    """清空垃圾桶：记录删除、文件删除、审计留痕（不可恢复的破坏性操作）。"""
    ids = [upload(color=(20, i * 30, 0)).json()["id"] for i in range(1, 3)]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, ids[0], "重复")
        await inspiration_trash.trash_inspiration(db, ids[1], "重复")
        trashed_paths = [(await _load(db, i)).file_path for i in ids]

    async with async_session() as db:
        result = await inspiration_trash.purge_trash(db)

    assert result["deleted"] == 2
    assert result["freed_bytes"] > 0
    assert [a for a in await _audit_actions() if a[0] == "empty_trash"] == [
        ("empty_trash", 2)
    ]
    for path in trashed_paths:
        assert not (settings.storage_root / path).exists()
    async with async_session() as db:
        remaining = (await db.execute(select(Inspiration.id))).scalars().all()
    assert remaining == []


async def test_purge_trash_only_expired_respects_retention(
    client, upload, monkeypatch
):
    """仅清理过期：保留期内不动；保留期配置为 0 时整体禁用（保护用户数据）。"""
    monkeypatch.setattr(settings, "trash_retention_days", 7)
    old_id = upload(color=(70, 0, 0)).json()["id"]
    new_id = upload(color=(0, 70, 0)).json()["id"]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, old_id, "重复")
        await inspiration_trash.trash_inspiration(db, new_id, "重复")

    # 把旧素材的 deleted_at 往前挪 30 天（超过 7 天保留期）
    async with async_session() as db:
        old = await _load(db, old_id)
        old.deleted_at = utcnow() - timedelta(days=30)
        await db.commit()

    async with async_session() as db:
        result = await inspiration_trash.purge_trash(db, only_expired=True)
    assert result["deleted"] == 1
    async with async_session() as db:
        assert (await db.get(Inspiration, new_id)) is not None
        assert (await db.get(Inspiration, old_id)) is None

    # 保留期 <= 0：自动回收整体禁用，一条都不删
    monkeypatch.setattr(settings, "trash_retention_days", 0)
    async with async_session() as db:
        disabled = await inspiration_trash.purge_trash(db, only_expired=True)
    assert disabled["deleted"] == 0
    assert "禁用" in disabled["message"]
    async with async_session() as db:
        assert (await db.get(Inspiration, new_id)) is not None


async def test_delete_rejected_moves_to_trash_with_quality_reason(client, upload):
    """质量审核拒绝批量移入：原因推成「质量差」、来源标 auto、写 delete_rejected 审计。"""
    # 不传 color：同一颜色会生成完全相同的字节 → 被内容去重拦下（拿不到 id）
    ids = [upload().json()["id"] for _ in range(2)]
    for insp_id in ids:
        await _set_fields(insp_id, quality_status="rejected")

    async with async_session() as db:
        result = await inspiration_trash.delete_rejected_inspirations(db)
    assert result["trashed"] == 2
    assert "2" in result["message"]
    assert await _violations() == []

    async with async_session() as db:
        rows = [
            await _load(db, i) for i in ids
        ]
    for row in rows:
        assert row.trash_reason == "质量差"  # rejected → 质量差（负样本学习口径）
        assert row.trash_source == "auto"
    assert [a for a in await _audit_actions() if a[0] == "delete_rejected"] == [
        ("delete_rejected", 2)
    ]

    # 再跑一次：没有可移入的（幂等）
    async with async_session() as db:
        again = await inspiration_trash.delete_rejected_inspirations(db)
    assert again["trashed"] == 0


async def test_delete_inspiration_requires_trash_first(client, upload):
    """物理删除：active 素材必须 409（先软删除）；垃圾桶内才允许，并删记录+文件。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        with pytest.raises(HTTPException) as active:
            await inspiration_trash.delete_inspiration(db, insp_id)
    assert active.value.status_code == 409

    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, insp_id, "重复")
        trashed_path = (await _load(db, insp_id)).file_path

    async with async_session() as db:
        await inspiration_trash.delete_inspiration(db, insp_id)

    async with async_session() as db:
        assert (await db.get(Inspiration, insp_id)) is None
    assert not (settings.storage_root / trashed_path).exists()
    assert [a for a in await _audit_actions() if a[0] == "delete"] == [("delete", 1)]

    async with async_session() as db:
        with pytest.raises(HTTPException) as unknown:
            await inspiration_trash.delete_inspiration(db, insp_id)
    assert unknown.value.status_code == 404


async def test_purge_trash_audits_even_if_vector_delete_fails(client, upload, monkeypatch):
    """清空垃圾桶时向量清理失败，审计仍必须落库（记录已删、不可恢复）。"""
    from sqlalchemy import select

    from app.models.audit import AuditLog
    from app.services import vector_store

    async def _boom(_ids):
        raise RuntimeError("向量库挂了")

    monkeypatch.setattr(vector_store, "delete_inspiration_vectors_batch", _boom)

    insp_id = upload().json()["id"]
    async with async_session() as db:
        await inspiration_trash.trash_inspiration(db, insp_id, "重复")

    async with async_session() as db:
        result = await inspiration_trash.purge_trash(db)

    assert result["deleted"] == 1
    async with async_session() as db:
        audits = (
            await db.execute(
                select(AuditLog.action, AuditLog.count).where(AuditLog.action == "empty_trash")
            )
        ).all()
    assert audits == [("empty_trash", 1)]


# ── 自动物理删除（AI 判定「无穿搭内容」）──


async def test_hard_delete_no_content_deletes_active_image(client, upload):
    """未在垃圾桶的图片也能直接物理删除，并写墓碑 + 审计（自动清理专用入口）。

    为什么单独一条：`delete_inspiration` 会以 409 拦住「活跃素材」（先软删除是给用户
    手动的保护），而自动清理必须能越过它——但不能越过墓碑与审计，否则同一 URL 会被
    采集回来、删掉的素材也查不到是谁删的。
    """
    insp_id = upload().json()["id"]
    url = "https://www.douyin.com/note/no-content-image"
    await _set_fields(insp_id, source_url=url)

    async with async_session() as db:
        file_path = (await _load(db, insp_id)).file_path
    assert (settings.storage_root / file_path).exists()

    async with async_session() as db:
        assert await inspiration_trash.hard_delete_no_content(db, insp_id) is True

    async with async_session() as db:
        assert await db.get(Inspiration, insp_id) is None
    assert not (settings.storage_root / file_path).exists()  # 文件也删了
    assert await _tombstone_count(url) == 1  # 墓碑：不再被采集回来
    assert ("auto_delete_no_content", 1) in await _audit_actions()


async def test_hard_delete_no_content_keeps_video_and_missing(client, upload):
    """视频不删（删视频的风险远大于收益）；素材不存在时返回 False 且不写审计。"""
    video_id = upload().json()["id"]
    # 夹具只能造图片，直接把行标成视频（删除逻辑只认 media_type）
    await _set_fields(video_id, media_type="video")
    async with async_session() as db:
        assert await inspiration_trash.hard_delete_no_content(db, video_id) is False
    async with async_session() as db:
        assert await db.get(Inspiration, video_id) is not None

    async with async_session() as db:
        assert await inspiration_trash.hard_delete_no_content(db, "not-exist") is False
    assert ("auto_delete_no_content", 1) not in await _audit_actions()
