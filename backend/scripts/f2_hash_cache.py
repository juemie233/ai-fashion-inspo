"""SHA-256 哈希缓存（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import hashlib
import sqlite3
import sys
import time
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """流式计算文件 SHA-256（分块读取，避免整文件驻留内存）。

    Args:
        path: 文件路径。
        chunk: 分块大小（字节）。

    Returns:
        十六进制哈希字符串。
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            hasher.update(block)
    return hasher.hexdigest()


HASH_CACHE_DBNAME = "f2_hash_cache.db"


def default_hash_cache_path() -> Path:
    """哈希缓存的缺省落盘位置（与素材/缩略图同根，随项目迁移）。"""
    return settings.storage_root / HASH_CACHE_DBNAME


class HashCache:
    """文件 SHA-256 的落盘缓存：``路径 → (size, mtime_ns) → 摘要``。

    为什么需要：判重要对整个下载目录算 SHA-256（实测 15,350 文件 / 7.28 GB
    约 81 秒），而这份成本**每次运行都要付一遍**——进程内的 dict 缓存不跨运行，
    开了「每日自动获取」就变成每天固定开销，且随下载量线性增长。

    判据是「路径 + 文件大小 + mtime_ns」三元组：文件被改动（重新下载 / 覆盖）
    必然改变其中一项，缓存自动失效并重算，不会给出过期摘要。

    不做的两件事（有意为之）：
      - 不清理「已从下载目录消失」的历史行：留着只占几 MB，且路径若被重新创建，
        size/mtime 同时撞上旧值的概率可忽略；为它引入标记清扫得不偿失。
      - 不实现完整 Mapping 协议：调用方只需要 get/set 语义，见 :func:`_digest`。

    线程约定：内部持有 sqlite 连接，**必须在创建它的线程内使用**（任务执行器
    因此在线程内创建，见 :func:`build_plan_with_cache`）。
    """

    SCHEMA_VERSION = 1
    TABLE = "file_hashes"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else default_hash_cache_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # WAL + busy_timeout：CLI 与后端 worker 可能同时开着这个缓存文件
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()
        self._rows: dict[str, tuple[int, int, str]] = {
            row[0]: (row[1], row[2], row[3])
            for row in self._conn.execute(
                f"SELECT path, size, mtime_ns, digest FROM {self.TABLE}"
            )
        }
        self._cached_rows = len(self._rows)
        self._pending: dict[str, tuple[int, int, str]] = {}
        self._hit = self._miss = self._computed = 0
        self._hash_seconds = 0.0
        self._error = ""

    # ── 内部 ──

    def _ensure_schema(self) -> None:
        """建表；`PRAGMA user_version` 不含预期版本时先丢表重建（缓存可随时重算）。"""
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version != self.SCHEMA_VERSION:
            self._conn.execute(f"DROP TABLE IF EXISTS {self.TABLE}")
            self._conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        self._conn.execute(
            f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
            "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
            "digest TEXT NOT NULL)"
        )
        self._conn.commit()

    def _probe(self, path: Path) -> tuple[str, tuple[int, int, str]]:
        """返回 (key, 该文件的 size/mtime/digest)；未命中抛 KeyError。"""
        key = str(path)
        try:
            stat = path.stat()
        except OSError as exc:  # 文件已被删除/不可读 → 视为未命中，让上层重算并报错
            raise KeyError(key) from exc
        row = self._pending.get(key) or self._rows.get(key)
        if row and row[0] == stat.st_size and row[1] == stat.st_mtime_ns:
            return key, row
        raise KeyError(key)

    # ── 对外 ──

    def get(self, path: Path, default: str | None = None) -> str | None:
        """dict 风格的取值：命中返回摘要，未命中返回 default。"""
        try:
            return self._probe(path)[1][2]
        except KeyError:
            return default

    def __contains__(self, path: Path) -> bool:
        try:
            self._probe(path)
            return True
        except KeyError:
            return False

    def __setitem__(self, path: Path, digest: str) -> None:
        stat = path.stat()
        self._pending[str(path)] = (stat.st_size, stat.st_mtime_ns, digest)

    def digest(self, path: Path) -> str:
        """取哈希：命中缓存直接返回；未命中才算（并累计哈希耗时，供报表说明成本）。"""
        try:
            key, row = self._probe(path)
            self._hit += 1
            return row[2]
        except KeyError:
            self._miss += 1
        started = time.monotonic()
        value = sha256_file(path)
        self._hash_seconds += time.monotonic() - started
        self._computed += 1
        self[path] = value
        return value

    def flush(self) -> int:
        """把本次算出的哈希批量落盘（单事务）。

        写缓存失败**绝不能**影响导入：异常只记进 :meth:`stats` 的 ``error``
        字段，由调用方决定是否提示（缓存最坏情况是下次重算）。
        """
        if not self._pending or self._conn is None:
            return 0
        rows = [(key, *value) for key, value in self._pending.items()]
        try:
            with self._conn:
                self._conn.executemany(
                    f"INSERT INTO {self.TABLE} (path, size, mtime_ns, digest) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET "
                    "size=excluded.size, mtime_ns=excluded.mtime_ns, digest=excluded.digest",
                    rows,
                )
        except sqlite3.Error as exc:
            self._error = str(exc)
        else:
            self._rows.update(self._pending)
        count = len(self._pending)
        self._pending.clear()
        return count

    def close(self) -> None:
        """落盘并关闭连接（幂等）。"""
        if self._conn is not None:
            self.flush()
            self._conn.close()
            self._conn = None

    def stats(self) -> dict:
        """本次运行的缓存命中情况（供报表 / 任务结果展示哈希成本）。"""
        return {
            "db": str(self.db_path),
            "cached_rows": self._cached_rows,
            "hit": self._hit,
            "miss": self._miss,
            "computed": self._computed,
            "hash_seconds": round(self._hash_seconds, 2),
            "error": self._error,
        }

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def _finish_cache(cache: HashCache | None) -> dict:
    """落盘并关闭哈希缓存，返回统计（无缓存时返回空字典）。"""
    if cache is None:
        return {}
    cache.flush()
    stats = cache.stats()
    cache.close()
    return stats


def open_hash_cache(db_path: Path | None = None) -> tuple[HashCache | None, str]:
    """打开哈希缓存，返回 ``(缓存, 原因)``。

    缓存是纯优化：文件损坏 / 不可写时返回 ``(None, 原因)`` 让调用方退化为全量
    重算，**绝不能因此让导入失败**。
    """
    try:
        return HashCache(db_path), ""
    except (sqlite3.Error, OSError) as exc:
        return None, f"哈希缓存不可用（本次全量重算哈希）：{exc}"


def _digest(path: Path, cache: HashCache | dict[Path, str] | None) -> str:
    """统一的取哈希入口：HashCache 走落盘缓存，dict 走进程内缓存，None 直接算。"""
    if cache is None:
        return sha256_file(path)
    if isinstance(cache, HashCache):
        return cache.digest(path)
    hit = cache.get(path)
    if hit:
        return hit
    value = sha256_file(path)
    cache[path] = value
    return value
