"""向量存储层（LanceDB 封装）的测试。

**为什么用真实 LanceDB 而不是 mock**：这一层的价值几乎全在「与 LanceDB 的交互细节」——
懒加载连接、并发建表回退、损坏表（空骨架）自愈、批量写、按 ID 删除、维度不匹配。
mock 掉以后只剩「我调用了某个方法」，等于没测。lancedb 与 pyarrow 都是测试依赖，
所以直接在临时目录上跑真库（每个用例独立 `lancedb_dir`，跑完即删）。
"""

import asyncio

import pytest

from app.config import settings
from app.services.vector import store


@pytest.fixture(autouse=True)
def _isolated_lancedb(tmp_path, monkeypatch):
    """每个用例一个独立的向量库目录，并清掉连接缓存。"""
    monkeypatch.setattr(settings, "lancedb_dir", tmp_path / "lancedb", raising=False)
    monkeypatch.setattr(settings, "lancedb_text_dim", 4, raising=False)
    monkeypatch.setattr(settings, "lancedb_image_dim", 8, raising=False)
    store.reset_connection()
    yield
    store.reset_connection()


def _vec(dim: int, seed: int = 0) -> list[float]:
    v = [0.1 * ((seed + i) % 5 + 1) for i in range(dim)]
    return v


# ── 可用性 / 连接 / 表名 / 维度 ──


def test_is_lancedb_available_true_when_installed():
    assert store.is_lancedb_available() is True


def test_table_name_and_dim_follow_settings():
    assert store._table_name("text") == settings.lancedb_text_table
    assert store._table_name("image") == settings.lancedb_image_table
    assert store._dim("text") == 4
    assert store._dim("image") == 8


def test_connect_is_lazy_and_cached():
    """首次连接才建目录，之后复用同一对象（线程池并发首屏只连一次）。"""
    assert not settings.lancedb_dir.exists()

    first = store._connect()
    second = store._connect()

    assert first is second
    assert settings.lancedb_dir.exists()

    store.reset_connection()
    third = store._connect()
    assert third is not first  # reset 后重连


def test_sql_quote_escapes_single_quote():
    assert store._sql_quote("a'b") == "a''b"


# ── 写 / 读 / 删 ──


async def test_upsert_and_get_vector():
    assert await store.upsert_vector("text", "i1", _vec(4)) is True

    assert await store.get_vector("text", "i1") == pytest.approx(_vec(4))
    assert await store.get_vector("text", "不存在") is None


async def test_upsert_overwrites_same_id():
    await store.upsert_vector("text", "i1", _vec(4, seed=0))
    await store.upsert_vector("text", "i1", _vec(4, seed=3))

    assert await store.get_vector("text", "i1") == pytest.approx(_vec(4, seed=3))
    # 覆盖而不是新增：行数仍为 1
    assert await store.count_vectors("text") == 1


async def test_upsert_rejects_wrong_dimension():
    """维度不符要返回 False 而不是把错维向量写进库（之后检索会全挂）。"""
    assert await store.upsert_vector("text", "bad", _vec(7)) is False


async def test_batch_upsert_and_list_ids():
    items = [("a", _vec(4, 0)), ("b", _vec(4, 1))]
    written = await store.batch_upsert_vectors("text", items)

    assert written == 2
    assert await store.list_vector_ids("text") == {"a", "b"}
    assert await store.count_vectors("text") == 2


async def test_batch_upsert_skips_bad_items():
    """单条坏数据（维度错）不该让整批失败。"""
    written = await store.batch_upsert_vectors(
        "text", [("ok", _vec(4)), ("bad", _vec(2))]
    )
    assert written == 1
    assert await store.list_vector_ids("text") == {"ok"}


async def test_delete_single_and_batch():
    await store.batch_upsert_vectors("text", [("a", _vec(4)), ("b", _vec(4, 1))])

    await store.delete_inspiration_vectors("a")
    assert await store.list_vector_ids("text") == {"b"}

    # 删不存在的 ID 不报错（幂等）
    await store.delete_inspiration_vectors("不存在")
    assert await store.list_vector_ids("text") == {"b"}

    await store.delete_inspiration_vectors_batch(["b"])
    assert await store.list_vector_ids("text") == set()


async def test_delete_batch_empty_is_noop():
    await store.upsert_vector("text", "keep", _vec(4))
    await store.delete_inspiration_vectors_batch([])
    assert await store.list_vector_ids("text") == {"keep"}


async def test_search_vectors_returns_scores():
    await store.batch_upsert_vectors(
        "text", [("same", [1.0, 0.0, 0.0, 0.0]), ("other", [0.0, 1.0, 0.0, 0.0])]
    )

    hits = await store.search_vectors("text", [1.0, 0.0, 0.0, 0.0], top_k=2)

    assert hits[0]["inspiration_id"] == "same"
    assert 0.9 <= hits[0]["score"] <= 1.0
    assert {h["inspiration_id"] for h in hits} == {"same", "other"}


async def test_search_vectors_empty_table():
    assert await store.search_vectors("text", [1.0, 0.0, 0.0, 0.0], top_k=5) == []


async def test_count_and_ids_on_empty_store():
    assert await store.count_vectors("text") == 0
    assert await store.list_vector_ids("text") == set()


# ── 损坏表自愈 ──


async def test_corrupt_table_degrades_on_read_and_heals_on_write(caplog):
    """空骨架（表注册在、数据集读不了）读路径降级，写路径先备份后重建。

    历史事故：删除向量目录与写入并发，把数据集删成空骨架，之后所有向量操作报
    "Table exists but could not be loaded"，管理页显示向量几乎全丢。
    """
    await store.upsert_vector("text", "before", _vec(4))

    # 构造「表注册存在但数据集读不了」：删掉数据版本清单（_versions/*.manifest）
    # 注意 LanceDB 的目录名带 .lance 后缀
    table_dir = settings.lancedb_dir / f"{settings.lancedb_text_table}.lance"
    manifests = list((table_dir / "_versions").glob("*.manifest"))
    assert manifests, "LanceDB 应生成 _versions/*.manifest"
    for m in manifests:
        m.unlink()

    store.reset_connection()

    # 读路径：按「无数据」降级，不抛异常
    assert await store.get_vector("text", "before") is None

    # 写路径：重建表并写入成功
    assert await store.upsert_vector("text", "after", _vec(4)) is True
    assert await store.list_vector_ids("text") == {"after"}


async def test_backup_corrupt_table_creates_backup_dir():
    """损坏表重建前要先备份目录（人工可恢复），而不是直接删。"""
    await store.upsert_vector("text", "x", _vec(4))
    table_dir = settings.lancedb_dir / f"{settings.lancedb_text_table}.lance"
    assert table_dir.exists()

    store._backup_corrupt_table(settings.lancedb_text_table)

    # 备份目录命名：.corrupt-<表名>-<时间戳>
    backups = list(settings.lancedb_dir.glob(f".corrupt-{settings.lancedb_text_table}-*"))
    assert backups, "损坏表必须先备份再重建"


# ── 存储重置 ──


async def test_reset_lancedb_storage_removes_dir():
    await store.upsert_vector("text", "gone", _vec(4))
    assert settings.lancedb_dir.exists()

    await store.reset_lancedb_storage()

    assert not settings.lancedb_dir.exists()
    # 重置后仍可继续用（懒加载重建）
    assert await store.upsert_vector("text", "new", _vec(4)) is True
    assert await store.list_vector_ids("text") == {"new"}


async def test_upsert_is_serialised_by_write_lock():
    """并发写入必须串行（LanceDB 同表并发写会互相打断）。"""
    results = await asyncio.gather(
        *[store.upsert_vector("text", f"p{i}", _vec(4, i)) for i in range(5)]
    )
    assert all(results)
    assert await store.count_vectors("text") == 5


# ── 批量读取（训练负样本等场景：必须一次加载，不能 O(N²)）──


async def test_get_vectors_batch_loads_once():
    await store.batch_upsert_vectors(
        "text", [("a", _vec(4, 0)), ("b", _vec(4, 1)), ("c", _vec(4, 2))]
    )

    got = await store.get_vectors_batch("text", ["a", "c", "不存在"])

    assert set(got) == {"a", "c"}
    assert got["a"] == pytest.approx(_vec(4, 0))


async def test_get_vectors_batch_empty_input():
    assert await store.get_vectors_batch("text", []) == {}


async def test_get_vectors_batch_on_empty_table():
    assert await store.get_vectors_batch("text", ["x"]) == {}


# ── 压缩（把逐条写产生的小文件合并）──


async def test_compact_vectors_reports_both_tables():
    await store.upsert_vector("text", "t1", _vec(4))
    await store.upsert_vector("image", "i1", _vec(8))

    stats = await store.compact_vectors()

    assert set(stats) == {"text", "image"}
    # 数据仍在（压缩不能丢数据）
    assert await store.list_vector_ids("text") == {"t1"}
    assert await store.list_vector_ids("image") == {"i1"}


# ── 状态 / 文本公式版本 ──


def test_get_status_reports_tables_and_dims():
    """管理页「向量状态」的数据源：可用性、目录、两张表的维度。

    注意这里给出的是**配置维度**而不是实时条数——条数需要打开表（可能很慢），
    状态接口刻意保持轻量，条数由前端另调 /vector/count。
    """
    status = store.get_status()

    assert status["available"] is True
    assert status["dir"] == str(settings.lancedb_dir)
    tables = status["tables"]
    assert tables[settings.lancedb_text_table] == settings.lancedb_text_dim
    assert tables[settings.lancedb_image_table] == settings.lancedb_image_dim


def test_text_formula_version_roundtrip():
    """文本嵌入公式版本：用于判断已存向量是否过期（升级公式后需重建）。"""
    assert store.get_stored_text_formula_version() is None

    store.set_stored_text_formula_version(2)
    assert store.get_stored_text_formula_version() == 2

    store.set_stored_text_formula_version(3)
    assert store.get_stored_text_formula_version() == 3
