"""多模型 × 多提示词组合分析（multi_analyze）与批量对比 / 应用到素材的集成测试。

纯 DB 测试（不调 Ollama）：组合分析执行器通过 monkeypatch 替换
analyze_image 为写库桩函数，验证每个组合独立产生分析日志与标签快照、
apply_tags=False 时不修改素材标签、批量对比与应用到素材接口的行为。
"""

import json

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag, Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.ai_tag_saver import iter_extracted_tags
from app.services.task_runners.batch_analyze import execute_multi_analyze


def _fake_analyze_image_factory():
    """生成 analyze_image 桩函数：按真实链路写日志 + 标签快照（标签名含模型名便于区分组合）。"""

    async def fake_analyze_image(
        db, inspiration_id, file_path, model_name=None, prompt=None, apply_tags=True
    ):
        from app.services.ai_service.analyze import _parse_and_save_tags, _write_analysis_log

        used_model = model_name or "default-model"
        tags_data = {"style": [f"{used_model}风格"]}
        raw = json.dumps(tags_data, ensure_ascii=False)
        extracted = list(iter_extracted_tags(tags_data))
        _, _, error_msg = await _parse_and_save_tags(
            db, inspiration_id, raw, apply_tags=apply_tags
        )
        assert error_msg is None
        await db.commit()
        await _write_analysis_log(
            db,
            inspiration_id,
            prompt or "default-prompt",
            raw,
            None,
            100,
            extracted,
            model_name=used_model,
        )
        return True

    return fake_analyze_image


async def test_multi_task_created_via_api(client, upload):
    """组合分析请求（对象格式请求体）创建 multi_analyze 任务并持久化组合列表。"""
    a = upload().json()["id"]
    b = upload().json()["id"]

    r = client.post(
        "/api/ai/batch-analyze",
        json={
            "inspiration_ids": [a, b],
            "models": ["model-a", "model-b"],
            "prompt_ids": [0],
            "apply_tags": False,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["combinations"] == 2
    assert data["count"] == 2

    async with async_session() as db:
        from app.models.task import TaskQueue

        task = await db.get(TaskQueue, data["task_id"])
        assert task.type == "multi_analyze"
        assert task.total == 4  # 2 素材 × 2 组合
        combos = task.result["combinations"]
        assert {c["model"] for c in combos} == {"model-a", "model-b"}
        assert task.result["apply_tags"] is False


async def test_execute_multi_analyze_writes_log_per_combination(client, upload, monkeypatch):
    """执行器按组合逐个分析：每个组合独立日志 + 快照，素材标签保持不变。"""
    insp_id = upload().json()["id"]
    monkeypatch.setattr(
        "app.services.task_runners.batch_analyze.analyze_image",
        _fake_analyze_image_factory(),
    )

    from app.services.task_runners.batch_analyze import create_multi_analyze_task

    combinations = [
        {"model": "model-a", "prompt": "prompt-a", "prompt_label": "版本 #1"},
        {"model": "model-b", "prompt": None, "prompt_label": "当前默认提示词"},
    ]
    async with async_session() as db:
        task = await create_multi_analyze_task(
            db, [insp_id], combinations, apply_tags=False
        )
        task_id = task.id

    async with async_session() as db:
        from app.models.task import TaskQueue

        task = await db.get(TaskQueue, task_id)
        await execute_multi_analyze(db, task)

        # 每个组合一条独立日志
        logs = (
            await db.execute(
                select(AIAnalysisLog)
                .where(AIAnalysisLog.inspiration_id == insp_id)
                .order_by(AIAnalysisLog.id)
            )
        ).scalars().all()
        assert {log.model_name for log in logs} == {"model-a", "model-b"}
        assert all(log.error is None for log in logs)
        # 快照随日志落库（每个组合 1 个标签）
        snap_counts = []
        for log in logs:
            snaps = (
                await db.execute(
                    select(AIAnalysisTag).where(AIAnalysisTag.log_id == log.id)
                )
            ).scalars().all()
            assert len(snaps) == 1
            snap_counts.append(len(snaps))
        assert sum(snap_counts) == 2

        # apply_tags=False：素材正式标签（inspiration_tags）保持为空
        links = (
            await db.execute(
                select(InspirationTag).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).all()
        assert links == []

        # 任务进度与结果
        assert task.total == 2
        assert task.done == 2
        assert task.progress == 100
        assert task.result["success_count"] == 2
        assert task.result["failed_count"] == 0


async def test_compare_batch_and_apply_to_material(client, upload):
    """批量对比接口返回共有/差异标签；应用到素材覆盖 AI 标签、保留手动标签。"""
    insp_id = upload().json()["id"]

    async with async_session() as db:
        # 两个标签（快照引用）
        db.add(Tag(name="标签A", category="style", source="ai_generated"))
        db.add(Tag(name="标签B", category="style", source="ai_generated"))
        db.add(Tag(name="手动标签", category="attribute", source="manual"))
        await db.flush()
        tag_map = {
            r[0]: r[1]
            for r in (
                (await db.execute(select(Tag.name, Tag.id))).all()
            )
        }
        # 既有 AI 标签关联（应被应用动作覆盖）+ 手动标签关联（应保留）
        db.add(
            InspirationTag(
                inspiration_id=insp_id,
                tag_id=tag_map["标签B"],
                confidence=0.9,
                source="ai_generated",
            )
        )
        db.add(
            InspirationTag(
                inspiration_id=insp_id,
                tag_id=tag_map["手动标签"],
                confidence=1.0,
                source="manual",
            )
        )
        # 两条日志：记录1 = {标签A, 标签B}，记录2 = {标签B} → 共有 标签B，差异 标签A
        log_ids = []
        for tag_names in (["标签A", "标签B"], ["标签B"]):
            log = AIAnalysisLog(
                inspiration_id=insp_id,
                model_name="model-a",
                log_type="analysis",
                prompt_version="abcdef12",
                processing_time_ms=100,
            )
            db.add(log)
            await db.flush()
            log_ids.append(log.id)
            for name in tag_names:
                db.add(
                    AIAnalysisTag(
                        log_id=log.id, tag_id=tag_map[name], confidence=0.8
                    )
                )
        await db.commit()

    # 批量对比
    r = client.post("/api/ai/compare-batch", json={"log_ids": log_ids})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["inspiration_id"] == insp_id
    assert data["tag_diff"]["common"] == ["标签B"]
    diff_names = {d["name"] for d in data["tag_diff"]["differing"]}
    assert diff_names == {"标签A"}

    # 应用记录1（含 标签A + 标签B）：AI 标签被覆盖为 {标签A, 标签B}，手动标签保留
    r2 = client.post(f"/api/ai/history/{log_ids[0]}/apply")
    assert r2.status_code == 200, r2.text
    assert r2.json()["applied"] == 2

    async with async_session() as db:
        insp = await db.get(Inspiration, insp_id)
        links = (
            await db.execute(
                select(InspirationTag.tag_id).where(
                    InspirationTag.inspiration_id == insp_id
                )
            )
        ).scalars().all()
        names = {
            r[0]
            for r in (
                (await db.execute(select(Tag.name).where(Tag.id.in_(links)))).all()
            )
        }
        assert names == {"标签A", "标签B", "手动标签"}
