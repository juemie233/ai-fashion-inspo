"""LanceDB 向量存储封装：文本向量表与图像向量表。

- 文本向量表：inspiration_id + 384 维文本向量（Ollama all-minilm）
- 图像向量表：inspiration_id + 512 维图像向量（CLIP ViT-B/32）

LanceDB 为嵌入式向量数据库，数据落盘到 ``backend/storage/lancedb/``，
无需独立服务，目录文件可随项目迁移。所有文件 I/O 放入线程池执行，
避免阻塞事件循环。

依赖：``pip install lancedb``（轻量，不含 torch）。
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 连接缓存（模块级单例，懒加载）
_db = None


def _utc_str() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def is_lancedb_available() -> bool:
    """检测 lancedb 是否已安装。"""
    try:
        import lancedb  # noqa: F401
        return True
    except ImportError:
        return False


def _connect():
    """连接 LanceDB 数据目录（懒加载并缓存连接）。"""
    global _db
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
    """同步执行 upsert（先删除旧行再插入），保证一个素材只有一条向量。"""
    try:
        table = _table(kind)
        try:
            table.delete(f"inspiration_id = '{inspiration_id}'")
        except Exception:
            pass  # 无匹配行时删除可能抛错，忽略即可
        table.add([{
            "id": uuid.uuid4().hex,
            "inspiration_id": inspiration_id,
            "vector": [float(x) for x in vector],
            "created_at": _utc_str(),
        }])
        return True
    except Exception as e:
        logger.error(f"写入向量失败 (kind={kind}, id={inspiration_id}): {e}")
        return False


async def upsert_vector(kind: str, inspiration_id: str, vector: list[float]) -> bool:
    """写入（或覆盖）一条素材的向量。kind 为 text/image。"""
    return await asyncio.to_thread(_upsert_sync, kind, inspiration_id, vector)


def _search_sync(
    kind: str, query_vector: list[float], top_k: int
) -> list[dict[str, Any]]:
    """同步执行余弦相似度 TopK 检索。"""
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


def _delete_inspiration_sync(inspiration_id: str) -> None:
    """同步删除素材在两张表中的向量（素材删除时调用）。"""
    for kind in ("text", "image"):
        try:
            table = _table(kind)
            table.delete(f"inspiration_id = '{inspiration_id}'")
        except Exception as e:
            logger.warning(
                f"删除素材向量失败 (kind={kind}, id={inspiration_id}): {e}"
            )


async def delete_inspiration_vectors(inspiration_id: str) -> None:
    """删除素材对应的文本与图像向量（素材物理删除时调用）。"""
    await asyncio.to_thread(_delete_inspiration_sync, inspiration_id)


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
