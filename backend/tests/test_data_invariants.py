"""数据不变量测试：垃圾桶三字段一致性、平台 ID 生命周期、批次↔素材关联。

这些不变量一旦破了，不会立刻报错，而是让「按原因筛选/负样本学习/重新采集/按批次反查」
悄悄失真。已有 `verify_trash_invariants` 扫描函数，但缺「正常业务流跑完仍然健康」的
回归断言；本文件在真实 API 流程后调用它，并补平台 ID/批次关联的跨接口校验。
"""

import uuid

from app.database import async_session
from app.services.inspiration_state import verify_trash_invariants


async def _assert_trash_invariants_ok():
    async with async_session() as db:
        violations = await verify_trash_invariants(db)
    assert violations == [], f"垃圾桶不变量被破坏：{violations}"


class TestTrashInvariants:
    async def test_single_trash_and_restore_keep_invariants(self, client, upload):
        inspiration_id = upload().json()["id"]

        trashed = client.post(
            f"/api/inspirations/{inspiration_id}/trash", json={"reason": "重复"}
        )
        assert trashed.status_code == 200, trashed.text
        body = trashed.json()
        assert body["deleted_at"] and body["trash_reason"] == "重复" and body["trash_source"]
        await _assert_trash_invariants_ok()

        restored = client.post(f"/api/inspirations/{inspiration_id}/restore")
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["deleted_at"] is None
        assert body["trash_reason"] is None
        assert body["trash_source"] is None
        await _assert_trash_invariants_ok()

    async def test_batch_trash_keeps_invariants(self, client, upload):
        ids = [upload().json()["id"] for _ in range(3)]

        response = client.post(
            "/api/inspirations/batch-trash", json={"ids": ids, "reason": "不喜欢"}
        )
        assert response.status_code == 200, response.text
        await _assert_trash_invariants_ok()

        trash_list = client.get("/api/inspirations/trash", params={"size": 100}).json()
        assert {item["id"] for item in trash_list["items"]} >= set(ids)

    async def test_auto_trash_source_is_recorded(self, client, upload):
        """质量审核自动移入垃圾桶时 source=auto（统计自动清理只看这个字段）。"""
        inspiration_id = upload().json()["id"]

        response = client.post(
            f"/api/inspirations/{inspiration_id}/trash",
            json={"reason": "质量差", "source": "auto"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["trash_source"] == "auto"
        await _assert_trash_invariants_ok()


class TestPlatformIdLifecycle:
    async def test_platform_id_unique_among_live_and_released_by_trash(self, client, upload):
        platform_id = f"system-test-{uuid.uuid4().hex[:8]}"

        first = client.post(
            "/api/inspirations",
            files={"file": ("p1.png", _png((10, 20, 30)), "image/png")},
            data={"source_type": "manual_upload", "source_platform_id": platform_id},
        )
        assert first.status_code == 201, first.text
        inspiration_id = first.json()["id"]

        # 未删除素材占用平台 ID：插件预检应报 exists=true
        check = client.get("/api/inspirations/check-platform-id", params={"platform_id": platform_id})
        assert check.json()["exists"] is True

        # 移入垃圾桶后释放平台 ID：允许重新采集
        client.post(f"/api/inspirations/{inspiration_id}/trash", json={"reason": "重复"})
        check = client.get("/api/inspirations/check-platform-id", params={"platform_id": platform_id})
        assert check.json()["exists"] is False


class TestGenerationRunLinks:
    async def test_candidates_confirm_links_material_to_run(self, client, upload):
        """确认入库的素材必须带 generation_run_id，且批次导入计数对得上。"""
        run = await _create_generation_run_with_candidate()
        run_id, filename = run

        confirmed = client.post(
            f"/api/generate/runs/{run_id}/confirm", json={"filenames": [filename]}
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["imported"] == 1 and body["ids"]

        listing = client.get("/api/inspirations", params={"generation_run_id": run_id})
        assert listing.status_code == 200
        assert {item["id"] for item in listing.json()["items"]} == set(body["ids"])


def _png(color: tuple[int, int, int]) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _create_generation_run_with_candidate() -> tuple[int, str]:
    """直接造一个「批次 + 候选文件」，避免为入库链接测试再跑一遍 worker/ComfyUI。"""
    import asyncio

    from app.config import settings
    from app.models.generation import GenerationRun

    run_id: int
    async with async_session() as db:
        run = GenerationRun(
            kind="t2i",
            status="done",
            prompt="不变量测试",
            model_key="z_image_int8",
            total=1,
            done=1,
            params_json="{}",
            batch_dir="",
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id
        run.batch_dir = f"generated/{run_id}"
        await db.commit()

    folder = settings.storage_root / f"generated/{run_id}"
    folder.mkdir(parents=True, exist_ok=True)
    filename = "01_seed-1.png"
    (folder / filename).write_bytes(_png((1, 2, 3)))
    await asyncio.sleep(0)
    return run_id, filename
