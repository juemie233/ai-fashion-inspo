"""博主人像头像（手动设置）测试：从素材选一张 / 本地上传 / 覆盖 / 清除 / 级联清理。

头像链路：选定照片 → 统一重编码为 JPEG（长边 ≤512）→ ``storage/avatars/avatar_{id}.jpg``
→ 写 ``avatar_path``；展示优先级为「手动头像 → 人脸小图 → 首字」。
"""

from PIL import Image

from app.database import async_session
from app.services.person.avatar import AVATAR_MAX_SIDE, avatar_rel_path


def _link_inspiration(client, insp_id: str, blogger_id: int) -> None:
    r = client.post(f"/api/inspirations/{insp_id}/bloggers", json={"person_ids": [blogger_id]})
    assert r.status_code == 200, r.text


def _set_avatar_from_material(client, blogger_id: int, insp_id: str):
    return client.post(
        f"/api/bloggers/{blogger_id}/avatar", data={"inspiration_id": insp_id}
    )


def test_set_avatar_from_linked_material(client, create_blogger, upload, make_image):
    """从该博主的素材里选一张：落盘为 JPEG 头像（长边 ≤512）并写 avatar_path。"""
    from app.config import settings

    blogger = create_blogger(name="选素材当头像")
    insp_id = upload().json()["id"]
    _link_inspiration(client, insp_id, blogger["id"])

    r = _set_avatar_from_material(client, blogger["id"], insp_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["avatar_path"] == avatar_rel_path(blogger["id"])

    avatar_file = settings.storage_root / body["avatar_path"]
    assert avatar_file.is_file()
    with Image.open(avatar_file) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= AVATAR_MAX_SIDE

    # 详情接口回读（前端据此渲染）
    detail = client.get(f"/api/bloggers/{blogger['id']}").json()
    assert detail["avatar_path"] == body["avatar_path"]


def test_set_avatar_from_upload(client, create_blogger, make_image):
    """本地上传一张照片（用真实图片字节，走 multipart）。"""
    from app.config import settings

    blogger = create_blogger(name="上传当头像")
    data, ctype = make_image(color=(120, 40, 200))

    r = client.post(
        f"/api/bloggers/{blogger['id']}/avatar",
        files={"file": ("me.png", data, ctype)},
    )

    assert r.status_code == 200, r.text
    avatar_file = settings.storage_root / r.json()["avatar_path"]
    assert avatar_file.is_file()
    with Image.open(avatar_file) as img:
        assert img.format == "JPEG"


async def test_set_avatar_from_video_material_uses_thumbnail(client, create_blogger, upload):
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

    r = _set_avatar_from_material(client, blogger["id"], insp["id"])

    assert r.status_code == 200, r.text
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

    # 坏图（不是图片字节）→ 400 人话提示
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
    """清除头像：avatar_path 置空 + 文件删除（回退到人脸小图/首字）。"""
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
