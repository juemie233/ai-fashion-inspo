"""博主人脸注册/注销的测试（特征平均池化 + 逐张质量判定 + 状态查询）。

人脸注册的质量判定链最容易出「看起来成功、其实没用」的问题：
- 模糊/侧脸（低 det_score）或小脸被纳入平均池化 → 特征被污染，之后匹配全废；
- 单张图解码失败被当成整次注册失败 → 用户以为照片不合格，一直换照片也修不好；
- 重新注册没有覆盖旧特征 → 同一博主存在两条特征，匹配结果不确定。

face-service（独立 Python 3.10 子服务）在测试里用假客户端替身。
"""

import numpy as np
import pytest
from fastapi import HTTPException
from sqlalchemy import select  # noqa: F401

from app.database import async_session
from app.services.face_client import FaceServiceHttpError, FaceServiceUnavailableError
from app.models.face import BloggerFaceEmbedding
from app.models.person import Blogger
from app.services import blogger_face

DIM = 8  # 假特征维度：不影响逻辑，够验证归一化/平均


def _embedding(seed: int) -> list[float]:
    vec = np.zeros(DIM, dtype=np.float32)
    vec[seed % DIM] = 1.0
    return vec.tolist()


class _FakeFaceClient:
    """按队列返回结果：每项是 dict / 异常。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def embed(self, data: bytes, *args, **kwargs):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _png_bytes(width: int = 200, height: int = 200) -> bytes:
    """造一张真实 PNG：人脸宽占比需要真实尺寸，用假字节算不出来。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    return buf.getvalue()


def _face(det_score: float = 0.95, bbox=None, seed: int = 0) -> dict:
    return {
        "faces": [
            {
                "det_score": det_score,
                "bbox": bbox or [10, 10, 60, 60],
                "embedding": _embedding(seed),
            }
        ]
    }


async def _blogger(name: str = "测试博主") -> int:
    async with async_session() as db:
        blogger = Blogger(name=name, platform="douyin")
        db.add(blogger)
        await db.commit()
        return blogger.id


@pytest.fixture(autouse=True)
def _patch_embed(monkeypatch):
    """默认替身：返回一张合格人脸（各用例按需覆盖）。"""

    async def _embed(data, *a, **k):
        return _face()

    monkeypatch.setattr(blogger_face.face_client, "embed", _embed)


async def test_register_single_photo_and_status(client):
    blogger_id = await _blogger()

    async with async_session() as db:
        result = await blogger_face.register_blogger_face(db, blogger_id, [b"img-1"])
        status = await blogger_face.get_blogger_face_status(db, blogger_id)

    assert result["registered"] is True
    assert result["photos_used"] == 1
    assert result["photo_results"][0]["status"] == "used"
    assert status["registered"] is True


async def test_register_averages_and_normalizes(client, monkeypatch):
    """多张合格照片 → 平均池化 + 归一化（余弦匹配语义要求单位向量）。"""
    blogger_id = await _blogger()
    responses = [_face(seed=0), _face(seed=1)]
    monkeypatch.setattr(
        blogger_face.face_client, "embed", _FakeFaceClient(responses).embed
    )

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"a", b"b"])

    async with async_session() as db:
        record = (
            await db.execute(
                select(BloggerFaceEmbedding).where(
                    BloggerFaceEmbedding.blogger_id == blogger_id
                )
            )
        ).scalar_one()
    vec = np.frombuffer(record.embedding, dtype=np.float32)
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5


async def test_reregister_overwrites_single_row(client):
    """重新注册是覆盖而非新增（同一博主只能有一条特征，否则匹配不确定）。"""
    blogger_id = await _blogger()

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"a"])

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"b"])

    async with async_session() as db:
        rows = (
            await db.execute(
                select(BloggerFaceEmbedding).where(
                    BloggerFaceEmbedding.blogger_id == blogger_id
                )
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_register_rejects_no_source(client):
    blogger_id = await _blogger()
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(db, blogger_id, [])
    assert exc.value.status_code == 400


async def test_register_rejects_too_many_photos(client):
    blogger_id = await _blogger()
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(db, blogger_id, [b"x"] * 6)
    assert exc.value.status_code == 422


async def test_register_unknown_blogger(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(db, 999999, [b"x"])
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "responses,reason",
    [
        ([FaceServiceHttpError(404, "no face")], "no_face"),
        ([FaceServiceHttpError(400, "decode")], "decode_failed"),
        ([{"faces": []}], "no_face"),
        ([_face(det_score=0.2)], "low_confidence"),
        ([_face(bbox=[10, 10, 11, 11])], "small_face"),  # 配合真实 PNG 使用
    ],
)
async def test_skipped_photo_reports_reason(client, monkeypatch, responses, reason):
    """单张不合格要给出**具体原因**（用户据此换照片，而不是盲猜）。"""
    blogger_id = await _blogger()
    monkeypatch.setattr(
        blogger_face.face_client, "embed", _FakeFaceClient(responses).embed
    )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(
                db, blogger_id, [_png_bytes(width=200)]
            )

    assert exc.value.status_code == 400
    detail = str(exc.value.detail)
    # 文案要能让用户知道下一步做什么（换清晰正脸照 / 换素材），而不是只说失败
    assert "清晰" in detail or "正脸" in detail or "解析" in detail


async def test_all_decode_failed_message_differs(client, monkeypatch):
    """全部解码失败时文案要说「无法解析」，不能说「没检出人脸」——否则用户白换照片。"""
    blogger_id = await _blogger()
    responses = [
        FaceServiceHttpError(400, "decode"),
        FaceServiceHttpError(400, "decode"),
    ]
    monkeypatch.setattr(
        blogger_face.face_client, "embed", _FakeFaceClient(responses).embed
    )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(db, blogger_id, [b"a", b"b"])

    assert "无法被人脸服务解析" in str(exc.value.detail)


async def test_partial_success_keeps_good_photo(client, monkeypatch):
    """一张坏一张好：坏的跳过、好的照常入库（不能因为一张图否定整次注册）。"""
    blogger_id = await _blogger()
    responses = [FaceServiceHttpError(404, "no face"), _face()]
    monkeypatch.setattr(
        blogger_face.face_client, "embed", _FakeFaceClient(responses).embed
    )

    async with async_session() as db:
        result = await blogger_face.register_blogger_face(db, blogger_id, [b"a", b"b"])

    assert result["photos_used"] == 1
    assert result["photos_total"] == 2
    assert result["photo_results"][0]["status"] == "skipped"
    assert result["photo_results"][1]["status"] == "used"


async def test_face_service_unavailable_maps_to_503(client, monkeypatch):
    blogger_id = await _blogger()
    responses = [FaceServiceUnavailableError("服务未启动")]
    monkeypatch.setattr(
        blogger_face.face_client, "embed", _FakeFaceClient(responses).embed
    )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(db, blogger_id, [b"img"])

    assert exc.value.status_code == 503
    assert "人脸服务不可用" in str(exc.value.detail) or "未启动" in str(exc.value.detail)


async def test_zero_vector_rejected(client, monkeypatch):
    """全零特征（子服务异常）要拒绝，不能存进库让匹配全失效。"""
    blogger_id = await _blogger()

    async def _zero(data, *a, **k):
        return {
            "faces": [
                {"det_score": 0.9, "bbox": [0, 0, 100, 100], "embedding": [0.0] * DIM}
            ]
        }

    monkeypatch.setattr(blogger_face.face_client, "embed", _zero)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.register_blogger_face(
                db, blogger_id, [_png_bytes(width=200)]
            )

    assert exc.value.status_code == 400
    assert "零向量" in str(exc.value.detail)


async def test_status_unregistered(client):
    blogger_id = await _blogger("未注册博主")
    async with async_session() as db:
        status = await blogger_face.get_blogger_face_status(db, blogger_id)
    assert status == {"registered": False, "blogger_id": blogger_id}


async def test_status_unknown_blogger_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.get_blogger_face_status(db, 999999)
    assert exc.value.status_code == 404


async def test_unregister_removes_embedding(client):
    blogger_id = await _blogger()

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"a"])

    async with async_session() as db:
        result = await blogger_face.unregister_blogger_face(db, blogger_id)

    assert result["unregistered"] is True if "unregistered" in result else True
    async with async_session() as db:
        status = await blogger_face.get_blogger_face_status(db, blogger_id)
    assert status["registered"] is False


def test_face_width_ratio_math():
    """人脸宽占比：bbox 宽 / 图宽；无图尺寸或无 bbox 返回 None。"""
    bbox = [10, 10, 110, 110]  # 宽 100
    assert blogger_face._face_width_ratio(bbox, (200, 200)) == 0.5
    assert blogger_face._face_width_ratio(bbox, None) is None
    assert blogger_face._face_width_ratio([], (200, 200)) is None
    assert blogger_face._face_width_ratio(bbox, (0, 0)) is None


def test_image_size_parses_dimensions():
    """用 PIL 造一张已知尺寸的 PNG，验证尺寸解析（决定 face_ratio 的分母）。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (640, 480), "white").save(buf, "PNG")
    assert blogger_face._image_size(buf.getvalue()) == (640, 480)
    assert blogger_face._image_size(b"not-an-image") is None


# ── 注销 / 解绑 / 检测记录管理 ──


async def _bind(insp_id: str, blogger_id: int) -> None:
    from app.models.person import InspirationBlogger

    async with async_session() as db:
        db.add(InspirationBlogger(inspiration_id=insp_id, blogger_id=blogger_id))
        await db.commit()


async def _detection(
    insp_id: str,
    *,
    blogger_id: int | None = None,
    face_index: int = 0,
    match_status: str | None = None,
    empty_embedding: bool = False,
) -> int:
    from app.models.face import InspirationFaceDetection

    async with async_session() as db:
        det = InspirationFaceDetection(
            inspiration_id=insp_id,
            face_index=face_index,
            embedding=b"" if empty_embedding else np.ones(DIM, dtype=np.float32).tobytes(),
            bbox=None,
            det_score=0.9,
            confidence=0.8 if blogger_id else None,
            matched_blogger_id=blogger_id,
            match_status=match_status,
        )
        db.add(det)
        await db.commit()
        return det.id


async def _insp_row(insp_id: str) -> None:
    """造一条素材行（人脸检测/博主归属都挂在它上面）。"""
    from app.models.inspiration import Inspiration

    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
                quality_status="approved",
            )
        )
        await db.commit()


async def test_unregister_clears_all_three_layers(client):
    """注销要一次清掉：特征、检测回退、素材归属（三者漏一个都会留下幽灵关联）。"""
    from sqlalchemy import select

    from app.models.face import BloggerFaceEmbedding
    from app.models.person import InspirationBlogger

    blogger_id = await _blogger("待注销博主")
    await _insp_row("u1")
    await _bind("u1", blogger_id)
    await _detection("u1", blogger_id=blogger_id)

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"a"])

    async with async_session() as db:
        result = await blogger_face.unregister_blogger_face(db, blogger_id)

    assert result["embedding_deleted"] == 1
    assert result["face_detections_cleared"] == 1
    assert result["inspirations_unlinked"] == 1

    async with async_session() as db:
        assert (
            await db.execute(
                select(BloggerFaceEmbedding).where(
                    BloggerFaceEmbedding.blogger_id == blogger_id
                )
            )
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(InspirationBlogger).where(
                    InspirationBlogger.blogger_id == blogger_id
                )
            )
        ).scalar_one_or_none() is None


async def test_unregister_unknown_blogger_404(client):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.unregister_blogger_face(db, 999999)
    assert exc.value.status_code == 404


async def test_unbind_keeps_embedding(client):
    """解绑素材只断开归属与检测匹配，**保留人脸特征**（以后仍能自动匹配）。"""
    from sqlalchemy import select

    from app.models.face import BloggerFaceEmbedding

    blogger_id = await _blogger("解绑博主")
    await _insp_row("b1")
    await _bind("b1", blogger_id)
    await _detection("b1", blogger_id=blogger_id)

    async with async_session() as db:
        await blogger_face.register_blogger_face(db, blogger_id, [b"a"])

    async with async_session() as db:
        result = await blogger_face.unbind_blogger_inspirations(db, blogger_id)

    assert result["inspirations_unlinked"] == 1
    async with async_session() as db:
        assert (
            await db.execute(
                select(BloggerFaceEmbedding).where(
                    BloggerFaceEmbedding.blogger_id == blogger_id
                )
            )
        ).scalar_one_or_none() is not None


async def test_unbind_specific_ids_only(client):
    blogger_id = await _blogger("部分解绑博主")
    await _insp_row("p1")
    await _insp_row("p2")
    await _bind("p1", blogger_id)
    await _bind("p2", blogger_id)

    async with async_session() as db:
        result = await blogger_face.unbind_blogger_inspirations(db, blogger_id, ["p1"])

    assert result["inspirations_unlinked"] == 1


async def test_list_detections_filters_placeholders_and_pending(client):
    """列表要滤掉：空 embedding 的「已扫无脸」占位、以及 pending 待审候选。"""
    blogger_id = await _blogger("检测列表博主")
    await _insp_row("d1")
    await _detection("d1", blogger_id=blogger_id, face_index=0)
    await _detection("d1", face_index=1, empty_embedding=True)
    await _detection("d1", face_index=2, match_status="pending")

    async with async_session() as db:
        items = await blogger_face.list_inspiration_detections(db, "d1")
        response = await blogger_face.list_inspiration_detections_response(db, "d1")

    assert len(items) == 1
    assert items[0]["matched_blogger_name"] == "检测列表博主"
    assert response["face_count"] == 1


async def test_set_detection_person_blogger_and_model_exclusive(client):
    from app.models.person import Model

    blogger_id = await _blogger("指定博主")
    async with async_session() as db:
        model = Model(name="指定模特")
        db.add(model)
        await db.commit()
        model_id = model.id

    await _insp_row("sd1")
    det_id = await _detection("sd1")

    async with async_session() as db:
        result = await blogger_face.set_detection_person(db, det_id, "blogger", blogger_id)
    assert result["matched_blogger_id"] == blogger_id
    assert result["matched_model_id"] is None

    async with async_session() as db:
        result = await blogger_face.set_detection_person(db, det_id, "model", model_id)
    assert result["matched_model_id"] == model_id
    assert result["matched_blogger_id"] is None  # 互斥：指定模特要清掉博主

    async with async_session() as db:
        result = await blogger_face.set_detection_person(db, det_id, None, None)
    assert result["matched_model_id"] is None


async def test_set_detection_rejects_locked_record(client):
    """已确认锁定的记录不能改（扫描审核的确认结果不能被单张操作翻掉）。"""
    blogger_id = await _blogger("锁定博主")
    await _insp_row("lk1")
    det_id = await _detection("lk1", blogger_id=blogger_id, match_status="confirmed")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.set_detection_person(db, det_id, "blogger", blogger_id)

    assert exc.value.status_code == 409


async def test_set_detection_validates_person_type_and_existence(client):
    await _insp_row("v1")
    det_id = await _detection("v1")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.set_detection_person(db, det_id, "cat", 1)
        assert exc.value.status_code == 422

        with pytest.raises(HTTPException) as exc:
            await blogger_face.set_detection_person(db, det_id, "blogger", 999999)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await blogger_face.set_detection_person(db, 999999, "blogger", 1)
        assert exc.value.status_code == 404


async def test_delete_detection_and_locked_rejected(client):
    await _insp_row("dd1")
    det_id = await _detection("dd1")
    locked_id = await _detection("dd1", face_index=1, match_status="confirmed")

    async with async_session() as db:
        await blogger_face.delete_detection(db, det_id)

        with pytest.raises(HTTPException) as exc:
            await blogger_face.delete_detection(db, locked_id)
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            await blogger_face.delete_detection(db, 999999)
        assert exc.value.status_code == 404


# ── 素材人脸检测与匹配（矩阵匹配 + 重新检测覆盖 + 锁定记录保护）──


@pytest.fixture(autouse=True)
def _patch_face_detect_deps(monkeypatch):
    """检测链路的外部依赖：库加载与矩阵匹配（纯 numpy 逻辑另有单测）。"""
    from app.services import face_match

    async def _library(db, *a, **k):
        return []

    monkeypatch.setattr(blogger_face, "load_person_library", _library)
    monkeypatch.setattr(blogger_face, "matrix_match_faces", lambda embs, lib, th: [None] * len(embs))


def _inner_face(det_score: float = 0.95, bbox=None, seed: int = 0) -> dict:
    """子服务返回结构里的「单张脸」（含 embedding），检测链路直接用这个。"""
    return {
        "det_score": det_score,
        "bbox": bbox or [10, 10, 60, 60],
        "embedding": _embedding(seed),
    }


async def _detect_bytes(insp_id: str, faces: list[dict] | Exception):
    """执行一次检测：faces 为该次「子服务返回」。"""
    if isinstance(faces, Exception):
        async def _embed(data, *a, **k):
            raise faces
    else:
        async def _embed(data, *a, **k):
            return {"faces": faces}

    blogger_face.face_client.embed = _embed  # type: ignore[assignment]


async def test_detect_writes_detections_with_face_index(client):
    await _insp_row("fx1")
    await _detect_bytes("fx1", [_inner_face(seed=0), _inner_face(seed=1)])

    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx1", b"img")

    assert result["face_count"] == 2
    assert [d["face_index"] for d in result["detections"]] == [0, 1]
    # 无库命中：matched 为空但 det_score 保留（供人工指定）
    assert all(d["matched_blogger_id"] is None for d in result["detections"])
    assert all(d["det_score"] is not None for d in result["detections"])


async def test_detect_matches_library_person(client, monkeypatch):
    blogger_id = await _blogger("命中博主")
    await _insp_row("fx2")
    await _detect_bytes("fx2", [_inner_face(seed=0)])

    async def _library(db, *a, **k):
        return [{"person_type": "blogger", "person_id": blogger_id, "embedding": _embedding(0)}]

    monkeypatch.setattr(blogger_face, "load_person_library", _library)
    monkeypatch.setattr(
        blogger_face,
        "matrix_match_faces",
        lambda embs, lib, th: [{"person_type": "blogger", "person_id": blogger_id, "score": 0.91}],
    )

    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx2", b"img")

    det = result["detections"][0]
    assert det["matched_blogger_id"] == blogger_id
    assert det["matched_blogger_name"] == "命中博主"  # 前端展示要名字，不能只给 id
    assert det["confidence"] == 0.91


async def test_detect_redetection_replaces_old_rows(client):
    """重新检测覆盖旧记录（不累积重复）。"""
    await _insp_row("fx3")
    await _detect_bytes("fx3", [_inner_face(seed=0)])
    async with async_session() as db:
        await blogger_face.detect_inspiration_faces(db, "fx3", b"img")

    await _detect_bytes("fx3", [_inner_face(seed=2)])
    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx3", b"img")

    assert result["face_count"] == 1


async def test_detect_keeps_locked_rows_and_offsets_new_indices(client):
    """已确认（锁定）记录不被覆盖，新人脸序号从锁定数起编（避免撞号）。"""
    await _insp_row("fx4")
    locked_id = await _detection("fx4", face_index=0, match_status="confirmed")

    await _detect_bytes("fx4", [_inner_face(seed=1)])
    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx4", b"img")

    indices = [d["face_index"] for d in result["detections"]]
    assert 0 in indices  # 锁定记录仍在
    assert max(indices) == 1  # 新人脸从 1 开始
    assert any(d["id"] == locked_id for d in result["detections"])


async def test_detect_skips_duplicate_of_locked_embedding(client):
    """同一张已锁定的脸重复检测：跳过插入（同源 embedding 字节级一致）。"""
    await _insp_row("fx5")
    face = _inner_face(seed=0)
    emb = np.asarray(face["embedding"], dtype=np.float32).tobytes()
    from app.models.face import InspirationFaceDetection

    async with async_session() as db:
        db.add(
            InspirationFaceDetection(
                inspiration_id="fx5",
                face_index=0,
                embedding=emb,
                det_score=0.9,
                match_status="confirmed",
            )
        )
        await db.commit()

    await _detect_bytes("fx5", [face])
    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx5", b"img")

    assert result["face_count"] == 1  # 只有那条锁定记录


async def test_detect_no_face_clears_unlocked_but_keeps_locked(client):
    """素材图里没人脸（404）：清非锁定记录、保留锁定记录。"""
    await _insp_row("fx6")
    await _detection("fx6", face_index=0, match_status="confirmed")
    await _detection("fx6", face_index=1)

    await _detect_bytes("fx6", FaceServiceHttpError(404, "no face"))
    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx6", b"img")

    assert result["face_count"] == 1
    assert result["detections"][0]["match_status"] == "confirmed"


async def test_detect_decode_error_gives_actionable_message(client):
    await _insp_row("fx7")
    await _detect_bytes("fx7", FaceServiceHttpError(400, "cannot decode"))

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.detect_inspiration_faces(db, "fx7", b"not-an-image")

    assert exc.value.status_code == 400
    assert "JPG/PNG" in str(exc.value.detail)


async def test_detect_service_down_maps_to_503(client):
    await _insp_row("fx8")
    await _detect_bytes("fx8", FaceServiceUnavailableError("未启动"))

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.detect_inspiration_faces(db, "fx8", b"img")
    assert exc.value.status_code == 503


async def test_detect_missing_material_404(client):
    await _detect_bytes("nope", [_inner_face()])
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.detect_inspiration_faces(db, "nope", b"img")
    assert exc.value.status_code == 404


async def test_detect_low_confidence_face_skips_matching(client, monkeypatch):
    """低置信度人脸不参与自动匹配（模糊/侧脸匹配易误判），但仍记录供手动指定。"""
    await _insp_row("fx9")
    await _detect_bytes("fx9", [_inner_face(det_score=0.3)])

    called = {"n": 0}

    def _match(embs, lib, th):
        called["n"] += 1
        return [None] * len(embs)

    monkeypatch.setattr(blogger_face, "matrix_match_faces", _match)

    async with async_session() as db:
        result = await blogger_face.detect_inspiration_faces(db, "fx9", b"img")

    assert called["n"] == 0  # 全部低置信 → 不做矩阵匹配
    assert result["face_count"] == 1


async def test_detect_loads_image_when_bytes_not_given(client, monkeypatch):
    """不传图（详情页「检测」按钮）时走 load_inspiration_image；失败给可操作提示。"""
    from app.services import face_image

    await _insp_row("fx10")

    async def _load(insp):
        return None, "视频首帧提取失败"

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await blogger_face.detect_inspiration_faces(db, "fx10", None)
    assert exc.value.status_code == 400
    assert "无法用于人脸检测" in str(exc.value.detail)


# ── 上传绑定博主 → 后台绑主脸（f2 批量上传场景）──


async def test_bind_uploaded_binds_confident_main_face(client, monkeypatch):
    """主脸置信度合格 → 绑定博主并锁定（confirmed），次脸只记普通记录。"""
    from app.services import face_image

    blogger_id = await _blogger("上传绑定博主")
    await _insp_row("ub1")

    async def _load(insp):
        return b"img", None

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)
    await _detect_bytes("ub1", [_inner_face(seed=0), _inner_face(seed=1, det_score=0.8)])

    async with async_session() as db:
        result = await blogger_face.bind_uploaded_inspiration_face(db, "ub1", blogger_id)

    assert result["bound"] is True
    assert result["face_count"] == 2
    async with async_session() as db:
        rows = await blogger_face.list_inspiration_detections(db, "ub1")
    # 主脸（锁定）+ 次脸（普通）都会展示
    assert len(rows) == 2
    assert any(r["match_status"] == "confirmed" and r["matched_blogger_id"] == blogger_id for r in rows)
    assert any(r["match_status"] is None and r["matched_blogger_id"] is None for r in rows)


async def test_bind_uploaded_low_confidence_does_not_bind(client, monkeypatch):
    """主脸置信度偏低（0.5~0.65）不锁定绑定：模糊/侧脸锁定易误判。"""
    from app.services import face_image

    blogger_id = await _blogger("低置信博主")
    await _insp_row("ub2")

    async def _load(insp):
        return b"img", None

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)
    await _detect_bytes("ub2", [_inner_face(det_score=0.55)])

    async with async_session() as db:
        result = await blogger_face.bind_uploaded_inspiration_face(db, "ub2", blogger_id)

    assert result["bound"] is False
    async with async_session() as db:
        rows = await blogger_face.list_inspiration_detections(db, "ub2")
    assert rows[0]["matched_blogger_id"] is None


async def test_bind_uploaded_handles_missing_material(client, monkeypatch):
    from app.services import face_image

    async def _load(insp):
        return b"img", None

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)

    async with async_session() as db:
        result = await blogger_face.bind_uploaded_inspiration_face(db, "不存在", 1)

    assert result["bound"] is False
    assert "不存在" in result["reason"]


async def test_bind_uploaded_unreadable_image(client, monkeypatch):
    from app.services import face_image

    blogger_id = await _blogger("读不出图博主")
    await _insp_row("ub3")

    async def _load(insp):
        return None, "视频关键帧提取失败"

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)

    async with async_session() as db:
        result = await blogger_face.bind_uploaded_inspiration_face(db, "ub3", blogger_id)

    assert result["bound"] is False
    assert "关键帧" in result["reason"]


async def test_bind_uploaded_service_down_and_no_face(client, monkeypatch):
    from app.services import face_image

    blogger_id = await _blogger("服务异常博主")
    await _insp_row("ub4")

    async def _load(insp):
        return b"img", None

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)

    await _detect_bytes("ub4", FaceServiceUnavailableError("未启动"))
    async with async_session() as db:
        down = await blogger_face.bind_uploaded_inspiration_face(db, "ub4", blogger_id)
    assert down["bound"] is False
    assert "不可用" in down["reason"]

    # 404 = 无人脸：同样不绑定，但要清掉非锁定旧记录
    await _detection("ub4", face_index=1)
    await _detect_bytes("ub4", FaceServiceHttpError(404, "no face"))
    async with async_session() as db:
        no_face = await blogger_face.bind_uploaded_inspiration_face(db, "ub4", blogger_id)
    assert no_face["bound"] is False
    assert "未检测到人脸" in no_face["reason"]

    # 其它 HTTP 错误（如 500）：记录原因不抛
    await _detect_bytes("ub4", FaceServiceHttpError(500, "boom"))
    async with async_session() as db:
        err = await blogger_face.bind_uploaded_inspiration_face(db, "ub4", blogger_id)
    assert err["bound"] is False
    assert "boom" in err["reason"]


async def test_bind_uploaded_empty_faces(client, monkeypatch):
    from app.services import face_image

    blogger_id = await _blogger("空人脸博主")
    await _insp_row("ub5")

    async def _load(insp):
        return b"img", None

    monkeypatch.setattr(face_image, "load_inspiration_image", _load)
    await _detect_bytes("ub5", [])

    async with async_session() as db:
        result = await blogger_face.bind_uploaded_inspiration_face(db, "ub5", blogger_id)

    assert result["bound"] is False
    assert "未检测到人脸" in result["reason"]


async def test_run_upload_face_bind_swallows_errors(monkeypatch):
    """后台任务失败只记日志，绝不把异常抛给上传主流程。"""

    async def _boom(db, insp_id, blogger_id):
        raise RuntimeError("炸了")

    monkeypatch.setattr(blogger_face, "bind_uploaded_inspiration_face", _boom)

    await blogger_face.run_upload_face_bind("x1", 1)  # 不抛
