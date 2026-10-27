"""人脸扫描路由（routers/face_scan）的单测。

**必须直接调用 handler**：TestClient 的请求跑在 anyio portal 线程里，pytest-cov
追踪不到，用 HTTP 写用例会让整个路由显示 0% 覆盖。

这组接口是「扫描 → 审核 → 聚合指派」的写入口，语义上的关键点：

1. **确认即锁定**：confirm 只处理 pending 或「未匹配」的人脸，已确认/已有匹配/
   被驳回的一律跳过并计数（不报错）——重复点确认不能把别的匹配覆盖掉。
2. **驳回要持久化**：reject 除了清空匹配，还要置 `match_excluded`，否则下次全库
   匹配会把这个「人工判定不匹配」的脸重新捞回候选区。
3. **撤回是空操作**：已确认记录不提供解锁，undo 全部计入 skipped（保留接口兼容）。
4. **聚合组是动态视图**：组内人脸确认完后该组要消失；`group_id` 必须是**原始下标**
   （而非过滤后序号），否则前面一组消失后，展开后面会取到已空的组。
5. **子服务探测始终 200**：可用性本身是查询结果，不是请求错误；未配置/不可达
   要通过 `enabled`/`reachable` 表达，前端据此禁用按钮。
"""

import pytest
from fastapi import HTTPException

from app.config import settings
from app.database import async_session
from app.models.face import InspirationFaceDetection
from app.models.inspiration import Inspiration
from app.models.person import Blogger, Model
from app.models.task import TaskQueue
from app.routers import face_scan as rt
from app.schemas.face_scan import FaceClusterRunIn, FaceConfirmIn, FaceScanStartIn


async def _insp(insp_id: str) -> str:
    async with async_session() as db:
        db.add(
            Inspiration(
                id=insp_id,
                source_type="douyin",
                file_path=f"images/x/{insp_id}.jpg",
                media_type="image",
            )
        )
        await db.commit()
    return insp_id


async def _person(name: str, *, model: bool = False) -> int:
    async with async_session() as db:
        p = (
            Model(name=name, platform="other", source="manual")
            if model
            else Blogger(name=name, platform="douyin", source="manual")
        )
        db.add(p)
        await db.commit()
        return p.id


async def _det(
    insp_id: str,
    *,
    status: str | None = "pending",
    blogger_id: int | None = None,
    model_id: int | None = None,
    excluded: bool = False,
    score: float = 0.9,
    face_index: int = 0,
) -> int:
    async with async_session() as db:
        d = InspirationFaceDetection(
            inspiration_id=insp_id,
            face_index=face_index,
            embedding=b"\x00" * 8,
            det_score=score,
            match_status=status,
            matched_blogger_id=blogger_id,
            matched_model_id=model_id,
            match_excluded=excluded,
        )
        db.add(d)
        await db.commit()
        return d.id


# ── 创建任务 ──


async def test_start_scan_returns_task_id():
    class _Task:
        id = 11
        total = 100

    async def _create(db, scope, auto_match):
        assert scope == "semi"
        return _Task()

    import app.services.blogger_face as bf

    rt_create = getattr(rt, "create_face_scan_task", _create)
    original = rt_create
    try:
        rt.create_face_scan_task = _create  # type: ignore[assignment]
        async with async_session() as db:
            out = await rt.start_scan(FaceScanStartIn(scope="semi", auto_match=True), db)
    finally:
        rt.create_face_scan_task = original  # type: ignore[assignment]

    assert out == {"task_id": 11, "total": 100}


async def test_run_match_maps_person_type_to_scope(monkeypatch):
    created: list[dict] = []

    class _Task:
        id = 7

    async def _create(db, *, scope, person_ids, threshold):
        created.append({"scope": scope, "person_ids": person_ids, "threshold": threshold})
        return _Task()

    monkeypatch.setattr(rt, "create_face_match_task", _create, raising=False)

    from app.schemas.face_scan import FaceMatchRunIn

    async with async_session() as db:
        await rt.run_match(FaceMatchRunIn(person_type="blogger", scope="all"), db)
        await rt.run_match(FaceMatchRunIn(person_type="model", scope="all"), db)
        await rt.run_match(FaceMatchRunIn(person_type="blogger", scope="all", person_id=5), db)

    assert created[0]["scope"] == "bloggers"  # 博主 + all → bloggers
    assert created[1]["scope"] == "models"
    assert created[2]["person_ids"] == [5]  # 指定 person_id 时只匹配该人物


# ── 子服务状态 ──


async def test_face_service_status_disabled_when_not_configured(monkeypatch):
    class _Client:
        enabled = False

    monkeypatch.setattr(rt, "face_client", _Client(), raising=False)
    monkeypatch.setattr(
        "app.services.face_client.face_client", _Client(), raising=False
    )

    data = await rt.face_service_status()

    assert data["enabled"] is False
    assert data["reachable"] is False
    assert "未配置" in data["message"]


async def test_face_service_status_reports_unreachable(monkeypatch):
    from app.services.face_client import FaceServiceUnavailableError

    class _Client:
        enabled = True

        async def health(self, timeout=0):
            raise FaceServiceUnavailableError("connection refused")

    monkeypatch.setattr("app.services.face_client.face_client", _Client(), raising=False)

    data = await rt.face_service_status()

    assert data["reachable"] is False
    assert "未启动或不可达" in data["message"]


async def test_face_service_status_ok_returns_detail(monkeypatch):
    class _Client:
        enabled = True

        async def health(self, timeout=0):
            return {"model": "buffalo_l", "registered": 42}

    monkeypatch.setattr("app.services.face_client.face_client", _Client(), raising=False)
    monkeypatch.setattr(settings, "face_service_url", "http://127.0.0.1:18889")

    data = await rt.face_service_status()

    assert data["enabled"] is True and data["reachable"] is True
    assert data["detail"]["registered"] == 42
    assert data["url"] == "http://127.0.0.1:18889"


# ── 任务状态 ──


async def test_scan_task_empty_returns_nulls():
    async with async_session() as db:
        assert await rt.scan_task(db) == {"scan_task": None, "match_task": None}


async def test_scan_task_returns_latest_per_type():
    async with async_session() as db:
        db.add(TaskQueue(type="face_scan", status="success", total=10, done=10, progress=100))
        db.add(TaskQueue(type="face_match", status="running", total=5, done=1, progress=20))
        await db.commit()

    async with async_session() as db:
        data = await rt.scan_task(db)

    # TaskOut 是 Pydantic 对象（不是 dict）
    assert data["scan_task"].status == "success"
    assert data["match_task"].status == "running"


# ── 结果查询 ──


async def _scan_results(db, **kwargs):
    """直接调用 handler：Query 默认值不会被 FastAPI 注入，必须逐个显式传。"""
    params = dict(
        status="pending",
        person_type=None,
        person_id=None,
        unmatched=False,
        page=1,
        size=50,
        db=db,
    )
    params.update(kwargs)
    return await rt.scan_results(**params)


async def test_scan_results_unmatched_mode_ignores_person_filters():
    await _insp("r1")
    await _det("r1", status="pending")  # 未匹配：matched_* 都为空

    async with async_session() as db:
        data = await _scan_results(db, unmatched=True)

    assert data["mode"] == "unmatched"


async def test_scan_results_person_aggregate_mode():
    await _insp("r2")
    bid = await _person("聚合博主")
    await _det("r2", status="pending", blogger_id=bid)

    async with async_session() as db:
        data = await _scan_results(db)

    assert data["mode"] == "persons"
    assert data["total"] == 1
    assert data["items"][0]["person_type"] == "blogger"


async def test_scan_results_detail_mode_infers_person_type():
    await _insp("r3")
    mid = await _person("模特甲", model=True)
    await _det("r3", status="pending", model_id=mid)

    async with async_session() as db:
        data = await _scan_results(db, person_id=mid)

    assert data["mode"] == "detail"


async def test_person_type_of_404_for_unknown_person():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await rt._person_type_of(db, 999999)
    assert exc.value.status_code == 404


# ── 审核确认 ──


async def _confirm(action: str, items: list[dict]) -> dict:
    async with async_session() as db:
        return await rt.confirm(FaceConfirmIn(action=action, items=items), db)


async def test_confirm_links_person_and_locks_detection():
    await _insp("c1")
    bid = await _person("确认博主")
    did = await _det("c1", status="pending", blogger_id=bid)

    out = await _confirm("confirm", [{"detection_id": did, "person_type": "blogger", "person_id": bid}])

    assert out["confirmed"] == 1
    async with async_session() as db:
        det = await db.get(InspirationFaceDetection, did)
        assert det.match_status == "confirmed"
        assert det.matched_blogger_id == bid
    # 关联表也写入了（素材 → 博主）
    from app.models.person import InspirationBlogger
    from sqlalchemy import select

    async with async_session() as db:
        links = (
            await db.execute(
                select(InspirationBlogger).where(InspirationBlogger.inspiration_id == "c1")
            )
        ).scalars().all()
    assert [link.blogger_id for link in links] == [bid]


async def test_confirm_skips_already_confirmed_detection():
    """已确认的候选不再重复处理（幂等跳过，且不覆盖成别人）。"""
    await _insp("c2")
    b1 = await _person("原主")
    b2 = await _person("新主")
    did = await _det("c2", status="confirmed", blogger_id=b1)

    out = await _confirm("confirm", [{"detection_id": did, "person_type": "blogger", "person_id": b2}])

    assert out["confirmed"] == 0
    assert out["skipped"] == 1
    async with async_session() as db:
        det = await db.get(InspirationFaceDetection, did)
        assert det.matched_blogger_id == b1  # 没被覆盖


async def test_confirm_skips_excluded_and_invalid_items():
    await _insp("c3")
    bid = await _person("排除博主")
    excluded = await _det("c3", status="pending", excluded=True)

    out = await _confirm("confirm", [{"detection_id": excluded, "person_type": "blogger", "person_id": bid}])
    assert out["skipped"] == 1

    # 人物类型非法 / person_id 缺失
    await _insp("c4")
    did = await _det("c4", status="pending")
    out2 = await _confirm("confirm", [{"detection_id": did, "person_type": "unknown", "person_id": None}])
    assert out2["skipped"] == 1


async def test_confirm_allows_unmatched_face_in_cluster_group():
    """聚合组的「整组指派」复用本接口：matched_* 为空的人脸也可确认。"""
    await _insp("c5")
    bid = await _person("指派博主")
    did = await _det("c5", status=None)  # 未匹配（无 match_status）

    out = await _confirm("confirm", [{"detection_id": did, "person_type": "blogger", "person_id": bid}])

    assert out["confirmed"] == 1


async def test_reject_clears_match_and_persists_exclusion():
    """驳回必须持久化 match_excluded：否则下次全库匹配又把这张脸捞回候选区。"""
    await _insp("c6")
    bid = await _person("被驳回")
    did = await _det("c6", status="pending", blogger_id=bid)

    out = await _confirm("reject", [{"detection_id": did}])

    assert out["rejected"] == 1
    async with async_session() as db:
        det = await db.get(InspirationFaceDetection, did)
        assert det.matched_blogger_id is None
        assert det.match_status is None
        assert det.match_excluded is True


async def test_reject_only_touches_pending():
    await _insp("c7")
    bid = await _person("已确认人家")
    did = await _det("c7", status="confirmed", blogger_id=bid)

    out = await _confirm("reject", [{"detection_id": did}])

    assert out["rejected"] == 0 and out["skipped"] == 1


async def test_undo_is_noop_by_design():
    """已确认即锁定：不提供解锁，undo 全部跳过（保留接口兼容）。"""
    await _insp("c8")
    bid = await _person("锁定人家")
    did = await _det("c8", status="confirmed", blogger_id=bid)

    out = await _confirm("undo", [{"detection_id": did}])

    assert out["undone"] == 0 and out["skipped"] == 1


# ── 聚类 ──


async def _cluster_task(groups: list[dict], *, status: str = "success", summary=None) -> None:
    async with async_session() as db:
        db.add(
            TaskQueue(
                type="face_cluster",
                status=status,
                total=1,
                done=1,
                progress=100,
                result={"groups": groups, **(summary or {})},
            )
        )
        await db.commit()


async def test_cluster_run_creates_task(monkeypatch):
    class _Task:
        id = 33

    async def _create(db, *, threshold, min_group_size):
        assert threshold == 0.5
        assert min_group_size == 3
        return _Task()

    monkeypatch.setattr(rt, "create_face_cluster_task", _create, raising=False)

    async with async_session() as db:
        out = await rt.cluster_run(FaceClusterRunIn(threshold=0.5, min_group_size=3), db)

    assert out["task_id"] == 33


async def test_cluster_task_empty():
    async with async_session() as db:
        assert await rt.cluster_task(db) == {"cluster_task": None}


async def test_cluster_groups_empty_without_task():
    async with async_session() as db:
        data = await rt.cluster_groups(page=1, size=20, db=db)

    assert data["items"] == [] and data["total"] == 0 and data["task_status"] is None


async def test_cluster_groups_keeps_original_index_and_drops_confirmed():
    """已确认完的组要消失，但剩余组的 group_id 必须保持原始下标。

    否则前面一组确认完消失后，后面组的编号前移，展开时会取到已空的组。
    """
    await _insp("g1")
    await _insp("g2")
    bid = await _person("已确认")
    d_confirmed = await _det("g1", status="confirmed", blogger_id=bid, face_index=0)
    d_open = await _det("g2", status=None, face_index=0, score=0.8)

    await _cluster_task([
        {"detection_ids": [d_confirmed]},  # 第 0 组已确认完 → 应消失
        {"detection_ids": [d_open]},  # 第 1 组仍有未确认人脸
    ])

    async with async_session() as db:
        data = await rt.cluster_groups(page=1, size=20, db=db)

    assert data["total"] == 1
    assert data["items"][0]["group_id"] == 1  # 原始下标，不是 0
    assert data["items"][0]["size"] == 1
    assert data["items"][0]["rep_detection_id"] == d_open


async def test_cluster_groups_skips_empty_and_excluded():
    await _insp("g3")
    bid = await _person("排除人脸")
    excluded = await _det("g3", status=None, excluded=True, blogger_id=bid)

    await _cluster_task([
        {"detection_ids": []},  # 空组
        {"detection_ids": [excluded]},  # 全是被驳回的脸 → 不占位
    ])

    async with async_session() as db:
        data = await rt.cluster_groups(page=1, size=20, db=db)

    assert data["total"] == 0


async def test_cluster_groups_pagination_and_summary():
    ids = []
    for i in range(3):
        await _insp(f"p{i}")
        ids.append(await _det(f"p{i}", status=None, face_index=0))

    await _cluster_task(
        [{"detection_ids": [i]} for i in ids],
        summary={"total_faces": 3, "group_count": 3, "method": "ann"},
    )

    async with async_session() as db:
        page1 = await rt.cluster_groups(page=1, size=2, db=db)
        page2 = await rt.cluster_groups(page=2, size=2, db=db)

    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 1
    assert page1["summary"]["total_faces"] == 3
    assert page1["summary"]["method"] == "ann"


async def test_cluster_group_detections_404_without_task():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await rt.cluster_group_detections(0, db=db)
    assert exc.value.status_code == 404


async def test_cluster_group_detections_404_for_bad_index():
    await _cluster_task([{"detection_ids": [1]}])
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await rt.cluster_group_detections(99, db=db)
    assert exc.value.status_code == 404


async def test_cluster_group_detections_returns_page():
    await _insp("gd1")
    did = await _det("gd1", status=None)
    await _cluster_task([{"detection_ids": [did]}])

    async with async_session() as db:
        data = await rt.cluster_group_detections(0, page=1, size=10, db=db)

    assert data["group_id"] == 0
    assert data["total"] == 1
