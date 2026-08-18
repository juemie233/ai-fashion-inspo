"""人脸注册数据存储：SQLite（标准库 sqlite3，特征向量以 float32 BLOB 保存）。

独立小项目自带存储，不依赖主后端数据库，避免跨环境耦合。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

DB_PATH = Path(__file__).resolve().parent.parent / "face_service.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS face_persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL
);
"""

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等）。"""
    with _lock:
        conn = _connect()
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()


def list_persons() -> list[dict[str, Any]]:
    """返回全部已注册人脸（不含特征向量）。"""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT id, name, created_at FROM face_persons ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_person(person_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT id, name, created_at FROM face_persons WHERE id = ?", (person_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def upsert_person(person_id: str, name: str, embedding: np.ndarray) -> None:
    """注册/更新人脸：特征向量以 float32 bytes 存 BLOB。"""
    blob = np.asarray(embedding, dtype=np.float32).tobytes()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO face_persons (id, name, embedding, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name, embedding = excluded.embedding
                """,
                (person_id, name, blob, now),
            )
            conn.commit()
        finally:
            conn.close()


def delete_person(person_id: str) -> bool:
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM face_persons WHERE id = ?", (person_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def all_embeddings() -> list[dict[str, Any]]:
    """返回全部注册（含特征向量），供匹配时全量余弦计算。"""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute("SELECT id, name, embedding FROM face_persons").fetchall()
            result = []
            for r in rows:
                emb = np.frombuffer(r["embedding"], dtype=np.float32)
                result.append({"id": r["id"], "name": r["name"], "embedding": emb})
            return result
        finally:
            conn.close()
