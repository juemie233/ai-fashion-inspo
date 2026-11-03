"""生成历史多姿态合并展示（group_id）回归测试。

底层仍是每个姿态一个独立 GenerationRun/任务；同一次提交的批次共用一个
32 位十六进制 group_id，历史列表按组合并成一行（候选图/进度/状态聚合），
展开时前端逐批拉详情。

覆盖：
1. 多姿态组在列表里合并成一行，total 按「组」计，run_ids 完整；
2. 组内候选图带 run_id，状态/张数取聚合；
3. 筛选只命中组内部分批次时，整组仍完整返回；
4. create_run 接受合法 group_id、拒绝非法格式；
5. 详情接口候选图带 run_id（多姿态入库按批调用所需）。
"""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun
from app.utils.time import utcnow

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
GROUP_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _seed_run(
    *,
    status: str = "done",
    prompt: str = "多姿态批次",
    group_id: str | None = None,
    total: int = 1,
    done: int = 1,
    failed: int = 0,
    created_offset: int = 0,
    write_candidate: bool = True,
) -> int:
    """直接建一条批次；group_id 相同即为同一次多姿态提交。"""

    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind="i2i",
                status=status,
                prompt=prompt,
                negative=None,
                params_json='{"pose_id": "p1", "size": "1536x2048"}',
                model_key="seedream_5_0_flash",
                workflow_name="ark_seedream",
                total=total,
                done=done,
                failed=failed,
                group_id=group_id,
                created_at=utcnow() + timedelta(seconds=created_offset),
                updated_at=utcnow() + timedelta(seconds=created_offset),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            run.batch_dir = f"generated/{run.id}"
            if write_candidate:
                folder = settings.storage_root / run.batch_dir
                folder.mkdir(parents=True, exist_ok=True)
                for suffix in ("01_seed-0.png", "02_seed-0.png"):
                    (folder / suffix).write_bytes(TINY_PNG)
            await db.commit()
            return run.id

    return asyncio.run(_do())


def _delete_runs(*run_ids: int) -> None:
    async def _do() -> None:
        async with async_session() as db:
            for run_id in run_ids:
                run = await db.get(GenerationRun, run_id)
                if run is not None:
                    folder = settings.storage_root / run.batch_dir
                    if folder.exists():
                        import shutil

                        shutil.rmtree(folder, ignore_errors=True)
                    await db.delete(run)
            await db.commit()

    asyncio.run(_do())


def test_history_merges_multi_pose_group(client):
    """同一次多姿态提交的 3 个独立批次 → 历史里合并成 1 行。"""
    first = _seed_run(group_id=GROUP_ID, created_offset=0)
    second = _seed_run(group_id=GROUP_ID, created_offset=1)
    third = _seed_run(group_id=GROUP_ID, created_offset=2)
    single = _seed_run(group_id=None, created_offset=3, write_candidate=False)
    try:
        r = client.get("/api/generate/runs", params={"limit": 100})
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["items"]

        group_row = next(item for item in items if item["id"] in (first, second, third))
        assert group_row["group_id"] == GROUP_ID
        assert group_row["run_ids"] == [first, second, third]
        assert group_row["total"] == 3
        assert group_row["done"] == 3
        assert group_row["candidate_total"] == 6
        # 合并行总量按「组」计：3 个姿态 1 行 + 1 个单批次 1 行
        assert body["total"] == 2

        single_row = next(item for item in items if item["id"] == single)
        assert single_row["run_ids"] == [single]
        assert single_row.get("group_id") is None
    finally:
        _delete_runs(first, second, third, single)


def test_group_candidates_carry_run_id(client):
    """合并行的候选图要带 run_id，前端才能按底层批次分别入库。"""
    first = _seed_run(group_id=GROUP_ID, created_offset=0)
    second = _seed_run(group_id=GROUP_ID, created_offset=1)
    try:
        rows = client.get("/api/generate/runs", params={"limit": 100}).json()["items"]
        group_row = next(item for item in rows if item["group_id"] == GROUP_ID)
        candidate_run_ids = {item["run_id"] for item in group_row["candidates"]}
        assert candidate_run_ids == {first, second}
    finally:
        _delete_runs(first, second)


def test_group_status_is_aggregated(client):
    """有批次在跑 → 组显示在跑；全部失败 → 组显示失败。"""
    done_id = _seed_run(group_id=GROUP_ID, created_offset=0, write_candidate=False)
    running_id = _seed_run(
        group_id=GROUP_ID,
        status="running",
        done=0,
        created_offset=1,
        write_candidate=False,
    )
    try:
        rows = client.get("/api/generate/runs", params={"limit": 100}).json()["items"]
        row = next(item for item in rows if item["group_id"] == GROUP_ID)
        assert row["status"] == "running"
    finally:
        _delete_runs(done_id, running_id)

    fail_a = _seed_run(
        group_id=GROUP_ID, status="failed", done=0, failed=1, created_offset=0, write_candidate=False
    )
    fail_b = _seed_run(
        group_id=GROUP_ID, status="failed", done=0, failed=1, created_offset=1, write_candidate=False
    )
    try:
        rows = client.get("/api/generate/runs", params={"limit": 100}).json()["items"]
        row = next(item for item in rows if item["group_id"] == GROUP_ID)
        assert row["status"] == "failed"
        assert row["failed"] == 2
    finally:
        _delete_runs(fail_a, fail_b)


def test_group_filter_returns_whole_group(client):
    """筛选只命中组内一个批次时，合并行仍要带全整组的批次。"""
    first = _seed_run(group_id=GROUP_ID, prompt="命中关键字的姿态", created_offset=0)
    second = _seed_run(group_id=GROUP_ID, prompt="另一个姿态", created_offset=1)
    try:
        r = client.get("/api/generate/runs", params={"limit": 100, "q": "命中关键字"})
        rows = r.json()["items"]
        row = next(item for item in rows if item["group_id"] == GROUP_ID)
        assert row["run_ids"] == [first, second]
    finally:
        _delete_runs(first, second)


def test_create_run_accepts_valid_group_id(client):
    payload = {
        "kind": "t2i",
        "prompt": "分组提交测试",
        "model_key": "z_image_int8",
        "group_id": GROUP_ID,
    }
    r = client.post("/api/generate/runs", json=payload)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    try:
        rows = client.get("/api/generate/runs", params={"limit": 100}).json()["items"]
        row = next(item for item in rows if item["id"] == run_id)
        assert row["group_id"] == GROUP_ID
    finally:
        _delete_runs(run_id)

    bad = client.post("/api/generate/runs", json={**payload, "group_id": "not-a-group"})
    assert bad.status_code == 422


def test_detail_candidates_carry_run_id(client):
    run_id = _seed_run(group_id=None)
    try:
        detail = client.get(f"/api/generate/runs/{run_id}").json()
        assert detail["items"], detail
        assert all(item["run_id"] == run_id for item in detail["items"])
    finally:
        _delete_runs(run_id)
