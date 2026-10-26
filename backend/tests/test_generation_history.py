"""生成历史列表的两个显示回归：时间带时区、描述不截断。

用户反馈「生成历史时间显示有问题、描述文字显示不全」，根因都在列表接口：

1. ``created_at`` 原来用裸 ``datetime.isoformat()``，DB 存的是 naive UTC，
   前端 ``new Date()`` 会当本地时间解析 —— UTC+8 下整批记录早 8 小时；
2. ``prompt`` 被截到 120 字，展开行也看不到后半段。

这里锁住接口契约（带 Z + 全文 + 参数快照），前端展示由
``GenerateHistoryTable.test.ts`` 覆盖。
"""

import asyncio

from app.database import async_session
from app.models.generation import GenerationRun

LONG_PROMPT = (
    "法式复古碎花雪纺连衣裙，浅蓝色底配白色小碎花，收腰显瘦，适合春季通勤与周末约会，"
    "搭配米色针织开衫与白色帆布鞋，自然光街拍，柔和背景虚化，突出面料垂坠感。" * 2
)


def _seed_run() -> int:
    """直接建一条批次记录（不走生成流程），返回 run_id。"""

    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                kind="t2i",
                status="done",
                prompt=LONG_PROMPT,
                negative="模糊，低质量",
                params_json=(
                    '{"width": 1024, "height": 768, "steps": 20, "cfg": 4.5,'
                    ' "seed_base": 123, "count": 2,'
                    ' "prompt": "params 里的重复副本", "negative": "params 里的重复副本"}'
                ),
                model_key="z_image_int8",
                workflow_name="z_image_t2i",
                total=2,
                done=2,
                failed=0,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return asyncio.run(_do())


def _delete_run(run_id: int) -> None:
    async def _do() -> None:
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            if run is not None:
                await db.delete(run)
                await db.commit()

    asyncio.run(_do())


def test_recent_runs_keeps_full_prompt_and_utc_iso(client):
    run_id = _seed_run()
    try:
        r = client.get("/api/generate/runs", params={"limit": 100})
        assert r.status_code == 200, r.text
        row = next(i for i in r.json()["items"] if i["id"] == run_id)

        # 时间必须带 Z：裸 isoformat() 会被前端当作本地时间
        assert row["created_at"].endswith("Z")
        assert row["updated_at"].endswith("Z")

        # 描述给全文（不截 120 字），负面词与参数快照一起返回给展开行
        assert row["prompt"] == LONG_PROMPT
        assert len(row["prompt"]) > 120
        assert row["negative"] == "模糊，低质量"
        assert row["workflow_name"] == "z_image_t2i"
        assert row["params"]["width"] == 1024
        assert row["params"]["height"] == 768
        assert row["params"]["steps"] == 20
        assert row["params"]["seed_base"] == 123
        # params 里原本存了一份 prompt/negative 副本，接口不应重复返回
        assert "prompt" not in row["params"]
        assert "negative" not in row["params"]
    finally:
        _delete_run(run_id)
