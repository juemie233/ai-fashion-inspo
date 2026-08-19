"""职业模特人脸特征注册测试：从写真照片组注册（Top-K 平均池化）/ 状态查询 / 边界。

人脸识别子服务通过 monkeypatch 替换 face_client.embed_batch 模拟（不依赖真实
子服务）；embedding 用固定 512 维单位向量保证确定性。
"""

import numpy as np

from app.services.model_face import DEFAULT_TOP_K, MAX_TOP_K


def _make_photo_bytes(color):
    """生成一张测试图片字节与 content_type。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue(), "image/jpeg"


def _unit_embedding(seed: int = 1) -> list[float]:
    """生成 512 维单位向量（种子固定，测试确定性）。"""
    rng = np.random.default_rng(seed)
    emb = rng.standard_normal(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    return emb.tolist()


def _create_set(client, model, name="写真一组"):
    r = client.post(f"/api/models/{model['id']}/photo-sets", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _upload_photo(client, model, set_id, color, sort_order=0, filename="a.jpg"):
    data, ctype = _make_photo_bytes(color)
    r = client.post(
        f"/api/models/{model['id']}/photo-sets/{set_id}/photos",
        files={"file": (filename, data, ctype)},
        data={"sort_order": str(sort_order)},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _patch_embed_batch(monkeypatch, scores: list[float]):
    """把 face_client.embed_batch 替换为按序返回 det_score 的假实现。

    scores 与照片顺序一一对应；每张图返回一张人脸（det_score + 单位特征）。
    """

    async def fake_embed_batch(images, filenames=None):
        items = []
        for idx, _img in enumerate(images):
            score = scores[idx] if idx < len(scores) else 0.0
            faces = (
                [{"bbox": [0, 0, 10, 10], "det_score": score, "embedding": _unit_embedding(idx + 1)}]
                if score > 0
                else []
            )
            items.append({"index": idx, "face_count": len(faces), "faces": faces})
        return {"items": items, "failed": 0}

    monkeypatch.setattr("app.services.model_face.face_client.embed_batch", fake_embed_batch)


def _setup_model_with_photos(client, create_model, n_photos=7):
    """创建模特 + 一个照片组 + n 张照片，返回 (model, set)。"""
    model = create_model(name="写真模特")
    photo_set = _create_set(client, model)
    for i in range(n_photos):
        _upload_photo(client, model, photo_set["id"], color=(i * 30 % 255, 40, 50), sort_order=i)
    return model, photo_set


def test_register_model_face_topk(client, create_model, monkeypatch):
    """主流程：7 张照片中 5 张合格（置信度≥0.65），top_k=5 取前 5 张平均池化入库。"""
    model, _ = _setup_model_with_photos(client, create_model)
    # 7 张：0.9/0.85/0.8/0.7/0.66 合格（≥0.65），0.4/0.3 被置信度过滤
    _patch_embed_batch(monkeypatch, [0.9, 0.85, 0.8, 0.7, 0.66, 0.4, 0.3])

    r = client.post(f"/api/models/{model['id']}/face", data={"top_k": str(DEFAULT_TOP_K)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"] is True
    assert body["model_id"] == model["id"]
    assert body["photos_used"] == DEFAULT_TOP_K  # 5 张最高质量
    assert body["photos_total"] == 7
    assert body["qualified"] == 5  # 通过质量过滤的人脸数
    assert body["updated_at"]

    # 状态查询
    s = client.get(f"/api/models/{model['id']}/face").json()
    assert s["registered"] is True
    assert s["updated_at"]


def test_register_model_face_respects_topk(client, create_model, monkeypatch):
    """top_k 生效：全部合格时 photos_used = top_k（3）。"""
    model, _ = _setup_model_with_photos(client, create_model, n_photos=5)
    _patch_embed_batch(monkeypatch, [0.9, 0.88, 0.86, 0.84, 0.82])

    r = client.post(f"/api/models/{model['id']}/face", data={"top_k": "3"})
    assert r.status_code == 200, r.text
    assert r.json()["photos_used"] == 3
    assert r.json()["qualified"] == 5


def test_register_model_face_overwrites(client, create_model, monkeypatch):
    """重复注册覆盖旧特征（重新注册语义），状态仍为 registered。"""
    model, _ = _setup_model_with_photos(client, create_model, n_photos=3)
    _patch_embed_batch(monkeypatch, [0.9, 0.9, 0.9])

    r1 = client.post(f"/api/models/{model['id']}/face")
    assert r1.status_code == 200, r1.text
    assert r1.json()["photos_used"] == 3

    r2 = client.post(f"/api/models/{model['id']}/face", data={"top_k": "1"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["photos_used"] == 1
    assert client.get(f"/api/models/{model['id']}/face").json()["registered"] is True


def test_register_model_face_no_photos(client, create_model):
    """模特没有照片组 → 400。"""
    model = create_model(name="无照模特")
    r = client.post(f"/api/models/{model['id']}/face")
    assert r.status_code == 400
    assert "暂无照片" in r.json()["detail"]


def test_register_model_face_all_no_face(client, create_model, monkeypatch):
    """全部照片均未检出人脸 → 400。"""
    model, _ = _setup_model_with_photos(client, create_model, n_photos=3)
    _patch_embed_batch(monkeypatch, [0.0, 0.0, 0.0])  # score=0 → 无脸

    r = client.post(f"/api/models/{model['id']}/face")
    assert r.status_code == 400
    assert "未检出清晰人脸" in r.json()["detail"]


def test_register_model_face_topk_out_of_range(client, create_model):
    """top_k 超出 1~9 → 422。"""
    model = create_model(name="参数模特")
    r = client.post(f"/api/models/{model['id']}/face", data={"top_k": str(MAX_TOP_K + 1)})
    assert r.status_code == 422


def test_register_model_face_model_not_found(client):
    """模特不存在 → 404。"""
    r = client.post("/api/models/999999/face")
    assert r.status_code == 404


def test_face_status_unregistered(client, create_model):
    """未注册时状态查询返回 registered=False。"""
    model = create_model(name="未注册模特")
    s = client.get(f"/api/models/{model['id']}/face").json()
    assert s["registered"] is False
    assert s["model_id"] == model["id"]
