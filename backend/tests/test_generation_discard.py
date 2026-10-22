"""生成历史的批次「丢弃/删除」（`DELETE /api/generate/runs/{run_id}`）。

覆盖三件事：
1. 正常丢弃：删掉批次候选目录里的图片，并把状态置为 `cancelled`；
2. 运行中的批次**拒绝**丢弃（runner 还在往目录里写，删了会撞 FileNotFoundError）；
3. 不存在的批次返回 404。

⚠️ 现状说明（不是测试写错）：`discard_run` 只**标记**为 `cancelled`，**不删 `generation_runs` 行**，
所以「生成历史」里仍会看到一条 cancelled 记录。而 `models/generation.py` 的模块注释描述的是
「用户清空生成历史时，已入库的图仍保留（`inspirations.generation_run_id` 为 ON DELETE SET NULL）」
—— 那更像是「删行」语义。两者不一致，这里用 `test_discard_keeps_the_row_as_cancelled`
把当前行为显式锁住；若之后改成删行，这条测试会失败并提醒同步更新前端文案。
"""

import asyncio
import pathlib

from app.config import settings
from app.database import async_session
from app.models.generation import GenerationRun


def _seed_run(batch_dir: str, status: str = "done", total: int = 2) -> int:
    """直接建一条批次记录（不走生成流程），返回 run_id。"""

    async def _do() -> int:
        async with async_session() as db:
            run = GenerationRun(
                status=status,
                prompt="测试用 prompt",
                batch_dir=batch_dir,
                total=total,
                done=total,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)
            return run.id

    return asyncio.run(_do())


def _read_run(run_id: int) -> tuple[str, int]:
    """读回 (status, id)；行不存在时抛 LookupError（供断言「是否被删除」）。"""

    async def _do() -> tuple[str, int]:
        async with async_session() as db:
            run = await db.get(GenerationRun, run_id)
            if run is None:
                raise LookupError(run_id)
            return run.status, run.id

    return asyncio.run(_do())


def _make_candidates(batch_dir: str, count: int = 2) -> pathlib.Path:
    """在 storage_root 下造出批次目录与候选图。"""
    folder = settings.storage_root / batch_dir
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        (folder / f"{i:03d}_seed-{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return folder


def test_discard_removes_candidates_and_marks_cancelled(client):
    """正常丢弃：候选目录被删、返回清理数量、状态变 cancelled。"""
    run_id = _seed_run("generated/901")
    folder = _make_candidates("generated/901", count=3)
    assert folder.exists()

    r = client.delete(f"/api/generate/runs/{run_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_files"] == 3, body
    assert body["status"] == "cancelled", body
    assert not folder.exists(), "批次目录应被删除"

    status, still_id = _read_run(run_id)
    assert still_id == run_id
    assert status == "cancelled"


def test_discard_refuses_running_run(client):
    """运行中的批次必须拒绝：否则 runner 正在写的目录被删会变成未知异常。"""
    run_id = _seed_run("generated/902", status="running")
    folder = _make_candidates("generated/902", count=1)

    r = client.delete(f"/api/generate/runs/{run_id}")
    assert r.status_code == 400, r.text
    assert "取消" in r.json()["detail"], r.json()
    assert folder.exists(), "被拒绝时不能动目录"

    status, _ = _read_run(run_id)
    assert status == "running", "被拒绝时状态不应改变"


def test_discard_unknown_run_returns_404(client):
    r = client.delete("/api/generate/runs/999999")
    assert r.status_code == 404, r.text


def test_discard_keeps_the_row_as_cancelled(client):
    """现状：丢弃只标记不删行（生成历史里仍留一条 cancelled 记录）。

    这条测试是**刻意**锁住当前行为的：模型注释描述的却是「清空历史」语义（删行 + SET NULL）。
    两者不一致，若要改成真删除，请同时改这条测试与前端「删除」文案。
    """
    run_id = _seed_run("generated/903")
    _make_candidates("generated/903", count=1)

    assert client.delete(f"/api/generate/runs/{run_id}").status_code == 200
    status, still_id = _read_run(run_id)
    assert still_id == run_id, "当前实现不删行"
    assert status == "cancelled"
