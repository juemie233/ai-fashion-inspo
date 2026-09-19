"""博主人像头像（手动设置）测试：从素材选一张 / 本地上传 / 人脸提取 / 兜底 / 清除。

头像链路：选定照片 → **提取人像**（送人脸识别子服务取照片里最大的人脸，按检测框
外扩裁成 256×256 正方形；照片里没检出人脸或子服务不可用时回退整张照片）→
``storage/avatars/avatar_{id}.jpg`` → 写 ``avatar_path``。这是头像的唯一来源
（不再按博主从素材人脸检测里自动生成）；``avatar_path`` 为空时前端显示名字首字占位。
"""

import io

import numpy as np
from PIL import Image

from app.database import async_session
from app.services.face_crop import FACE_CROP_SIZE
from app.services.person.avatar import AVATAR_MAX_SIDE


def _patch_face_embed(monkeypatch, faces: list[dict]):
    """把人脸子服务的 embed 换成假实现（返回给定人脸列表）。"""

    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        return {"face_count": len(faces), "faces": faces}

    monkeypatch.setattr("app.services.face_crop.face_client.embed", fake_embed)


def _patch_no_face(monkeypatch):
    """模拟「照片里没有检出人脸」：子服务返回 404。"""
    from app.services.face_client import FaceServiceHttpError

    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        raise FaceServiceHttpError(404, "未检测到人脸")

    monkeypatch.setattr("app.services.face_crop.face_client.embed", fake_embed)


def _link_inspiration(client, insp_id: str, blogger_id: int) -> None:
    r = client.post(f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger_id]})
    assert r.status_code == 200, r.text


def _set_avatar_from_material(client, blogger_id: int, insp_id: str):
    return client.post(f"/api/bloggers/{blogger_id}/avatar", data={"inspiration_id": insp_id})


def test_set_avatar_extracts_face_from_photo(client, create_blogger, upload, monkeypatch):
    """从素材设置头像：提取照片里的人脸，输出 256×256 正方形人像。"""
    from app.config import settings

    blogger = create_blogger(name="提人脸当头像")
    insp_id = upload(size=(200, 120)).json()["id"]  # 非正方形照片，验证确实按人脸裁
    _link_inspiration(client, insp_id, blogger["id"])
    _patch_face_embed(
        monkeypatch,
        [
            # 两张脸：应挑面积大的那张（画面主体），而不是 det_score 更高的那张
            {"bbox": [10, 10, 80, 80], "det_score": 0.9, "embedding": [0.0] * 512},
            {"bbox": [150, 80, 175, 105], "det_score": 0.99, "embedding": [0.0] * 512},
        ],
    )

    r = _set_avatar_from_material(client, blogger["id"], insp_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["face_cropped"] is True
    assert "人脸" in body["message"]
    with Image.open(settings.storage_root / body["avatar_path"]) as img:
        assert img.format == "JPEG"
        assert img.size == (FACE_CROP_SIZE, FACE_CROP_SIZE)  # 正方形人像

    # 详情接口回读（前端据此渲染）
    assert client.get(f"/api/bloggers/{blogger['id']}").json()["avatar_path"] == body["avatar_path"]


def test_set_avatar_falls_back_to_whole_photo_when_no_face(
    client, create_blogger, upload, monkeypatch
):
    """照片里没检出人脸：回退整张照片（长边 ≤512），提示说明走的是回退。"""
    from app.config import settings

    blogger = create_blogger(name="无人脸当头像")
    insp_id = upload(size=(200, 120)).json()["id"]
    _link_inspiration(client, insp_id, blogger["id"])
    _patch_no_face(monkeypatch)

    r = _set_avatar_from_material(client, blogger["id"], insp_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["face_cropped"] is False
    assert "整张照片" in body["message"]
    with Image.open(settings.storage_root / body["avatar_path"]) as img:
        assert max(img.size) <= AVATAR_MAX_SIDE
        assert img.size != (FACE_CROP_SIZE, FACE_CROP_SIZE)


def test_set_avatar_falls_back_when_face_service_unavailable(
    client, create_blogger, upload, monkeypatch
):
    """人脸子服务未配置/不可用：不报错，回退整张照片。"""
    from app.config import settings
    from app.services.face_client import FaceServiceUnavailableError

    blogger = create_blogger(name="服务不可用博")
    insp_id = upload(size=(120, 200)).json()["id"]
    _link_inspiration(client, insp_id, blogger["id"])

    async def fake_embed(image_bytes: bytes, filename: str = "image.jpg") -> dict:
        raise FaceServiceUnavailableError("未配置人脸识别子服务（FACE_SERVICE_URL）")

    monkeypatch.setattr("app.services.face_crop.face_client.embed", fake_embed)

    r = _set_avatar_from_material(client, blogger["id"], insp_id)

    assert r.status_code == 200, r.text
    assert r.json()["face_cropped"] is False
    assert (settings.storage_root / r.json()["avatar_path"]).is_file()


def test_crop_face_pads_and_clamps(make_image):
    """裁剪纯函数：bbox 外扩 20% 且 clamp 到图内，输出正方形 JPEG。"""
    from app.services.face_crop import crop_face

    data, _ctype = make_image(size=(100, 100))
    out = crop_face(data, [40, 40, 60, 60], size=64)
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (64, 64)
        assert img.format == "JPEG"
    # 越界 bbox 同样不报错（clamp 到图内）
    out2 = crop_face(data, [-20, -20, 30, 30], size=32)
    with Image.open(io.BytesIO(out2)) as img:
        assert img.size == (32, 32)


def test_set_avatar_from_upload(client, create_blogger, make_image, monkeypatch):
    """本地上传一张照片（multipart）：同样走人脸提取。"""
    from app.config import settings

    blogger = create_blogger(name="上传当头像")
    data, ctype = make_image(color=(120, 40, 200), size=(160, 160))
    _patch_face_embed(
        monkeypatch,
        [{"bbox": [30, 30, 90, 90], "det_score": 0.95, "embedding": np.zeros(512).tolist()}],
    )

    r = client.post(
        f"/api/bloggers/{blogger['id']}/avatar",
        files={"file": ("me.png", data, ctype)},
    )

    assert r.status_code == 200, r.text
    assert r.json()["face_cropped"] is True
    with Image.open(settings.storage_root / r.json()["avatar_path"]) as img:
        assert img.size == (FACE_CROP_SIZE, FACE_CROP_SIZE)


async def test_set_avatar_from_video_material_uses_thumbnail(
    client, create_blogger, upload, monkeypatch
):
    """视频素材当头像：用首帧缩略图（视频本体不能当图片解码）。"""
    from app.config import settings
    from app.models.inspiration import Inspiration

    blogger = create_blogger(name="视频当头像")
    insp = upload().json()
    _link_inspiration(client, insp["id"], blogger["id"])

    async with async_session() as db:
        row = await db.get(Inspiration, insp["id"])
        rel = f"videos/{insp['id']}.mp4"
        path = settings.storage_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        row.media_type = "video"
        row.file_path = rel
        await db.commit()

    _patch_face_embed(monkeypatch, [{"bbox": [5, 5, 40, 40], "det_score": 0.9}])

    r = _set_avatar_from_material(client, blogger["id"], insp["id"])

    assert r.status_code == 200, r.text
    assert r.json()["face_cropped"] is True
    assert (settings.storage_root / r.json()["avatar_path"]).is_file()


def test_set_avatar_rejects_bad_sources(client, create_blogger, upload, make_image):
    """参数与来源校验：二选一、素材必须属于该博主、坏图 400、博主不存在 404。"""
    blogger = create_blogger(name="校验头像博")
    other = create_blogger(name="别人的素材")
    insp_id = upload().json()["id"]
    _link_inspiration(client, insp_id, other["id"])
    data, ctype = make_image()

    both = client.post(
        f"/api/bloggers/{blogger['id']}/avatar",
        data={"inspiration_id": insp_id},
        files={"file": ("me.jpg", data, ctype)},
    )
    assert both.status_code == 400 and "二选一" in both.json()["detail"]

    none = client.post(f"/api/bloggers/{blogger['id']}/avatar")
    assert none.status_code == 400

    # 素材属于别人 → 拒绝（不借头像接口读任意素材）
    foreign = _set_avatar_from_material(client, blogger["id"], insp_id)
    assert foreign.status_code == 400 and "不属于这位博主" in foreign.json()["detail"]

    missing_insp = _set_avatar_from_material(client, blogger["id"], "not-a-real-id")
    assert missing_insp.status_code == 400

    # 坏图（不是图片字节）→ 400 人话提示（归一化阶段就失败，不经过人脸子服务）
    broken = client.post(
        f"/api/bloggers/{blogger['id']}/avatar",
        files={"file": ("broken.jpg", b"definitely-not-an-image", "image/jpeg")},
    )
    assert broken.status_code == 400 and "无法解析" in broken.json()["detail"]

    missing_blogger = client.post(
        "/api/bloggers/999999/avatar", files={"file": ("me.jpg", data, ctype)}
    )
    assert missing_blogger.status_code == 404


def test_set_avatar_overwrites_previous(client, create_blogger, upload, make_image):
    """再次设置头像：同名覆盖，不产生多余文件，内容随之变化。"""
    from app.config import settings

    blogger = create_blogger(name="换头像博")
    first_insp = upload(color=(10, 10, 200)).json()["id"]
    _link_inspiration(client, first_insp, blogger["id"])
    first = _set_avatar_from_material(client, blogger["id"], first_insp).json()
    first_bytes = (settings.storage_root / first["avatar_path"]).read_bytes()

    data, ctype = make_image(color=(240, 30, 30))
    second = client.post(
        f"/api/bloggers/{blogger['id']}/avatar", files={"file": ("new.jpg", data, ctype)}
    ).json()

    assert second["avatar_path"] == first["avatar_path"]  # 固定命名，覆盖式
    assert (settings.storage_root / second["avatar_path"]).read_bytes() != first_bytes
    # avatars/ 下只有这一张（按人物 id 命名）
    files = list((settings.storage_root / "avatars").glob(f"avatar_{blogger['id']}.*"))
    assert len(files) == 1


def test_clear_avatar(client, create_blogger, upload):
    """清除头像：avatar_path 置空 + 文件删除（前端回退首字占位）。"""
    from app.config import settings

    blogger = create_blogger(name="清头像博")
    insp_id = upload().json()["id"]
    _link_inspiration(client, insp_id, blogger["id"])
    avatar_path = _set_avatar_from_material(client, blogger["id"], insp_id).json()["avatar_path"]
    assert (settings.storage_root / avatar_path).is_file()

    r = client.delete(f"/api/bloggers/{blogger['id']}/avatar")

    assert r.status_code == 200, r.text
    assert r.json()["avatar_path"] is None
    assert not (settings.storage_root / avatar_path).exists()


def test_delete_blogger_removes_avatar_file(client, create_blogger, upload):
    """删除博主：头像文件一并清理，不残留孤儿文件。"""
    from app.config import settings

    blogger = create_blogger(name="待删头像博")
    insp_id = upload().json()["id"]
    _link_inspiration(client, insp_id, blogger["id"])
    avatar_path = _set_avatar_from_material(client, blogger["id"], insp_id).json()["avatar_path"]
    # 删除博主要求无关联素材：先解绑
    client.delete(f"/api/inspirations/{insp_id}/bloggers/{blogger['id']}")

    r = client.delete(f"/api/bloggers/{blogger['id']}")

    assert r.status_code == 204
    assert not (settings.storage_root / avatar_path).exists()
