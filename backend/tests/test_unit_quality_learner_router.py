"""负样本初筛器路由补充测试（直接调用路由函数，不经过 HTTP）。

覆盖 ``app/routers/quality_learner.py`` 的三个端点：
status 在 lancedb 可用/不可用两种姿态下的 dataset 字段、train 透传、
reset 透传。依赖的服务函数全部打桩，不加载模型、不训练。
"""

from app.database import async_session
from app.routers import quality_learner as router
from app.services import quality_learner
from app.services.vector import store as vector_store


async def test_status_without_lancedb_reports_install_hint(monkeypatch):
    """lancedb 不可用：dataset 给安装提示，不调用 collect_samples。"""
    calls: list[str] = []

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: False)
    monkeypatch.setattr(
        quality_learner, "get_status", lambda: {"trained": False, "threshold": 0.5}
    )

    async def _should_not_run(_db):
        calls.append("collect")
        return [[], [], {}]

    monkeypatch.setattr(quality_learner, "collect_samples", _should_not_run)

    async with async_session() as db:
        body = await router.learner_status(db=db)

    assert calls == []
    assert body["trained"] is False
    assert "lancedb 未安装" in body["dataset"]["error"]


async def test_status_with_lancedb_collects_dataset(monkeypatch):
    """lancedb 可用：附带正负样本统计，不覆盖已有 status 字段。"""
    stats = {"positive": 3, "negative": 7, "excluded_privacy": 1}

    monkeypatch.setattr(vector_store, "is_lancedb_available", lambda: True)
    monkeypatch.setattr(quality_learner, "get_status", lambda: {"trained": True})

    async def _fake_collect(_db):
        return [[], [], stats]

    monkeypatch.setattr(quality_learner, "collect_samples", _fake_collect)

    async with async_session() as db:
        body = await router.learner_status(db=db)

    assert body["trained"] is True
    assert body["dataset"] == stats


async def test_train_passes_db_to_service(monkeypatch):
    """train 端点把会话原样交给服务层，并返回其指标。"""
    seen: dict = {}

    async def _fake_train(db):
        seen["db"] = db
        return {"trained": 1, "accuracy": 0.9}

    monkeypatch.setattr(quality_learner, "train", _fake_train)

    async with async_session() as db:
        trained = await router.learner_train(db=db)

    assert trained == {"trained": 1, "accuracy": 0.9}
    assert seen["db"] is not None


async def test_reset_passes_through(monkeypatch):
    """reset 端点直接返回服务层的删除结果。"""
    monkeypatch.setattr(quality_learner, "reset", lambda: {"removed": 2})

    assert await router.learner_reset() == {"removed": 2}
