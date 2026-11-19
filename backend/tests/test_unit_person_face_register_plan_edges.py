"""批量注册计划 / 批量注册执行（`services/person/face_register_plan.py`）边界补测。

既有 `tests/test_face_register_plan.py` 只覆盖主流程（真实聚类 + 成功注册 + 归属
校验）；这里补：人脸宽度占比的降级分支、候选过滤/建议去重/截断、无候选博主的
skip_reason，以及批量执行里的 503 快速失败、非博主、空素材、单条异常等分支。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.person import Blogger, InspirationBlogger
from app.services.blogger_face import MIN_FACE_WIDTH_RATIO
from app.services.person.face_register_plan import (
    MAX_BATCH_SIZE,
    _face_width_ratio,
    build_register_plan,
    register_bloggers_batch,
)


def _make_image_file(rel_path: str, size=(100, 100)) -> str:
    path = settings.storage_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (128, 128, 128)).save(path, format="JPEG")
    return rel_path


async def _add_blogger(name: str = "计划博主") -> int:
    async with async_session() as db:
        row = Blogger(name=name, platform="douyin")
        db.add(row)
        await db.commit()
        return row.id


async def _link_inspirations(blogger_id: int, inspiration_ids: list[str]) -> None:
    async with async_session() as db:
        for iid in inspiration_ids:
            db.add(InspirationBlogger(inspiration_id=iid, blogger_id=blogger_id))
        await db.commit()


async def _add_single_face_detection(inspiration_id: str) -> None:
    async with async_session() as db:
        db.add(
            InspirationFaceDetection(
                inspiration_id=inspiration_id,
                face_index=0,
                embedding=np.zeros(512, dtype=np.float32).tobytes(),
                det_score=0.9,
                bbox="[0, 0, 20, 20]",
            )
        )
        await db.commit()


# ── _face_width_ratio：bbox/文件缺失与异常降级 ──


@pytest.mark.parametrize(
    "candidate",
    [
        {},  # 无 bbox、无路径
        {"bbox": "[0, 0, 10, 10]"},  # 无路径
        {"file_path": "plan/any.jpg"},  # 无 bbox
        {"bbox": "not-a-json", "file_path": "plan/any.jpg"},
        {"bbox": json.dumps([0, 0, 10]), "file_path": "plan/any.jpg"},  # 非 4 元组
        {"bbox": json.dumps([0, 0, "x", 10]), "file_path": "plan/any.jpg"},  # 无法转 float
        {"bbox": "[0, 0, 10, 10]", "file_path": "plan/missing.jpg"},  # 文件不存在
    ],
)
def test_face_width_ratio_degrades_to_none(candidate):
    assert _face_width_ratio(candidate) is None


def test_face_width_ratio_image_uses_file_path():
    rel = _make_image_file("plan/width100.jpg", size=(100, 50))
    candidate = {"bbox": "[0, 0, 20, 10]", "file_path": rel, "media_type": "image"}
    assert _face_width_ratio(candidate) == pytest.approx(0.2)
    # bbox 允许直接给 list（不是 JSON 字符串）
    assert _face_width_ratio({**candidate, "bbox": [0, 0, 40, 10]}) == pytest.approx(0.4)


def test_face_width_ratio_video_uses_thumbnail():
    """视频按首帧缩略图算比例（视频本体不能被 PIL 打开）。"""
    _make_image_file("plan/video_thumb.jpg", size=(200, 100))
    _make_image_file("plan/video_full.jpg", size=(50, 50))
    candidate = {
        "bbox": "[0, 0, 40, 10]",
        "media_type": "video",
        "file_path": "plan/video_full.jpg",
        "thumbnail_path": "plan/video_thumb.jpg",
    }
    assert _face_width_ratio(candidate) == pytest.approx(0.2)


# ── build_register_plan 的过滤与 skip_reason ──


async def test_plan_without_candidates_marks_skip_reason(client, upload):
    """only_with_candidates=False：无候选博主也返回，带固定 skip_reason。"""
    blogger_id = await _add_blogger("无候选博主")
    await _link_inspirations(blogger_id, [upload().json()["id"]])

    async with async_session() as db:
        plan = await build_register_plan(db, only_with_candidates=False)

    item = next(i for i in plan["items"] if i["person_id"] == blogger_id)
    assert item["clusters"] == []
    assert item["registered"] is False
    assert item["skip_reason"] == "素材里没有稳定人脸（单脸未匹配检测不足 3 次）"
    assert item["inspiration_count"] == 1
    assert plan["summary"]["no_candidates"] >= 1


async def test_plan_filters_small_faces_and_dedupes_suggestions(
    client, upload, monkeypatch
):
    """候选按人脸宽度门槛过滤；全小的簇整簇丢弃；建议素材去重且最多 3 个。"""
    blogger_id = await _add_blogger("过滤博主")
    insp_ids = [upload().json()["id"] for _ in range(3)]
    for iid in insp_ids:
        await _link_inspirations(blogger_id, [iid])
        await _add_single_face_detection(iid)

    good_rel = _make_image_file("plan/good.jpg", size=(100, 100))
    small_rel = _make_image_file("plan/small.jpg", size=(100, 100))

    def _cand(det_id: int, iid: str, rel: str, bbox: str) -> dict:
        return {
            "detection_id": det_id,
            "inspiration_id": iid,
            "file_path": rel,
            "media_type": "image",
            "bbox": bbox,
            "det_score": 0.9,
        }

    good = [
        _cand(1, insp_ids[0], good_rel, "[0, 0, 20, 10]"),
        _cand(2, insp_ids[0], good_rel, "[0, 0, 30, 10]"),  # 与上条同素材 → 建议去重
        _cand(3, insp_ids[1], good_rel, "[0, 0, 20, 10]"),
        _cand(4, insp_ids[2], good_rel, "[0, 0, 20, 10]"),
        # 人脸过小（1% < 5%）→ 过滤
        _cand(5, "insp-small", small_rel, "[0, 0, 1, 1]"),
    ]

    async def fake_list_candidates(db, blogger_id, **_kwargs):  # noqa: ANN001
        return {
            "clusters": [
                {"size": 3, "avg_score": 0.85, "candidates": good},
                # 整个簇都不合格 → 该簇被丢弃
                {"size": 2, "avg_score": 0.7, "candidates": [good[-1]]},
            ]
        }

    monkeypatch.setattr(
        "app.services.person.face_register_plan.list_face_candidates",
        fake_list_candidates,
    )

    async with async_session() as db:
        plan = await build_register_plan(db)

    item = next(i for i in plan["items"] if i["person_id"] == blogger_id)
    assert len(item["clusters"]) == 1
    cluster = item["clusters"][0]
    assert cluster["size"] == 3
    assert cluster["avg_det_score"] == pytest.approx(0.85)
    # 建议素材去重：4 个好候选只来自 3 个素材
    assert cluster["suggested_inspiration_ids"] == insp_ids
    # 人脸过小者被剔除（cover 不含 det 5）
    assert cluster["cover_detection_ids"] == [1, 2, 3, 4]
    # 前端预览字段只取前 3 个可用候选
    assert len(cluster["candidates"]) == 3
    assert all(c["face_width_ratio"] >= MIN_FACE_WIDTH_RATIO for c in cluster["candidates"])
    assert cluster["candidates"][0]["face_width_ratio"] == pytest.approx(0.2)


# ── register_bloggers_batch 各失败分支 ──


def _item(person_id, person_type="blogger", inspiration_ids=None) -> SimpleNamespace:
    return SimpleNamespace(
        person_type=person_type,
        person_id=person_id,
        inspiration_ids=inspiration_ids if inspiration_ids is not None else [],
    )


async def test_plan_drops_blogger_when_all_clusters_filtered(
    client, upload, monkeypatch
):
    """有候选检测但簇内人脸全被质量门槛过滤：该博主不出现在计划里（也不补 skip 条目），
    只计入 summary 的 no_candidates 统计。"""
    blogger_id = await _add_blogger("全不合格博主")
    for _ in range(3):
        iid = upload().json()["id"]
        await _link_inspirations(blogger_id, [iid])
        await _add_single_face_detection(iid)

    small_rel = _make_image_file("plan/only_small.jpg", size=(100, 100))

    async def fake_list_candidates(db, blogger_id, **_kwargs):  # noqa: ANN001
        return {
            "clusters": [
                {
                    "size": 3,
                    "avg_score": 0.9,
                    "candidates": [
                        {
                            "detection_id": 1,
                            "inspiration_id": "iid-small",
                            "file_path": small_rel,
                            "media_type": "image",
                            "bbox": "[0, 0, 1, 1]",
                            "det_score": 0.9,
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(
        "app.services.person.face_register_plan.list_face_candidates",
        fake_list_candidates,
    )

    async with async_session() as db:
        plan = await build_register_plan(db, only_with_candidates=False)

    assert all(item["person_id"] != blogger_id for item in plan["items"])
    assert plan["summary"]["with_candidates"] == 0
    assert plan["summary"]["no_candidates"] >= 1


async def test_batch_rejects_non_blogger_and_empty_selection():
    blogger_id = await _add_blogger("批量参数博主")
    async with async_session() as db:
        out = await register_bloggers_batch(
            db,
            [
                _item(blogger_id, person_type="model", inspiration_ids=["x"]),
                _item(blogger_id, inspiration_ids=["", "   "]),
            ],
        )

    assert out["succeeded"] == 0
    assert out["failed"] == 2
    assert out["items"][0]["error"] == "暂只支持博主注册"
    assert out["items"][1]["error"] == "至少选择 1 张素材"


async def test_batch_rejects_foreign_inspiration(client, upload):
    """素材不属于该博主：显式失败，不静默少注册。"""
    blogger_id = await _add_blogger("归属博主")
    other_id = await _add_blogger("别的博主")
    iid = upload().json()["id"]
    await _link_inspirations(other_id, [iid])

    async with async_session() as db:
        out = await register_bloggers_batch(db, [_item(blogger_id, inspiration_ids=[iid])])

    row = out["items"][0]
    assert row["ok"] is False
    assert "不属于该博主" in row["error"]
    assert out["failed"] == 1


async def test_batch_success_returns_face_count_and_warnings(
    client, upload, monkeypatch
):
    blogger_id = await _add_blogger("批量成功博主")
    iid = upload().json()["id"]
    await _link_inspirations(blogger_id, [iid])
    seen: dict = {}

    async def fake_register(db, person_id, image_bytes_list, inspiration_ids=None):
        seen.update(
            person_id=person_id,
            image_bytes_list=image_bytes_list,
            inspiration_ids=inspiration_ids,
        )
        return {"photos_used": 2, "warnings": ["第二张模糊"]}

    monkeypatch.setattr(
        "app.services.person.face_register_plan.register_blogger_face", fake_register
    )
    async with async_session() as db:
        out = await register_bloggers_batch(
            db, [_item(str(blogger_id), inspiration_ids=[f"  {iid}  "])]
        )

    assert seen["person_id"] == blogger_id  # 转 int
    assert seen["inspiration_ids"] == [iid]  # strip 后传入
    assert seen["image_bytes_list"] == []  # 素材路径由注册函数内部解析
    assert out["succeeded"] == 1
    assert out["items"][0] == {
        "person_id": blogger_id,
        "ok": True,
        "face_count": 2,
        "error": None,
        "warnings": ["第二张模糊"],
    }


async def test_batch_truncates_to_max_batch_size(client, upload, monkeypatch):
    """单条最多送 5 张素材（[:5] 截断）。"""
    blogger_id = await _add_blogger("截断博主")
    iids = [upload().json()["id"] for _ in range(MAX_BATCH_SIZE + 1)]
    await _link_inspirations(blogger_id, iids)
    captured: list[list[str]] = []

    async def fake_register(db, person_id, image_bytes_list, inspiration_ids=None):
        captured.append(inspiration_ids)
        return {"photos_used": len(inspiration_ids), "warnings": []}

    monkeypatch.setattr(
        "app.services.person.face_register_plan.register_blogger_face", fake_register
    )
    async with async_session() as db:
        out = await register_bloggers_batch(db, [_item(blogger_id, inspiration_ids=iids)])

    assert captured == [iids[:MAX_BATCH_SIZE]]
    assert out["succeeded"] == 1


async def test_batch_service_down_stops_remaining_items(
    client, upload, monkeypatch
):
    """单条 503 后本批剩余条目不执行，直接标记不可用。"""
    blogger_id = await _add_blogger("服务挂博主")
    iid = upload().json()["id"]
    await _link_inspirations(blogger_id, [iid])

    async def fake_register(*args, **kwargs):
        raise HTTPException(status_code=503, detail="人脸识别子服务不可用")

    monkeypatch.setattr(
        "app.services.person.face_register_plan.register_blogger_face", fake_register
    )
    async with async_session() as db:
        out = await register_bloggers_batch(
            db,
            [
                _item(blogger_id, inspiration_ids=[iid]),
                _item(blogger_id, inspiration_ids=[iid]),
            ],
        )

    assert out["failed"] == 2
    assert out["items"][0]["error"] == "人脸识别子服务不可用"
    assert out["items"][1]["error"] == "人脸识别子服务不可用，本批剩余未执行"


async def test_batch_generic_exception_isolated(client, upload, monkeypatch):
    """单条未知异常只让该条失败，不中断整批（error 带异常文本）。"""
    blogger_id = await _add_blogger("异常博主")
    iid = upload().json()["id"]
    await _link_inspirations(blogger_id, [iid])

    async def fake_register(*args, **kwargs):
        raise RuntimeError("磁盘炸了")

    monkeypatch.setattr(
        "app.services.person.face_register_plan.register_blogger_face", fake_register
    )
    async with async_session() as db:
        out = await register_bloggers_batch(db, [_item(blogger_id, inspiration_ids=[iid])])

    assert out["failed"] == 1
    assert out["items"][0]["error"] == "注册异常：磁盘炸了"
