"""手机截图裁剪编排服务的测试（apply_crops 的跳过判定 / 去重决策 / 回滚）。

裁剪是**破坏性操作**：它直接替换原图文件。所以这一层的测试价值集中在三处：

1. **跳过原因逐条准确**：用户勾了 20 张只想裁其中 5 张，必须知道另外 15 张为什么没动
   （非手动上传 / 非竖屏 / 文件缺失 / 已裁过），否则会以为"点了没反应"；
2. **内容重复不自动丢弃**：裁完与库里某张内容相同 → 生成左右对比预览交用户决策，
   而不是默默丢掉用户的图；
3. **失败要能回滚**：原图已被替换、后续步骤（缩略图/主色调）失败时必须从备份恢复，
   否则磁盘与数据库就永久不一致。

图像算法（黑边/边界检测）与缩略图都在本层之外，用 monkeypatch 替身；
文件系统用真实临时目录（配 `settings.storage_root`）。
"""

import json
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.database import async_session
from app.models.inspiration import Inspiration
from app.services import crop_service


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """独立的 storage 根 + 内置备份/重复预览目录都落在这里。"""
    monkeypatch.setattr(settings, "storage_root", tmp_path, raising=False)
    monkeypatch.setattr(
        crop_service, "_resolve_storage_path", lambda rel: (tmp_path / rel) if rel else None
    )
    # 尺寸探测：默认视为竖屏手机截图（1080x2400）
    monkeypatch.setattr(crop_service, "_probe_size", lambda path: (1080, 2400))
    # 裁剪：把 tmp 写成一个真实文件（内容标记为 cropped）
    def _crop(src, top, bottom, *args, **kwargs):
        out = tmp_path / "crop_tmp.jpg"
        out.write_bytes(b"cropped-content")
        return out

    monkeypatch.setattr(crop_service, "crop_image_to_temp", _crop)
    # 哈希：按内容区分，便于构造重复
    monkeypatch.setattr(
        crop_service, "file_sha256", lambda path: __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    )
    # 缩略图：默认成功（返回相对路径）
    async def _thumb(path):
        rel = f"thumbnails/{path.stem}.jpg"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"thumb")
        return rel

    monkeypatch.setattr(crop_service, "generate_thumbnail", _thumb)
    monkeypatch.setattr(crop_service, "extract_dominant_colors", lambda path: ["#ffffff"])
    # 黑边检测：默认「顶部 100px、底部 200px」
    monkeypatch.setattr(crop_service, "detect_photo_band", lambda path: (100, 1400))
    return tmp_path


async def _insp(
    insp_id: str,
    *,
    source_type: str = "manual_upload",
    media_type: str = "image",
    deleted: bool = False,
    content_hash: str | None = None,
    colors: str | None = None,
    write_file: bool = True,
    rel: str | None = None,
) -> str:
    rel = rel or f"images/2026-10/{insp_id}.jpg"
    if write_file:
        path = settings.storage_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"original-{insp_id}".encode())
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type=source_type,
                file_path=rel,
                media_type=media_type,
                quality_status="approved",
                content_hash=content_hash,
                dominant_colors=colors,
                thumbnail_path=f"thumbnails/{insp_id}.jpg",
                deleted_at=datetime.now(timezone.utc) if deleted else None,
            )
        )
        await db.commit()
    return insp_id


# ── 参数校验 ──


async def test_apply_rejects_unknown_mode(client):
    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await crop_service.apply_crops(db, ["x"], mode="magic")
    assert "不支持的裁剪模式" in str(exc.value)


async def test_apply_rejects_ratio_sum_over_one(client):
    async with async_session() as db:
        with pytest.raises(ValueError) as exc:
            await crop_service.apply_crops(db, ["x"], mode="ratio", crop_top=0.6, crop_bottom=0.5)
    assert "必须 < 1" in str(exc.value)


# ── 跳过判定 ──


@pytest.mark.parametrize(
    "kwargs,reason_part",
    [
        ({"source_type": "douyin"}, "仅支持处理手动上传素材"),
        ({"media_type": "video"}, "非图片素材"),
        ({"deleted": True}, "已入垃圾桶"),
        ({"write_file": False}, "文件不存在"),
    ],
)
async def test_skip_reasons(client, kwargs, reason_part):
    insp_id = await _insp("skip1", **kwargs)

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    assert result["processed"] == 0
    assert result["skipped"]
    assert reason_part in result["skipped"][0]["reason"]


async def test_skip_path_outside_storage_root(client, monkeypatch):
    """路径越出存储根（历史脏数据）按「路径非法」跳过，而不是去读库外文件。"""
    monkeypatch.setattr(crop_service, "_resolve_storage_path", lambda rel: None)
    insp_id = await _insp("nopath1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio")

    assert "文件路径为空" in result["skipped"][0]["reason"]


async def test_skip_missing_record(client):
    async with async_session() as db:
        result = await crop_service.apply_crops(db, ["不存在"], mode="ratio")
    assert result["skipped"] == [{"id": "不存在", "reason": "记录不存在"}]


async def test_skip_non_portrait(client, monkeypatch):
    """高/宽 < 阈值（横图/方图）不参与裁剪。"""
    monkeypatch.setattr(crop_service, "_probe_size", lambda path: (2400, 1080))
    insp_id = await _insp("wide1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio")

    assert "非竖屏" in result["skipped"][0]["reason"]


async def test_content_mode_allows_wider_ratio(client, monkeypatch):
    """content 模式放宽下限（被裁过的截图比例可能低至 1.3）。"""
    monkeypatch.setattr(crop_service, "_probe_size", lambda path: (1000, 1400))  # 1.4
    insp_id = await _insp("content1")

    def _combined(path):
        return {}, {
            "top_frac": 0.1,
            "bottom_frac": 0.0,
            "residual_top_frac": 0,
            "residual_bottom_frac": 0,
            "already_cropped": False,
        }

    monkeypatch.setattr(crop_service, "analyze_screenshot_combined", _combined)

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="content")

    assert result["processed"] == 1


async def test_skip_undecodable(client, monkeypatch):
    def _boom(path):
        raise OSError("坏文件")

    monkeypatch.setattr(crop_service, "_probe_size", _boom)
    insp_id = await _insp("bad1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio")

    assert "无法解码" in result["skipped"][0]["reason"]


async def test_detect_failure_recorded_as_skip(client, monkeypatch):
    def _fail(path):
        raise ValueError("未检测到照片区域")

    monkeypatch.setattr(crop_service, "detect_photo_band", _fail)
    insp_id = await _insp("det1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="auto")

    assert "自动检测失败" in result["skipped"][0]["reason"]


async def test_content_mode_skips_already_cropped(client, monkeypatch):
    """无截图特征且检测无建议 → 视为已裁干净，跳过（不白裁一遍）。"""
    insp_id = await _insp("done1")

    def _combined(path):
        return {}, {
            "top_frac": 0,
            "bottom_frac": 0,
            "residual_top_frac": 0,
            "residual_bottom_frac": 0,
            "already_cropped": True,
        }

    monkeypatch.setattr(crop_service, "analyze_screenshot_combined", _combined)

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="content")

    assert "无需裁剪" in result["skipped"][0]["reason"]


async def test_content_mode_rejects_zero_ratio(client, monkeypatch):
    """有截图特征但算不出比例 → 明确跳过，绝不裁剪 0/0 制造假成功。"""
    insp_id = await _insp("zero1")

    def _combined(path):
        return {"top_bar": True}, {
            "top_frac": 0,
            "bottom_frac": 0,
            "residual_top_frac": 0,
            "residual_bottom_frac": 0,
            "already_cropped": False,
        }

    monkeypatch.setattr(crop_service, "analyze_screenshot_combined", _combined)

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="content")

    assert result["processed"] == 0
    assert "未检出可裁区域" in result["skipped"][0]["reason"]


# ── 正常裁剪 ──


async def test_apply_ratio_success(client):
    insp_id = await _insp("ok1", colors=json.dumps(["#000000"]))

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    assert result["processed"] == 1
    assert result["backup_dir"]
    async with async_session() as db:
        refreshed = await db.get(Inspiration, insp_id)
    # 内容哈希与主色重算、感知哈希作废、缩略图指向新文件
    assert refreshed.content_hash
    assert refreshed.phash is None
    assert refreshed.dominant_colors
    # 原图已被替换为新内容
    assert (settings.storage_root / refreshed.file_path).read_bytes() == b"cropped-content"


async def test_apply_dedups_repeated_ids(client):
    """同一素材重复勾选只处理一次（前端多选很容易重复传）。"""
    insp_id = await _insp("dup1")

    async with async_session() as db:
        result = await crop_service.apply_crops(
            db, [insp_id, insp_id, insp_id], mode="ratio", crop_top=0.1
        )

    assert result["processed"] == 1


async def test_apply_creates_backup(client):
    insp_id = await _insp("bak1")
    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    backups = list((settings.storage_root / result["backup_dir"]).glob("*"))
    assert backups, "裁剪前必须先备份原图"
    assert backups[0].read_bytes() == b"original-bak1"


# ── 内容重复：交给用户决策，不自动丢 ──


async def test_duplicate_result_returns_preview_instead_of_dropping(client):
    """裁完与库里另一张内容相同 → 返回预览交用户决策（不默默丢弃）。"""
    await _insp("orig2", content_hash="whatever")
    insp_id = await _insp("dup2")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    # 本次裁剪结果与 orig2 的 content_hash 不同，所以不该判重
    assert result["duplicates"] == []
    assert result["processed"] == 1


async def test_duplicate_detected_when_hash_matches_other_material(client):
    """构造「裁剪结果哈希 == 库内另一张」：应产出 duplicates 条目并保留预览。"""
    import hashlib

    cropped_hash = hashlib.sha256(b"cropped-content").hexdigest()
    await _insp("keeper", content_hash=cropped_hash)
    insp_id = await _insp("cand1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    assert result["processed"] == 0
    assert result["duplicates"]
    dup = result["duplicates"][0]
    assert dup["dup_id"] == "keeper"
    assert dup["preview_path"]
    # 原图未被替换（等用户决定）
    async with async_session() as db:
        refreshed = await db.get(Inspiration, insp_id)
    assert (settings.storage_root / refreshed.file_path).read_bytes() == b"original-cand1"


# ── 失败回滚 ──


async def test_thumbnail_failure_rolls_back_original(client, monkeypatch):
    """缩略图生成抛异常时必须从备份恢复原图（磁盘与 DB 不能不一致）。"""
    insp_id = await _insp("rb1")

    async def _boom(path):
        raise OSError("缩略图写入失败")

    monkeypatch.setattr(crop_service, "generate_thumbnail", _boom)

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    assert result["processed"] == 0
    # 原图已回滚
    async with async_session() as db:
        refreshed = await db.get(Inspiration, insp_id)
    assert (settings.storage_root / refreshed.file_path).read_bytes() == b"original-rb1"


async def test_thumbnail_returns_none_clears_thumb_path(client, monkeypatch):
    """缩略图生成返回 None（不抛错）时：置空缩略图路径，避免前端展示错配的旧图。"""

    async def _none(path):
        return None

    monkeypatch.setattr(crop_service, "generate_thumbnail", _none)
    insp_id = await _insp("nothumb1")

    async with async_session() as db:
        result = await crop_service.apply_crops(db, [insp_id], mode="ratio", crop_top=0.1)

    assert result["processed"] == 1
    async with async_session() as db:
        refreshed = await db.get(Inspiration, insp_id)
    assert refreshed.thumbnail_path is None


# ── 手动区域裁剪（素材详情页拖动分割线）──


@pytest.fixture(autouse=True)
def _patch_manual_crop_deps(monkeypatch, tmp_path):
    """手动裁剪链路的外部依赖：区域裁剪、感知哈希、回填登记、审计。"""

    def _crop_region(src, y1, y2, *args, **kwargs):
        out = tmp_path / "region_tmp.jpg"
        out.write_bytes(b"region-cropped")
        return out

    monkeypatch.setattr(crop_service, "crop_region_to_temp", _crop_region)
    monkeypatch.setattr(crop_service, "perceptual_hash", lambda path: "phash-new")

    async def _load_full(db, insp_id):
        return await db.get(Inspiration, insp_id)

    monkeypatch.setattr(crop_service, "load_inspiration_full", _load_full)

    async def _enqueue(db, ids):
        return len(ids)

    monkeypatch.setattr(crop_service, "enqueue_vector_backfills", _enqueue)

    async def _audit(**kwargs):
        return None

    import app.services.audit_service as audit_mod

    monkeypatch.setattr(audit_mod, "record_audit_log", _audit)
    return tmp_path


async def test_manual_crop_success_updates_derived_fields(client):
    from app.models.inspiration import Inspiration as Insp

    insp_id = await _insp("mc1", colors=json.dumps(["#111111"]))

    async with async_session() as db:
        insp = await crop_service.crop_inspiration_region(db, insp_id, 0.1, 0.9)

    assert insp.content_hash
    assert insp.phash == "phash-new"
    assert insp.dominant_colors
    assert (settings.storage_root / insp.file_path).read_bytes() == b"region-cropped"


async def test_manual_crop_deletes_backup_on_success(client):
    """手动裁剪成功后立即删备份（否则备份无限累积）。"""
    insp_id = await _insp("mc2")

    async with async_session() as db:
        await crop_service.crop_inspiration_region(db, insp_id, 0.1, 0.9)

    backups = list((settings.storage_root / "_crop_backup").glob("*/*")) if (
        settings.storage_root / "_crop_backup"
    ).exists() else []
    assert backups == []


@pytest.mark.parametrize(
    "y1,y2,detail_part",
    [
        (0.5, 0.5, "裁剪比例非法"),
        (0.9, 0.1, "裁剪比例非法"),
        (-0.1, 0.5, "裁剪比例非法"),
        (1.0, 1.2, "裁剪比例非法"),
    ],
)
async def test_manual_crop_rejects_bad_ratio(client, y1, y2, detail_part):
    from fastapi import HTTPException

    insp_id = await _insp("mc3")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, insp_id, y1, y2)
    assert exc.value.status_code == 400
    assert detail_part in str(exc.value.detail)


async def test_manual_crop_rejects_too_small_region(client):
    from fastapi import HTTPException

    insp_id = await _insp("mc4")
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            # 高度 2400 × 1% = 24px < MIN_MANUAL_CROP_HEIGHT_PX
            await crop_service.crop_inspiration_region(db, insp_id, 0.5, 0.51)
    assert exc.value.status_code == 400
    assert "保留区域高度过小" in str(exc.value.detail)


async def test_manual_crop_rejects_missing_and_trashed_and_video(client):
    from fastapi import HTTPException

    trashed = await _insp("mc5", deleted=True)
    video = await _insp("mc6", media_type="video")

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, "不存在", 0.1, 0.9)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, trashed, 0.1, 0.9)
        assert "垃圾桶" in str(exc.value.detail)

        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, video, 0.1, 0.9)
        assert "仅支持裁剪图片" in str(exc.value.detail)


async def test_manual_crop_missing_file(client, monkeypatch):
    from fastapi import HTTPException

    insp_id = await _insp("mc7")
    monkeypatch.setattr(crop_service, "_resolve_storage_path", lambda rel: None)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, insp_id, 0.1, 0.9)
    assert "文件缺失" in str(exc.value.detail)


async def test_manual_crop_rolls_back_when_later_step_fails(client, monkeypatch):
    """替换原图后失败（commit 之前）必须从备份恢复，磁盘与库保持一致。"""
    from fastapi import HTTPException

    insp_id = await _insp("mc8")

    async def _boom(path):
        raise OSError("缩略图挂了")

    monkeypatch.setattr(crop_service, "generate_thumbnail", _boom)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, insp_id, 0.1, 0.9)
        assert exc.value.status_code == 500

    async with async_session() as db:
        refreshed = await db.get(Inspiration, insp_id)
    assert (settings.storage_root / refreshed.file_path).read_bytes() == b"original-mc8"


async def test_manual_crop_propagates_http_exception_unchanged(client, monkeypatch):
    """已经是 HTTPException 的不要被包成 500（否则 400 的文案变「裁剪处理失败」）。"""
    from fastapi import HTTPException

    insp_id = await _insp("mc9")

    def _raise_400(*args, **kwargs):
        raise HTTPException(status_code=400, detail="自定义错误")

    monkeypatch.setattr(crop_service, "crop_region_to_temp", _raise_400)

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await crop_service.crop_inspiration_region(db, insp_id, 0.1, 0.9)
    assert exc.value.status_code == 400
    assert exc.value.detail == "自定义错误"
