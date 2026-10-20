"""素材标签关联的服务层用例（补齐 `inspiration_tags.py` 25% 覆盖率的盲区）。

为什么需要：这 88 条语句里真正容易错的是**统计口径与幂等**，而 API 用例只断言
「标签出现在列表里」：

- `batch_add_tags` 的 `added / affected / skipped / not_found / skipped_existing`
  是界面上「新增 N 个、跳过 M 个」的依据，算错会让用户以为没生效；
- 重复调用必须幂等（`skipped_existing` 累加、`added` 归零），且**只对真正变了的
  素材**登记向量回填（否则每点一次都把上百个素材重新排队）；
- `category_mismatch`（同名标签类别不同）是「提示已添加、大标签区却看不到」的根因，
  必须显式回报；`strict_category=True` 时**不建链**。
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import async_session
from app.models.tag import InspirationTag
from app.services import inspiration_tags


async def _linked_tag_ids(insp_id: str) -> list[int]:
    """素材当前关联的标签 ID（升序，便于断言）。"""
    async with async_session() as db:
        rows = (
            await db.execute(
                select(InspirationTag.tag_id).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).scalars().all()
    return sorted(int(r) for r in rows)


@pytest.fixture
def no_vector_io(monkeypatch):
    """掐掉向量相关副作用：单测只关心统计口径与关联结果。

    - `rebuild_text_vector` 会尝试连 Ollama/LanceDB（不可用时也只是慢）；
    - `enqueue_vector_backfills` 会把素材写进待回填队列（这里改成记录调用）。
    """

    calls: list[list[str]] = []

    async def _fake_rebuild(_db, _insp_id):
        return None

    async def _fake_enqueue(_db, ids):
        calls.append(list(ids))
        return len(ids)

    from app.services import vector_service
    from app.services.task_runners import vector_backfill

    monkeypatch.setattr(vector_service, "rebuild_text_vector", _fake_rebuild)
    monkeypatch.setattr(vector_backfill, "enqueue_vector_backfills", _fake_enqueue)
    return calls


async def test_batch_add_tags_counts_and_idempotent(client, upload, no_vector_io):
    """批量打标：首次全新增、二次全跳过；只对真正变更的素材登记向量回填。"""
    id_a = upload().json()["id"]
    id_b = upload().json()["id"]
    no_vector_io.clear()  # upload() 自己会给新素材登记回填，先清掉再断言

    async with async_session() as db:
        result = await inspiration_tags.batch_add_tags(
            db, [id_a, id_b], ["白色", "JK"], category="color"
        )

    assert result["added"] == 4  # 2 素材 × 2 标签
    assert result["affected"] == 2
    assert result["skipped"] == 0
    assert result["not_found"] == 0
    assert len(await _linked_tag_ids(id_a)) == 2
    assert len(await _linked_tag_ids(id_b)) == 2
    # 只有真正变更的素材进回填队列
    assert no_vector_io == [[id_a, id_b]]

    async with async_session() as db:
        again = await inspiration_tags.batch_add_tags(
            db, [id_a, id_b], ["白色", "JK"], category="color"
        )
    assert again["added"] == 0
    assert again["affected"] == 0
    assert again["skipped"] == 2  # 已全部关联过
    assert again["skipped_existing"] == 4
    assert no_vector_io == [[id_a, id_b]]  # 没有新变更 → 不再入队


async def test_batch_add_tags_reports_missing_ids(client, upload, no_vector_io):
    """不存在的素材计入 not_found / missing_ids，不影响其余素材的关联。"""
    insp_id = upload().json()["id"]

    async with async_session() as db:
        result = await inspiration_tags.batch_add_tags(
            db, [insp_id, "not-exist"], ["JK"]
        )

    assert result["added"] == 1
    assert result["affected"] == 1
    assert result["not_found"] == 1
    assert result["missing_ids"] == ["not-exist"]
    assert result["skipped"] == 1
    assert len(await _linked_tag_ids(insp_id)) == 1


async def test_batch_add_tags_dedupes_ids_and_names(client, upload, no_vector_io):
    """重复的素材 ID 与标签名先去重，避免统计虚增与重复查询。"""
    insp_id = upload().json()["id"]

    async with async_session() as db:
        result = await inspiration_tags.batch_add_tags(
            db, [insp_id, insp_id], ["JK", "JK ", ""]
        )

    assert result["added"] == 1  # 去重后只有 1 个标签
    assert result["affected"] == 1
    assert len(await _linked_tag_ids(insp_id)) == 1


async def test_batch_add_tags_rejects_empty_names(client, upload, no_vector_io):
    """空标签名（含全是空白）必须 400，而不是静默返回 0。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_tags.batch_add_tags(db, [insp_id], ["  ", ""])
    assert exc.value.status_code == 400


async def test_add_tags_rejects_unknown_inspiration_and_empty_names(
    client, upload, no_vector_io
):
    """单条打标：素材不存在 404、名称列表为空 400。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as missing:
            await inspiration_tags.add_inspiration_tags(db, "not-exist", ["JK"])
    assert missing.value.status_code == 404

    insp_id = upload().json()["id"]
    async with async_session() as db:
        with pytest.raises(HTTPException) as empty:
            await inspiration_tags.add_inspiration_tags(db, insp_id, [])
    assert empty.value.status_code == 400


async def test_add_tags_reports_category_mismatch(client, upload, no_vector_io):
    """同名标签类别不同：回报 category_mismatch；strict 模式下不建链。"""
    insp_id = upload().json()["id"]

    # 先用 color 类别建出「白色」
    async with async_session() as db:
        first = await inspiration_tags.add_inspiration_tags(
            db, insp_id, ["白色"], category="color"
        )
    assert first["count"] == 1
    assert first["category_mismatch"] == []
    tag_id = first["added"][0]["id"]

    # 换类别再打同一个标签：非严格 → 建链（如果还没建）并回报冲突
    async with async_session() as db:
        mismatch = await inspiration_tags.add_inspiration_tags(
            db, insp_id, ["白色"], category="style"
        )
    # 已经关联过 → already_linked=True，仍然要回报类别不符（否则用户零反馈）
    assert len(mismatch["category_mismatch"]) == 1
    info = mismatch["category_mismatch"][0]
    assert info["id"] == tag_id
    assert info["category"] == "color"
    assert info["requested"] == "style"
    assert info["already_linked"] is True
    assert mismatch["count"] == 0

    # 严格模式 + 已关联：保留原关联、不新建（关联只有一条）
    assert await _linked_tag_ids(insp_id) == [tag_id]

    # 新素材 + 已存在标签（类别不符）+ strict → 不建链
    other = upload().json()["id"]
    async with async_session() as db:
        strict = await inspiration_tags.add_inspiration_tags(
            db, other, ["白色"], category="style", strict_category=True
        )
    assert strict["count"] == 0
    assert strict["category_mismatch"][0]["already_linked"] is False
    assert await _linked_tag_ids(other) == []


async def test_remove_tag_link(client, upload, no_vector_io):
    """解除关联：成功返回 removed，不存在 404（不影响标签本身）。"""
    insp_id = upload().json()["id"]
    async with async_session() as db:
        added = await inspiration_tags.add_inspiration_tags(db, insp_id, ["JK"])
    tag_id = added["added"][0]["id"]

    async with async_session() as db:
        result = await inspiration_tags.remove_inspiration_tag(db, insp_id, tag_id)
    assert result == {"removed": 1}
    assert await _linked_tag_ids(insp_id) == []

    # 再删一次：关联已不存在 → 404
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await inspiration_tags.remove_inspiration_tag(db, insp_id, tag_id)
    assert exc.value.status_code == 404
