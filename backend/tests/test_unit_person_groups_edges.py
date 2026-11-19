"""人物组（`services/person/person_groups.py`）未覆盖分支补测。

既有 `tests/test_person_groups.py` 只走 HTTP 端点的主路径；这里直接调服务层，
补齐：领域异常、二选一参数校验、并入已有组、组内自动主账号（素材数优先/同数取
小 id）、解绑后的组清理与组名刷新、手动主账号覆盖、审计留痕等分支。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database import async_session
from app.models.audit import AuditLog
from app.models.person import Blogger, InspirationBlogger, PersonGroup
from app.services.person.base import PersonConflictError, PersonNotFoundError
from app.services.person.person_groups import (
    _auto_primary_blogger_id,
    _effective_primary,
    _get_blogger,
    _get_group,
    get_group_info,
    link_bloggers,
    set_primary_blogger,
    unlink_blogger,
)


async def _add_bloggers(*names: str, platform: str = "xiaohongshu") -> list[int]:
    """直接建博主行（绕开路由，聚焦服务层行为），返回按创建顺序的 id。"""
    async with async_session() as db:
        rows = [Blogger(name=n, platform=platform) for n in names]
        db.add_all(rows)
        await db.commit()
        return [r.id for r in rows]


async def _add_inspiration_links(blogger_id: int, inspiration_ids: list[str]) -> None:
    async with async_session() as db:
        for iid in inspiration_ids:
            db.add(InspirationBlogger(inspiration_id=iid, blogger_id=blogger_id))
        await db.commit()


async def _audit_actions() -> list[AuditLog]:
    async with async_session() as db:
        return list((await db.execute(select(AuditLog))).scalars().all())


async def test_missing_group_and_blogger_raise_domain_errors():
    """_get_group / _get_blogger 不存在时抛领域异常（路由统一转 404）。"""
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError) as group_exc:
            await _get_group(db, 987654)
        assert group_exc.value.message == "人物组不存在"

        with pytest.raises(PersonNotFoundError) as blogger_exc:
            await _get_blogger(db, 987654)
        assert blogger_exc.value.message == "博主未找到"


async def test_auto_primary_conflict_when_group_has_no_member():
    """组内没有任何博主时无法确定主账号 → PersonConflictError。"""
    async with async_session() as db:
        group = PersonGroup(name="空组")
        db.add(group)
        await db.commit()
        await db.refresh(group)
        with pytest.raises(PersonConflictError) as exc:
            await _auto_primary_blogger_id(db, group)
        assert "没有博主" in exc.value.message


async def test_link_requires_exactly_one_target():
    """target_blogger_id 与 group_id 必须且只能提供一个。"""
    (blogger_id,) = await _add_bloggers("参数博主")
    (target_id,) = await _add_bloggers("参数目标")
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as both_missing:
            await link_bloggers(db, blogger_id)
        assert "必须且只能" in both_missing.value.message

        with pytest.raises(PersonConflictError) as both_given:
            await link_bloggers(
                db, blogger_id, target_blogger_id=target_id, group_id=1
            )
        assert "必须且只能" in both_given.value.message


async def test_link_self_rejected():
    (blogger_id,) = await _add_bloggers("自环博主")
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await link_bloggers(db, blogger_id, target_blogger_id=blogger_id)
        assert exc.value.message == "不能把博主绑定到自身"


async def test_link_missing_blogger_or_target_raises_not_found():
    (blogger_id,) = await _add_bloggers("存在博主")
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError):
            await link_bloggers(db, 999999, target_blogger_id=blogger_id)
        with pytest.raises(PersonNotFoundError):
            await link_bloggers(db, blogger_id, target_blogger_id=999999)


async def test_link_creates_group_and_writes_audit():
    """新建组：主账号按 id 较小者（素材数同为 0），组名取主账号名，写审计。"""
    a, b = await _add_bloggers("建组甲", "建组乙")

    async with async_session() as db:
        out = await link_bloggers(db, a, target_blogger_id=b)

    assert out["group_id"] > 0
    assert set(out["member_ids"]) == {a, b}
    # 同为 0 个素材，取 id 较小者（先建）
    assert out["primary_blogger_id"] == a
    assert out["group_name"] == "建组甲"

    async with async_session() as db:
        group = await db.get(PersonGroup, out["group_id"])
        assert group is not None
        assert group.name == "建组甲"
        assert (await db.get(Blogger, b)).person_group_id == out["group_id"]

    logs = await _audit_actions()
    assert len(logs) == 1
    assert logs[0].action == "link_blogger_group"
    assert logs[0].count == 2
    assert "绑定到组" in logs[0].detail


async def test_link_into_existing_group_via_group_id():
    """group_id 分支：第三个账号并入已有组，返回全部成员。"""
    a, b, c = await _add_bloggers("并入甲", "并入乙", "并入丙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]

    async with async_session() as db:
        out = await link_bloggers(db, c, group_id=gid)

    assert out["group_id"] == gid
    assert set(out["member_ids"]) == {a, b, c}
    async with async_session() as db:
        assert (await db.get(Blogger, c)).person_group_id == gid


async def test_link_group_id_rejects_member_already_in_group():
    a, b = await _add_bloggers("已在组甲", "已在组乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await link_bloggers(db, a, group_id=gid)
        assert exc.value.message == "该博主已在目标组内"


async def test_link_target_already_grouped_joins_target_group():
    """target 已在某组：不是新建组，而是把 blogger 并入 target 所在组。"""
    a, b, c = await _add_bloggers("目标组甲", "目标组乙", "游离丙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
    async with async_session() as db:
        out = await link_bloggers(db, c, target_blogger_id=b)

    assert out["group_id"] == gid
    assert set(out["member_ids"]) == {a, b, c}


async def test_link_both_already_in_same_group_rejected():
    a, b = await _add_bloggers("同组甲", "同组乙")
    async with async_session() as db:
        await link_bloggers(db, a, target_blogger_id=b)
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await link_bloggers(db, a, target_blogger_id=b)
        assert exc.value.message == "两个博主已在同一组"


async def test_link_group_not_found_raises_not_found():
    (blogger_id,) = await _add_bloggers("组不存在博主")
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError) as exc:
            await link_bloggers(db, blogger_id, group_id=888888)
        assert exc.value.message == "人物组不存在"


async def test_auto_primary_prefers_more_inspirations(client, upload):
    """自动主账号：素材数最多者优先（同数才取 id 小者）。"""
    a, b = await _add_bloggers("素材少甲", "素材多乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]

    insp_1 = upload().json()["id"]
    insp_2 = upload().json()["id"]
    insp_3 = upload().json()["id"]
    await _add_inspiration_links(a, [insp_1])
    await _add_inspiration_links(b, [insp_2, insp_3])

    async with async_session() as db:
        group = await db.get(PersonGroup, gid)
        assert await _auto_primary_blogger_id(db, group) == b
        assert await _effective_primary(db, group) == b


async def test_effective_primary_manual_override_wins(client, upload):
    """手动指定主账号优先于「素材数最多」（即使被指定者素材更少）。"""
    a, b = await _add_bloggers("手动主甲", "素材多乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
        group = await db.get(PersonGroup, gid)
        group.primary_blogger_id = a
        await db.commit()

    insp = upload().json()["id"]
    await _add_inspiration_links(b, [insp])

    async with async_session() as db:
        group = await db.get(PersonGroup, gid)
        assert await _effective_primary(db, group) == a


async def test_unlink_keeps_group_and_clears_manual_primary():
    """3 人组解绑一人：组保留，手动主账号被解绑者清空并刷新组名。"""
    a, b, c = await _add_bloggers("解绑甲", "解绑乙", "解绑丙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
    async with async_session() as db:
        await link_bloggers(db, c, group_id=gid)
    async with async_session() as db:
        await set_primary_blogger(db, gid, a)  # 手动指定被解绑者为主账号

    async with async_session() as db:
        out = await unlink_blogger(db, a)

    assert out == {"blogger_id": a, "removed_group_id": None}
    async with async_session() as db:
        group = await db.get(PersonGroup, gid)
        assert group is not None
        assert group.primary_blogger_id is None  # 手动主账号已清空，恢复自动
        assert group.name == (await db.get(Blogger, b)).name  # 自动主账号（id 小者）
        assert (await db.get(Blogger, a)).person_group_id is None

    logs = await _audit_actions()
    unlink_log = next(log for log in logs if log.action == "unlink_blogger_group")
    assert unlink_log.count == 1
    assert "移出组" in unlink_log.detail
    assert "组已删除" not in unlink_log.detail


async def test_unlink_last_but_one_removes_group():
    """2 人组解绑一人：组内只剩 1 个账号 → 组自动删除。"""
    a, b = await _add_bloggers("删组甲", "删组乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]

    async with async_session() as db:
        out = await unlink_blogger(db, b)

    assert out == {"blogger_id": b, "removed_group_id": gid}
    async with async_session() as db:
        assert await db.get(PersonGroup, gid) is None
        assert (await db.get(Blogger, a)).person_group_id is None

    logs = await _audit_actions()
    unlink_log = next(log for log in logs if log.action == "unlink_blogger_group")
    assert "组已删除" in unlink_log.detail


async def test_unlink_not_in_group_conflict():
    (blogger_id,) = await _add_bloggers("游离博主")
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await unlink_blogger(db, blogger_id)
        assert exc.value.message == "该博主不在任何人物组内"


async def test_unlink_missing_blogger_raises_not_found():
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError):
            await unlink_blogger(db, 765432)


async def test_set_primary_updates_group_and_audit():
    a, b = await _add_bloggers("切主甲", "切主乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]

    async with async_session() as db:
        out = await set_primary_blogger(db, gid, b)

    assert out == {"group_id": gid, "primary_blogger_id": b}
    async with async_session() as db:
        group = await db.get(PersonGroup, gid)
        assert group.primary_blogger_id == b
        assert group.name == "切主乙"
        info = await get_group_info(db, gid)
    assert info["primary_blogger_id"] == b

    logs = await _audit_actions()
    primary_log = next(log for log in logs if log.action == "set_primary_blogger")
    assert "主账号设为" in primary_log.detail


async def test_set_primary_rejects_outsider():
    a, b, outsider = await _add_bloggers("切主组甲", "切主组乙", "组外丙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
    async with async_session() as db:
        with pytest.raises(PersonConflictError) as exc:
            await set_primary_blogger(db, gid, outsider)
        assert exc.value.message == "该博主不在目标组内"


async def test_set_primary_missing_group_or_blogger():
    a, b = await _add_bloggers("缺组甲", "缺组乙")
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]
    async with async_session() as db:
        with pytest.raises(PersonNotFoundError):
            await set_primary_blogger(db, 999999, a)
        with pytest.raises(PersonNotFoundError):
            await set_primary_blogger(db, gid, 999999)


async def test_group_info_members_and_fields():
    """组信息：成员按 id 升序，字段齐全；独立组内账号不混入。"""
    a, b = await _add_bloggers("信息甲", "信息乙")
    outsider = (await _add_bloggers("信息组外"))[0]
    async with async_session() as db:
        gid = (await link_bloggers(db, a, target_blogger_id=b))["group_id"]

    async with async_session() as db:
        info = await get_group_info(db, gid)

    assert info["group_id"] == gid
    assert info["group_name"] == "信息甲"
    assert [m["id"] for m in info["members"]] == [a, b]
    assert outsider not in [m["id"] for m in info["members"]]
    for member in info["members"]:
        assert set(member) == {
            "id",
            "name",
            "platform",
            "platform_user_id",
            "xhs_id",
            "profile_url",
            "avatar_path",
        }
        assert member["platform"] == "xiaohongshu"
