"""博主 CSV 导入（`services/person/csv_import.py`）直接单测。

HTTP 端点用例在 `tests/test_persons.py`（由并行工作流维护）；这里直接调服务函数，
补齐：编码/表头校验、表头容错、逐行错误、文件内去重、upsert、错误明细上限、
以及并发冲突（IntegrityError）的重查降级分支。冲突分支用最小假会话驱动，
不依赖真实并发。
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.models.person import Blogger
from app.services.person.csv_import import import_bloggers_csv


def _csv_upload(content: str | bytes, filename: str = "bloggers.csv") -> UploadFile:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return UploadFile(file=io.BytesIO(data), filename=filename)


async def _all_bloggers() -> list[Blogger]:
    async with async_session() as db:
        return list((await db.execute(select(Blogger).order_by(Blogger.id))).scalars().all())


async def _add_blogger(name: str, xhs_id: str, ip_location: str | None = None) -> int:
    async with async_session() as db:
        row = Blogger(
            name=name, platform="xiaohongshu", xhs_id=xhs_id, ip_location=ip_location
        )
        db.add(row)
        await db.commit()
        return row.id


# ── 输入校验 ──


async def test_non_utf8_rejected():
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await import_bloggers_csv(db, _csv_upload(b"nickname,xhs_id\n\xb2\xe2\xca\xd4,abc\n"))
    assert exc.value.status_code == 400
    assert "不是 UTF-8" in exc.value.detail


@pytest.mark.parametrize("content", [b"", b"\n\n"])
async def test_empty_csv_rejected(content: bytes):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await import_bloggers_csv(db, _csv_upload(content))
    assert exc.value.status_code == 400
    assert "缺少表头" in exc.value.detail


async def test_whitespace_only_header_rejected():
    """只有空白表头 → 按缺少必填列拒绝（同样是 400，不落库）。"""
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await import_bloggers_csv(db, _csv_upload("  \n\n"))
    assert exc.value.status_code == 400
    assert "缺少必填列" in exc.value.detail


@pytest.mark.parametrize(
    ("content", "missing"),
    [
        ("xhs_id,ip_location\nabc,浙江\n", "nickname"),
        ("nickname,ip_location\n博主A,浙江\n", "xhs_id"),
    ],
)
async def test_missing_required_column(content: str, missing: str):
    async with async_session() as db:
        with pytest.raises(HTTPException) as exc:
            await import_bloggers_csv(db, _csv_upload(content))
    assert exc.value.status_code == 400
    assert f"缺少必填列：{missing}" in exc.value.detail


# ── 表头容错与导入 ──


async def test_header_case_space_and_column_order_tolerated():
    """表头大小写/首尾空白容错、列顺序不限、ip_location 可缺省。"""
    content = " XHS_ID , NickName \n abc123 , 小红书博主 \n"
    async with async_session() as db:
        out = await import_bloggers_csv(db, _csv_upload(content))

    assert out == {"imported": 1, "updated": 0, "skipped": 0, "failed": 0, "errors": []}
    (row,) = await _all_bloggers()
    assert (row.name, row.xhs_id, row.ip_location) == ("小红书博主", "abc123", None)
    assert row.platform == "xiaohongshu"
    assert row.source == "manual"


async def test_upsert_updates_existing_by_xhs_id():
    await _add_blogger("旧昵称", "same001", ip_location="浙江")

    async with async_session() as db:
        out = await import_bloggers_csv(
            db, _csv_upload("nickname,xhs_id,ip_location\n新昵称,same001,上海\n")
        )

    assert out["imported"] == 0
    assert out["updated"] == 1
    rows = await _all_bloggers()
    assert len(rows) == 1
    assert (rows[0].name, rows[0].ip_location) == ("新昵称", "上海")


async def test_empty_ip_location_updates_to_none():
    """CSV 里 ip_location 为空视为「清空」（写 None），不是保留旧值。"""
    await _add_blogger("有属地", "ip001", ip_location="浙江")
    async with async_session() as db:
        out = await import_bloggers_csv(
            db, _csv_upload("nickname,xhs_id,ip_location\n有属地,ip001,\n")
        )
    assert out["updated"] == 1
    (row,) = await _all_bloggers()
    assert row.ip_location is None


async def test_invalid_rows_reported_with_row_numbers():
    content = (
        "nickname,xhs_id\n"          # 表头
        ",no-nickname\n"             # 第 1 行：昵称为空
        "有人名,\n"                  # 第 2 行：小红书号为空
        f"超长号,{'x' * 65}\n"       # 第 3 行：小红书号 > 64 字符
        "正常,ok001\n"               # 第 4 行：合法
    )
    async with async_session() as db:
        out = await import_bloggers_csv(db, _csv_upload(content))

    assert out["imported"] == 1
    assert out["failed"] == 3
    assert [(e["row"], e["reason"]) for e in out["errors"]] == [
        (1, "昵称为空"),
        (2, "小红书号为空"),
        (3, "小红书号超过 64 字符"),
    ]
    # 错误行不入库
    assert [r.xhs_id for r in await _all_bloggers()] == ["ok001"]


async def test_duplicate_xhs_id_in_file_keeps_last():
    content = (
        "nickname,xhs_id,ip_location\n"
        "先出现,dup001,浙江\n"
        "后出现,dup001,上海\n"
    )
    async with async_session() as db:
        out = await import_bloggers_csv(db, _csv_upload(content))

    assert out["imported"] == 1
    assert out["skipped"] == 1
    (row,) = await _all_bloggers()
    assert (row.name, row.ip_location) == ("后出现", "上海")


async def test_error_list_capped_at_limit():
    """错误明细最多 100 条，但 failed 计数如实统计全部失败行。"""
    rows = "".join(f",xhs{i}\n" for i in range(105))
    async with async_session() as db:
        out = await import_bloggers_csv(db, _csv_upload("nickname,xhs_id\n" + rows))

    assert out["failed"] == 105
    assert len(out["errors"]) == 100
    assert out["imported"] == 0


async def test_header_only_csv_commits_with_zero_counts():
    async with async_session() as db:
        out = await import_bloggers_csv(db, _csv_upload("nickname,xhs_id\n"))
    assert out == {"imported": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}
    assert await _all_bloggers() == []


# ── 并发冲突降级（假会话驱动 IntegrityError 分支）──


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeNested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _ConflictSession:
    """flush 必抛 IntegrityError 的假会话：第一次查已存在行为空（模拟并发插入前），
    重查返回 ``retry``（模拟另一个请求刚提交）。"""

    def __init__(self, retry=None):
        self.retry = retry
        self.execute_calls = 0
        self.added: list[Blogger] = []
        self.expunged: list[Blogger] = []
        self.committed = False
        self.nested_entered = False

    async def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _FakeResult([])  # 批量查已存在：并发者尚未提交，查不到
        return _FakeResult([self.retry] if self.retry is not None else [])

    def add(self, obj):
        self.added.append(obj)

    def expunge(self, obj):
        self.expunged.append(obj)

    async def flush(self):
        raise IntegrityError("INSERT INTO bloggers", {}, Exception("UNIQUE constraint"))

    def begin_nested(self):
        self.nested_entered = True
        return _FakeNested()

    async def commit(self):
        self.committed = True


async def test_integrity_conflict_retry_updates_existing():
    """并发下同一 xhs_id 已被其它请求插入：重查后按更新处理，并回退 imported 计数。"""
    from types import SimpleNamespace

    retry_row = SimpleNamespace(name="旧名", ip_location=None)
    session = _ConflictSession(retry=retry_row)

    out = await import_bloggers_csv(
        session, _csv_upload("nickname,xhs_id,ip_location\n新名,race001,上海\n")
    )

    assert session.nested_entered is True
    assert session.committed is True
    assert len(session.added) == 1 and len(session.expunged) == 1  # pending 对象被移除
    assert isinstance(session.expunged[0], Blogger)
    assert (retry_row.name, retry_row.ip_location) == ("新名", "上海")
    assert out == {"imported": 0, "updated": 1, "skipped": 0, "failed": 0, "errors": []}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "疑似统计 bug：IntegrityError 且重查不到行时，new_person 已先 imported += 1，"
        "失败分支未回退计数，导致 imported 与 failed 同时 +1（应只计 failed）"
    ),
)
async def test_integrity_conflict_without_retry_row_does_not_count_as_imported():
    """并发冲突且重查不到行：该行应只计入 failed，不应同时计入 imported。"""
    session = _ConflictSession(retry=None)
    out = await import_bloggers_csv(
        session, _csv_upload("nickname,xhs_id\n冲突,race002\n")
    )

    assert out["failed"] == 1  # 已复现：failed=1
    assert out["imported"] == 0  # 当前实现为 1（疑似 bug）


async def test_integrity_conflict_without_retry_row_reports_error():
    session = _ConflictSession(retry=None)
    out = await import_bloggers_csv(
        session, _csv_upload("nickname,xhs_id\n冲突,race002\n")
    )

    assert session.committed is True
    assert out["updated"] == 0
    assert out["failed"] == 1
    assert out["errors"] == [{"row": 1, "nickname": "冲突", "reason": "导入冲突"}]
