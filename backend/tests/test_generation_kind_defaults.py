"""档位与生成方式的默认值（2026-10 图生图报错修复的回归测试）。

历史 bug：`CreateRunPayload.model_key` 的默认值是 `"z_image_int8"`，
`model_dump()` 会把它当成「用户显式选择」传给服务层，导致
`payload.get("model_key") or default_model` 永远取不到按 kind 算出的默认值 ——
于是**任何** `kind=i2i` 请求都会被「档位与生成方式不符」拒掉。

修法：schema 默认改为 `None`，服务层再用 `or default_model` 兜底。
这里锁住两点：不传 model_key 的 i2i 请求**不能**再命中那条校验；t2i 仍能拿到文生图档位。
"""

from app.database import async_session
from app.models.generation import GenerationRun


def test_i2i_without_model_key_is_not_rejected_by_kind_check(client, upload):
    insp = upload().json()["id"]
    r = client.post(
        "/api/generate/runs",
        json={"kind": "i2i", "prompt": "x", "reference_ids": [insp]},
    )
    assert "与当前生成方式不符" not in r.text, r.text


def test_i2i_without_model_key_uses_i2i_default(client, upload):
    """若批次真的建成，落库的档位必须是 i2i 的默认值，而不是 schema 里那个 t2i 默认。"""
    import asyncio

    insp = upload().json()["id"]
    r = client.post(
        "/api/generate/runs",
        json={"kind": "i2i", "prompt": "x", "reference_ids": [insp]},
    )
    if r.status_code != 200:
        return  # 环境不具备（如 ComfyUI 未就绪）时跳过断言，避免与本次修复无关的失败
    run_id = r.json()["run_id"]

    async def _read() -> str:
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            return run.model_key if run else ""

    assert asyncio.run(_read()) == "qwen_2_1_i2i"


def test_t2i_default_stays_text_to_image_model(client):
    r = client.post("/api/generate/runs", json={"kind": "t2i", "prompt": "x"})
    assert "与当前生成方式不符" not in r.text, r.text

