"""人脸矩阵匹配测试：matrix_match_faces 纯函数 + match_all_faces 全库候选匹配。

- matrix_match_faces：纯 numpy 函数，直接单测（阈值过滤/互斥取最高分/空库）；
- match_all_faces：走真实 DB（async_session 工厂 + TestClient lifespan 建表），
  覆盖 博主/模特特征注册 → 素材检测 → 全库匹配写 pending → 幂等（只写变化行）。
"""

import numpy as np
import pytest
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.services.face_match import (
    load_person_library,
    match_all_faces,
    matrix_match_faces,
)


def _unit(seed: int) -> np.ndarray:
    """生成 512 维单位向量（种子固定）。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    return emb / np.linalg.norm(emb)


def _near(seed: int, base: np.ndarray, mix: float = 0.05) -> np.ndarray:
    """与 base 近似同向的单位向量（cos ≈ 1 - mix^2/2）。"""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(512).astype(np.float32)
    v = (1 - mix) * base + mix * noise
    return v / np.linalg.norm(v)


# ═══════════════════════════════════════════════════════════════
#  matrix_match_faces 纯函数
# ═══════════════════════════════════════════════════════════════


def test_matrix_match_blogger_and_model_mutual_exclusion():
    """互斥取最高分：脸与博主同向（cos=1）时即使模特也相似仍命中博主。"""
    base = _unit(1)
    library = [
        {"person_type": "blogger", "person_id": 10, "embedding": base},
        {"person_type": "model", "person_id": 20, "embedding": _near(2, base)},
    ]
    faces = np.stack([base, _unit(9)], axis=0)
    results = matrix_match_faces(faces, library, threshold=0.5)
    assert results[0] == {"person_type": "blogger", "person_id": 10, "score": pytest.approx(1.0, abs=1e-5)}
    assert results[1] is None  # 正交向量低于阈值


def test_matrix_match_threshold_filter():
    """阈值过滤：低于阈值的脸返回 None。"""
    base = _unit(1)
    library = [{"person_type": "blogger", "person_id": 1, "embedding": base}]
    far = _unit(3)  # 与 base 独立随机 → cos 接近 0
    results = matrix_match_faces(np.stack([far], axis=0), library, threshold=0.5)
    assert results == [None]


def test_matrix_match_empty_library():
    """空特征库：全部返回 None（不崩溃）。"""
    faces = np.stack([_unit(1), _unit(2)], axis=0)
    assert matrix_match_faces(faces, [], threshold=0.5) == [None, None]


def test_matrix_match_empty_faces():
    """空人脸：返回空列表。"""
    library = [{"person_type": "blogger", "person_id": 1, "embedding": _unit(1)}]
    assert matrix_match_faces(np.zeros((0, 512), dtype=np.float32), library, 0.5) == []


def _unit_axis(i: int) -> np.ndarray:
    """第 i 维为 1 的 512 维单位向量（坐标轴）。"""
    v = np.zeros(512, dtype=np.float32)
    v[i] = 1.0
    return v


def _with_cos(cos_val: float, seed: int) -> np.ndarray:
    """构造与 e0 夹角余弦**精确等于** cos_val 的 512 维单位向量。

    按人阈值测试需要可控的相似度：这样能直接指定「某脸对该人 0.50、
    对另一人 0.55」这种组合，而不必依赖随机向量的近似夹角。
    """
    e0 = _unit_axis(0)
    rng = np.random.default_rng(seed)
    perp = rng.standard_normal(512).astype(np.float32)
    perp[0] = 0.0  # 与 e0 正交
    perp /= np.linalg.norm(perp)
    v = cos_val * e0 + np.sqrt(1.0 - cos_val**2) * perp
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_matrix_match_per_person_threshold_not_stolen_by_stricter_person():
    """按人阈值：先各自达标再取最优，高分但阈值更严的人不得挤掉达标者。

    构造：脸对博主 A 相似度 0.50（A 阈值 0.30，达标）、对模特 B 相似度 0.55
    （B 阈值 0.60，不达标）。若先取全局 argmax（B，0.55）再卡单一阈值，
    会误判命中 B；按人阈值应命中 A。
    """
    face = _unit_axis(0)
    library = [
        {
            "person_type": "blogger",
            "person_id": 1,
            "embedding": _with_cos(0.50, 1),
            "threshold": 0.30,
        },
        {
            "person_type": "model",
            "person_id": 2,
            "embedding": _with_cos(0.55, 2),
            "threshold": 0.60,
        },
    ]
    results = matrix_match_faces(np.stack([face], axis=0), library, threshold=0.5)
    assert results[0] is not None
    assert results[0]["person_type"] == "blogger"
    assert results[0]["person_id"] == 1
    assert results[0]["score"] == pytest.approx(0.50, abs=1e-3)


def test_matrix_match_per_person_threshold_all_fail_returns_none():
    """全员不达标 → None：掩码后相似度全为 -inf，不能误取 argmax 下标 0。"""
    face = _unit_axis(0)
    library = [
        {
            "person_type": "blogger",
            "person_id": 1,
            "embedding": _with_cos(0.50, 1),
            "threshold": 0.90,
        },
        {
            "person_type": "model",
            "person_id": 2,
            "embedding": _with_cos(0.55, 2),
            "threshold": 0.80,
        },
    ]
    assert matrix_match_faces(np.stack([face], axis=0), library, threshold=0.5) == [None]


def test_matrix_match_missing_threshold_falls_back_to_argument():
    """库条目未带 threshold 键时回退 threshold 参数（兼容旧调用方）。"""
    face = _unit_axis(0)
    library = [{"person_type": "blogger", "person_id": 1, "embedding": _with_cos(0.50, 1)}]
    assert matrix_match_faces(np.stack([face], axis=0), library, threshold=0.6) == [None]
    hit = matrix_match_faces(np.stack([face], axis=0), library, threshold=0.4)
    assert hit[0] is not None and hit[0]["person_id"] == 1


def test_matrix_match_non_finite_threshold_falls_back_instead_of_bypassing():
    """条目阈值为 None / nan 时必须回退兜底值，不能变成「永不掩码」。

    回归：``np.asarray([None], dtype=np.float32)`` 得到 nan，而 ``score < nan``
    恒为 False → 掩码对该人物失效，哪怕相似度只有 0.45（兜底阈值 0.5）也会以
    最高分挤掉真正达标的人。nan 阈值同理。
    """
    face = _unit_axis(0)
    # 博主 1：相似度更高（0.45）但阈值缺失 → 应按兜底 0.5 判不达标
    # 模特 2：相似度 0.40，自身阈值 0.30 → 唯一达标者
    for bad_threshold in (None, float("nan"), "不是数字"):
        library = [
            {
                "person_type": "blogger",
                "person_id": 1,
                "embedding": _with_cos(0.45, 1),
                "threshold": bad_threshold,
            },
            {
                "person_type": "model",
                "person_id": 2,
                "embedding": _with_cos(0.40, 2),
                "threshold": 0.30,
            },
        ]
        results = matrix_match_faces(np.stack([face], axis=0), library, threshold=0.5)
        assert results[0] is not None, f"阈值={bad_threshold!r} 时不应全员落空"
        assert results[0]["person_id"] == 2, f"阈值={bad_threshold!r} 时不该被未达标者顶掉"
        assert results[0]["score"] == pytest.approx(0.40, abs=1e-3)


def test_matrix_match_non_finite_default_threshold_uses_settings():
    """兜底阈值本身非有限值时回退 settings，不能让 nan 传染给整库。

    判据用等价性：传 nan 的结果必须与显式传 ``settings.face_match_threshold``
    完全一致（两者都走同一条掩码路径）。
    """
    face = _unit_axis(0)
    library = [{"person_type": "blogger", "person_id": 1, "embedding": _with_cos(0.10, 1)}]
    faces = np.stack([face], axis=0)
    assert matrix_match_faces(faces, library, threshold=float("nan")) == (
        matrix_match_faces(faces, library, threshold=settings.face_match_threshold)
    )


# ═══════════════════════════════════════════════════════════════
#  match_all_faces 集成（真实 DB）
# ═══════════════════════════════════════════════════════════════


def _make_photo_bytes(color):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue(), "image/jpeg"


def _setup_blogger_face(client, create_blogger, monkeypatch, embedding):
    """创建博主并注册人脸（mock face_client.embed）。"""
    blogger = create_blogger(name="脸博主")
    async def fake_embed(image_bytes, filename="image.jpg"):
        return {
            "face_count": 1,
            "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
        }
    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)
    r = client.post(
        f"/api/bloggers/{blogger['id']}/face",
        files=[("files", ("a.jpg", b"photo", "image/jpeg"))],
    )
    assert r.status_code == 200, r.text
    return blogger


def _setup_model_face(client, create_model, monkeypatch, embedding):
    """创建模特 + 照片组 + 照片并注册人脸（mock face_client.embed_batch）。"""
    model = create_model(name="脸模特")
    r = client.post(f"/api/models/{model['id']}/photo-sets", json={"name": "写真"})
    assert r.status_code == 201, r.text
    set_id = r.json()["id"]
    data, ctype = _make_photo_bytes((10, 20, 30))
    r = client.post(
        f"/api/models/{model['id']}/photo-sets/{set_id}/photos",
        files={"file": ("a.jpg", data, ctype)},
        data={"sort_order": "0"},
    )
    assert r.status_code == 201, r.text

    async def fake_embed_batch(images, filenames=None):
        return {
            "items": [
                {
                    "index": 0,
                    "face_count": 1,
                    "faces": [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": embedding}],
                }
            ],
            "failed": 0,
        }

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", fake_embed_batch)
    r = client.post(f"/api/models/{model['id']}/face")
    assert r.status_code == 200, r.text
    return model


def _setup_inspiration_faces(client, monkeypatch, faces):
    """上传素材并检测（mock face_client.embed 返回 faces 列表）。"""
    data, ctype = _make_photo_bytes((200, 30, 40))
    r = client.post(
        "/api/inspirations",
        files={"file": ("insp.jpg", data, ctype)},
        data={"source_type": "manual_upload"},
    )
    assert r.status_code == 201, r.text
    inspiration_id = r.json()["id"]

    async def fake_embed(image_bytes, filename="image.jpg"):
        return {"face_count": len(faces), "faces": faces}

    monkeypatch.setattr("app.services.blogger_face.face_client.embed", fake_embed)
    r = client.post(f"/api/inspirations/{inspiration_id}/face-detect")
    assert r.status_code == 200, r.text
    return inspiration_id


async def test_match_all_faces_writes_pending(
    client, create_blogger, create_model, monkeypatch
):
    """全库匹配：博主/模特合并库取最高分，写入 pending；未命中保持空。"""
    base = _unit(1)
    blogger = _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    model = _setup_model_face(client, create_model, monkeypatch, _near(2, base).tolist())
    # 素材两张脸：脸0 与博主同向（cos=1 > 与模特 ~0.998），脸1 随机（未命中）
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [
            {"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()},
            {"bbox": [10, 10, 20, 20], "det_score": 0.9, "embedding": _unit(9).tolist()},
        ],
    )

    async with async_session() as db:
        stats = await match_all_faces(db)
        assert stats["total_faces"] == 2
        assert stats["matched"] == 1
        assert stats["unmatched"] == 1
        assert stats["updated"] == 2
        assert stats["library_size"] == 2

        detections = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        detections = sorted(detections, key=lambda d: d.face_index)
        # 脸0：命中博主（取最高分者），pending 候选
        assert detections[0].matched_blogger_id == blogger["id"]
        assert detections[0].matched_model_id is None
        assert detections[0].match_status == "pending"
        assert detections[0].confidence > 0.9
        # 脸1：未命中
        assert detections[1].matched_blogger_id is None
        assert detections[1].matched_model_id is None
        assert detections[1].match_status == "pending"

        # 幂等：再跑一次无变化
        stats2 = await match_all_faces(db)
        assert stats2["updated"] == 0


async def test_match_all_faces_model_scope(client, create_blogger, create_model, monkeypatch):
    """scope=models 时只与模特库比对，博主特征不参与。"""
    base = _unit(1)
    _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    model = _setup_model_face(client, create_model, monkeypatch, _near(2, base).tolist())
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()}],
    )

    async with async_session() as db:
        stats = await match_all_faces(db, scope="models")
        # 脸与博主同向（cos=1）但与模特 ~0.998：scope=models 下命中模特
        assert stats["matched"] == 1
        detections = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        det = detections[0]
        assert det.matched_model_id == model["id"]
        assert det.matched_blogger_id is None
        assert det.match_status == "pending"


async def test_match_all_faces_skips_excluded(client, create_blogger, monkeypatch):
    """人工「不匹配」（match_excluded=True）的人脸不再参与全库匹配。

    修复前：reject 只清空匹配字段，下次 match_all_faces 会重新匹配并再次
    产出 pending 候选（同一张被拒图反复出现）。现在 excluded 记录被排除。
    """
    base = _unit(1)
    blogger = _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()}],
    )

    async with async_session() as db:
        # 第一轮全库匹配：产出 pending 候选
        stats = await match_all_faces(db)
        assert stats["matched"] == 1
        dets = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        assert dets[0].matched_blogger_id == blogger["id"]
        assert dets[0].match_status == "pending"

        # 人工「不匹配」：置 match_excluded=True（等价于扫描页 reject 动作）
        dets[0].match_excluded = True
        dets[0].matched_blogger_id = None
        dets[0].confidence = None
        dets[0].match_status = None
        await db.commit()

        # 第二轮全库匹配：excluded 记录被排除，不再产出候选
        stats2 = await match_all_faces(db)
        assert stats2["total_faces"] == 0
        assert stats2["matched"] == 0

        dets2 = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        assert dets2[0].match_excluded is True
        assert dets2[0].match_status is None


async def test_match_all_faces_skips_corrupt_embeddings(
    client, create_blogger, monkeypatch
):
    """异常维度脏嵌入不阻塞全库匹配：跳过并计数，任务照常产出候选。

    修复前：`_bytes_to_embedding` 遇到非 512 维嵌入直接抛 ValueError，
    整条匹配任务失败（真实库曾出现 388 维脏数据，face_cluster 同策略过滤）。
    """
    base = _unit(1)
    _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": base.tolist()}],
    )

    async with async_session() as db:
        # 注入一条非 512 维脏嵌入（模拟历史脏数据残留）
        rng = np.random.default_rng(7)
        db.add(
            InspirationFaceDetection(
                inspiration_id=insp_id,
                face_index=1,
                embedding=rng.standard_normal(388).astype(np.float32).tobytes(),
                bbox=None,
                det_score=0.9,
                match_status=None,
            )
        )
        await db.commit()

        stats = await match_all_faces(db)
        # 脏数据被跳过：仅 1 张有效人脸（命中博主），bad_embeddings 计数 1，
        # 统计恒等式 matched + unmatched == total_faces 保持成立
        assert stats["total_faces"] == 1
        assert stats["matched"] == 1
        assert stats["unmatched"] == 0
        assert stats["bad_embeddings"] == 1


# ═══════════════════════════════════════════════════════════════
#  按人自适应阈值
# ═══════════════════════════════════════════════════════════════


async def test_load_person_library_carries_per_person_threshold(
    client, create_blogger, monkeypatch
):
    """特征库条目带各自阈值：未配置用默认，配了用自身的（互不影响）。"""
    base = _unit(1)
    blogger = _setup_blogger_face(client, create_blogger, monkeypatch, base.tolist())

    async with async_session() as db:
        library = await load_person_library(db, default_threshold=0.42)
        assert len(library) == 1
        assert library[0]["threshold"] == pytest.approx(0.42)  # 未配置 → 回退默认

    # PATCH 配置阈值后，特征库立即带上它（无需重新注册人脸）
    r = client.patch(
        f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": 0.31}
    )
    assert r.status_code == 200, r.text
    assert r.json()["face_match_threshold"] == pytest.approx(0.31)

    async with async_session() as db:
        library = await load_person_library(db, default_threshold=0.42)
        assert library[0]["threshold"] == pytest.approx(0.31)


async def test_person_threshold_range_validation_and_clear(
    client, create_blogger, create_model
):
    """阈值接口：越界拒绝、可设置、显式传 null 清除回退全局；模特共享同一字段。"""
    blogger = create_blogger(name="阈值博主")
    r = client.patch(f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": 1.5})
    assert r.status_code == 422  # 越界拒绝（0~1）

    r = client.patch(f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": 0.4})
    assert r.status_code == 200, r.text
    assert r.json()["face_match_threshold"] == pytest.approx(0.4)

    r = client.patch(f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": None})
    assert r.status_code == 200, r.text
    assert r.json()["face_match_threshold"] is None  # 清除 → 回退全局

    model = create_model(name="阈值模特")
    r = client.patch(f"/api/models/{model['id']}", json={"face_match_threshold": 0.35})
    assert r.status_code == 200, r.text
    assert r.json()["face_match_threshold"] == pytest.approx(0.35)


async def test_match_all_faces_uses_per_person_threshold(
    client, create_blogger, monkeypatch
):
    """端到端：调低某人阈值后，原本漏匹配的脸立刻命中；清除后恢复不匹配。

    脸与该博主相似度固定 0.45：全局阈值 0.5 → 不匹配；把该博主阈值调到
    0.40 → 立刻命中；清除阈值（回退全局）→ 又不匹配。
    """
    face_vec = _with_cos(0.45, 11)
    blogger = _setup_blogger_face(
        client, create_blogger, monkeypatch, _unit_axis(0).tolist()
    )
    insp_id = _setup_inspiration_faces(
        client,
        monkeypatch,
        [{"bbox": [0, 0, 10, 10], "det_score": 0.9, "embedding": face_vec.tolist()}],
    )

    async with async_session() as db:
        stats = await match_all_faces(db, threshold=0.5)
        assert stats["matched"] == 0, "全局 0.5 下 0.45 不应命中"

    r = client.patch(
        f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": 0.40}
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        stats = await match_all_faces(db, threshold=0.5)
        assert stats["matched"] == 1, "该博主阈值调到 0.40 后应立刻命中"
        det = (
            await db.execute(
                select(InspirationFaceDetection).where(
                    InspirationFaceDetection.inspiration_id == insp_id
                )
            )
        ).scalars().one()
        assert det.matched_blogger_id == blogger["id"]
        assert det.matched_model_id is None
        assert det.confidence == pytest.approx(0.45, abs=1e-3)

    r = client.patch(
        f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": None}
    )
    assert r.status_code == 200, r.text
    async with async_session() as db:
        stats = await match_all_faces(db, threshold=0.5)
        assert stats["matched"] == 0, "清除阈值回退全局后应恢复不匹配"


async def test_match_all_faces_report_labels_threshold_source(
    client, create_blogger, monkeypatch
):
    """报告区分兜底阈值与自定义阈值：threshold 只是兜底值，不能当成唯一阈值。

    回归：原先报告只有 ``threshold``（= 本次调用的兜底值），页面上看起来像是
    「本次匹配只用这一个阈值」，而实际每个配了阈值的人物各按自身阈值判定。
    """
    _setup_blogger_face(
        client, create_blogger, monkeypatch, _unit_axis(0).tolist()
    )

    async with async_session() as db:
        uniform = await match_all_faces(db, threshold=0.5)
    assert uniform["threshold"] == pytest.approx(0.5)
    assert uniform["default_threshold"] == pytest.approx(0.5)
    assert uniform["custom_threshold_count"] == 0
    assert uniform["threshold_mode"] == "uniform"

    blogger = _setup_blogger_face(
        client, create_blogger, monkeypatch, _unit_axis(1).tolist()
    )
    r = client.patch(
        f"/api/bloggers/{blogger['id']}", json={"face_match_threshold": 0.33}
    )
    assert r.status_code == 200, r.text

    async with async_session() as db:
        per_person = await match_all_faces(db, threshold=0.5)
    assert per_person["default_threshold"] == pytest.approx(0.5), "兜底值应原样回报"
    assert per_person["custom_threshold_count"] == 1, "只有配了阈值的那 1 人计入"
    assert per_person["threshold_mode"] == "per_person"
