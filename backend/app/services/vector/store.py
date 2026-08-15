"""LanceDB 向量存储模块（原 app.services.vector_store，已迁入 vector 子包）。

文本向量表与图像向量表：

- 文本向量表：inspiration_id + 384 维文本向量（Ollama all-minilm）
- 图像向量表：inspiration_id + 512 维图像向量（CLIP ViT-B/32）

LanceDB 为嵌入式向量数据库，数据落盘到 ``backend/storage/lancedb/``，
无需独立服务，目录文件可随项目迁移。所有文件 I/O 放入线程池执行，
避免阻塞事件循环。

依赖：``pip install lancedb``（轻量，不含 torch）。
"""

import asyncio
import logging
import math
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 连接缓存（模块级单例，懒加载）；加锁防止线程池并发首屏重复连接
_db = None
_db_lock = threading.Lock()


def _utc_str() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _sql_quote(value: str) -> str:
    """对 SQL 字符串字面量做单引号转义（防止注入）。"""
    return value.replace("'", "''")


def is_lancedb_available() -> bool:
    """检测 lancedb 是否已安装。"""
    try:
        import lancedb  # noqa: F401
        return True
    except ImportError:
        return False


def _connect():
    """连接 LanceDB 数据目录（懒加载并缓存连接，线程安全）。

    加锁 + 双重检查：首次从多个线程池线程并发触发时只初始化一次。
    """
    global _db
    if _db is not None:
        return _db
    with _db_lock:
        # 双重检查，避免线程池并发首屏重复连接
        if _db is not None:
            return _db
        import lancedb

        settings.lancedb_dir.mkdir(parents=True, exist_ok=True)
        _db = lancedb.connect(str(settings.lancedb_dir))
        return _db


def _table_name(kind: str) -> str:
    """返回指定类型（text/image）对应的表名。"""
    return (
        settings.lancedb_text_table if kind == "text" else settings.lancedb_image_table
    )


def _dim(kind: str) -> int:
    """返回指定类型的向量维度。"""
    return (
        settings.lancedb_text_dim if kind == "text" else settings.lancedb_image_dim
    )


def _table(kind: str):
    """打开表，不存在时按 schema 创建。

    LanceDB 表结构（两张表共用）：
        id             string    行主键（uuid）
        inspiration_id string    素材 UUID（业务唯一键）
        vector         float32[]  向量（定长，维度随表类型而定）
        created_at     string    写入时间（ISO）
    """
    import pyarrow as pa

    db = _connect()
    name = _table_name(kind)
    if name in db.table_names():
        return db.open_table(name)

    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("inspiration_id", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), _dim(kind))),
        pa.field("created_at", pa.string()),
    ])
    logger.info(f"创建 LanceDB 表: {name} (维度 {_dim(kind)})")
    return db.create_table(name, schema=schema)


def _upsert_sync(kind: str, inspiration_id: str, vector: list[float]) -> bool:
    """同步执行 upsert（先删除旧行再插入），保证一个素材只有一条向量。

    写入前校验向量维度与有限性（NaN/Inf），不合法时记 warning 并返回 False，
    避免 embedding 模型切换后向固定维度 schema 写入非法向量导致静默失效。
    """
    try:
        expected_dim = _dim(kind)
        vec = [float(x) for x in vector]
        if len(vec) != expected_dim:
            logger.warning(
                f"写入向量维度不匹配 (kind={kind}, id={inspiration_id}): "
                f"期望 {expected_dim}，实际 {len(vec)}，跳过写入"
            )
            return False
        if not all(math.isfinite(x) for x in vec):
            logger.warning(
                f"写入向量包含非法值（NaN/Inf）(kind={kind}, id={inspiration_id})，跳过写入"
            )
            return False

        table = _table(kind)
        try:
            table.delete(f"inspiration_id = '{_sql_quote(inspiration_id)}'")
        except Exception:
            pass  # 无匹配行时删除可能抛错，忽略即可
        table.add([{
            "id": uuid.uuid4().hex,
            "inspiration_id": inspiration_id,
            "vector": vec,
            "created_at": _utc_str(),
        }])
        return True
    except Exception as e:
        logger.error(f"写入向量失败 (kind={kind}, id={inspiration_id}): {e}")
        return False


async def upsert_vector(kind: str, inspiration_id: str, vector: list[float]) -> bool:
    """写入（或覆盖）一条素材的向量。kind 为 text/image。"""
    return await asyncio.to_thread(_upsert_sync, kind, inspiration_id, vector)


def _delete_ids_sync(kind: str, inspiration_ids: list[str], chunk_size: int = 500) -> None:
    """分批删除指定素材的向量（分块避免 IN 子句过长）。

    与单条 ``_upsert_sync`` 的「先删后插」语义一致，但按块合并删除，
    避免逐条 delete 造成与逐条 add 同等的 manifest 版本膨胀。
    """
    if not inspiration_ids:
        return
    try:
        table = _table(kind)
        for i in range(0, len(inspiration_ids), chunk_size):
            chunk = inspiration_ids[i:i + chunk_size]
            clause = "inspiration_id IN (" + ", ".join(
                f"'{_sql_quote(x)}'" for x in chunk
            ) + ")"
            table.delete(clause)
    except Exception as e:
        logger.warning(
            f"批量删除向量失败 (kind={kind}, count={len(inspiration_ids)}): {e}"
        )


def _batch_add_sync(kind: str, items: list[tuple[str, list[float]]]) -> int:
    """同步批量写入多条向量（先删同批旧向量再单次 add，实现真 upsert）。

    单条写入时 LanceDB 每次 add 都会生成一个新 manifest，其大小随片段数增长，
    逐条写入 N 条会落盘约 O(N²) 字节（实测 1657 条图像向量膨胀到 524MB）。
    批量一次性插入全部行，只产生极少数 fragment 与 manifest，是回填场景的正确写法。

    返回:
        实际写入的行数（跳过维度不匹配或含 NaN/Inf 的非法向量）。
    """
    if not items:
        return 0
    expected_dim = _dim(kind)
    rows = []
    for inspiration_id, vector in items:
        vec = [float(x) for x in vector]
        if len(vec) != expected_dim:
            logger.warning(
                f"批量写入向量维度不匹配 (kind={kind}, id={inspiration_id}): "
                f"期望 {expected_dim}，实际 {len(vec)}，跳过"
            )
            continue
        if not all(math.isfinite(x) for x in vec):
            logger.warning(
                f"批量写入向量含非法值（NaN/Inf）(kind={kind}, id={inspiration_id})，跳过"
            )
            continue
        rows.append({
            "id": uuid.uuid4().hex,
            "inspiration_id": inspiration_id,
            "vector": vec,
            "created_at": _utc_str(),
        })
    if not rows:
        return 0
    try:
        table = _table(kind)
        # 真 upsert：先删除同批素材的旧向量，再批量插入，保证一个素材只有一条
        # 向量（与单条 _upsert_sync 语义一致）。分块删除，避免 IN 子句过长。
        _delete_ids_sync(kind, [row["inspiration_id"] for row in rows])
        table.add(rows)
        return len(rows)
    except Exception as e:
        logger.error(f"批量写入向量失败 (kind={kind}, count={len(rows)}): {e}")
        return 0


async def batch_upsert_vectors(
    kind: str, items: list[tuple[str, list[float]]]
) -> int:
    """批量写入（或新增）多条向量。kind 为 text/image，返回实际写入行数。"""
    return await asyncio.to_thread(_batch_add_sync, kind, items)


def _search_sync(
    kind: str, query_vector: list[float], top_k: int
) -> list[dict[str, Any]]:
    """同步执行余弦相似度 TopK 检索。

    任何异常（如 LanceDB 未安装、表缺失）都记日志并返回空列表，
    避免语义搜索接口直接 500。
    """
    try:
        table = _table(kind)
        rows = table.search(query_vector).metric("cosine").limit(top_k).to_list()
        results: list[dict[str, Any]] = []
        for r in rows:
            dist = float(r.get("_distance", 1.0))
            # LanceDB cosine 距离 = 1 - 余弦相似度
            score = max(0.0, min(1.0, 1.0 - dist))
            results.append({
                "inspiration_id": str(r["inspiration_id"]),
                "score": round(score, 4),
            })
        return results
    except Exception as e:
        logger.error(f"向量检索失败 (kind={kind}): {e}")
        return []


async def search_vectors(
    kind: str, query_vector: list[float], top_k: int
) -> list[dict[str, Any]]:
    """按余弦相似度搜索，返回 [{inspiration_id, score}] 列表（降序）。"""
    return await asyncio.to_thread(_search_sync, kind, query_vector, top_k)


def _get_vector_sync(kind: str, inspiration_id: str) -> list[float] | None:
    """同步读取指定素材的向量；不存在时返回 None。"""
    try:
        import pyarrow.compute as pc

        table = _table(kind)
        arrow_table = table.to_arrow()
        filtered = arrow_table.filter(
            pc.equal(arrow_table["inspiration_id"], inspiration_id)
        )
        if filtered.num_rows == 0:
            return None
        vec = filtered["vector"][0].as_py()
        return [float(x) for x in vec]
    except Exception as e:
        logger.error(f"读取向量失败 (kind={kind}, id={inspiration_id}): {e}")
        return None


async def get_vector(kind: str, inspiration_id: str) -> list[float] | None:
    """读取指定素材的向量；不存在时返回 None。"""
    return await asyncio.to_thread(_get_vector_sync, kind, inspiration_id)


def _count_sync(kind: str) -> int:
    """同步统计表中向量条数。"""
    try:
        return _table(kind).count_rows()
    except Exception as e:
        logger.error(f"统计向量数量失败 (kind={kind}): {e}")
        return 0


async def count_vectors(kind: str) -> int:
    """统计指定表（text/image）的向量条数。"""
    return await asyncio.to_thread(_count_sync, kind)


def _list_vector_ids_sync(kind: str) -> set[str]:
    """同步读取表中全部素材 ID 集合（供回填增量判断，一次表扫描）。"""
    try:
        table = _table(kind)
        arrow_table = table.to_arrow()
        return {str(x) for x in arrow_table["inspiration_id"].to_pylist()}
    except Exception as e:
        logger.error(f"读取向量表素材 ID 失败 (kind={kind}): {e}")
        return set()


async def list_vector_ids(kind: str) -> set[str]:
    """返回指定表中已存在向量的全部素材 ID 集合。"""
    return await asyncio.to_thread(_list_vector_ids_sync, kind)


def _delete_inspiration_batch_sync(inspiration_ids: list[str]) -> None:
    """同步批量删除多个素材在两张表中的向量（素材批量删除时调用）。"""
    if not inspiration_ids:
        return
    for kind in ("text", "image"):
        try:
            table = _table(kind)
            # 对每个 id 做单引号转义后拼成 IN 子句，防止注入
            clause = "inspiration_id IN (" + ", ".join(
                f"'{_sql_quote(i)}'" for i in inspiration_ids
            ) + ")"
            table.delete(clause)
        except Exception as e:
            logger.warning(
                f"批量删除素材向量失败 (kind={kind}, count={len(inspiration_ids)}): {e}"
            )


async def delete_inspiration_vectors_batch(inspiration_ids: list[str]) -> None:
    """批量删除多个素材对应的文本与图像向量（素材批量删除时调用）。

    LanceDB 未安装时静默返回（直接 return），不影响主流程。
    """
    if not is_lancedb_available():
        return
    if not inspiration_ids:
        return
    await asyncio.to_thread(_delete_inspiration_batch_sync, inspiration_ids)


async def delete_inspiration_vectors(inspiration_id: str) -> None:
    """删除素材对应的文本与图像向量（素材物理删除时调用）。"""
    await delete_inspiration_vectors_batch([inspiration_id])


def get_status() -> dict:
    """返回 LanceDB 能力状态（供 /api/search/vector/status 使用）。"""
    if not is_lancedb_available():
        return {
            "available": False,
            "dir": str(settings.lancedb_dir),
            "reason": "lancedb 未安装，请执行：pip install lancedb",
        }
    return {
        "available": True,
        "dir": str(settings.lancedb_dir),
        "tables": {
            settings.lancedb_text_table: settings.lancedb_text_dim,
            settings.lancedb_image_table: settings.lancedb_image_dim,
        },
    }
