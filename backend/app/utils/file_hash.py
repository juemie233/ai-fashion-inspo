"""文件哈希工具：MD5 计算与哈希分组，用于重复检测与智能去重。

被 admin 路由（重复预览）与任务队列（去重执行）共用，
避免在两处重复实现 MD5 扫描逻辑。
"""

import hashlib
from pathlib import Path


def _digest(path: Path, factory: callable) -> str | None:
    """分块读取文件并计算指定算法的摘要；文件不可读时返回 None。"""
    try:
        h = factory()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def file_hash(path: Path) -> str | None:
    """计算文件的 MD5 哈希，用于后台全库重复检测。"""
    return _digest(path, hashlib.md5)


def file_sha256(path: Path) -> str | None:
    """计算文件的 SHA-256 哈希，用于上传前/入库时内容去重。

    与 file_hash（MD5）并存：MD5 用于后台全库去重，SHA-256 用于上传去重，
    后者与前端 Web Crypto 的原生 SHA-256 保持一致，避免引入额外依赖。
    """
    return _digest(path, hashlib.sha256)


def build_hash_map(
    db_records: list[tuple],
    storage_root: Path,
    include_meta: bool = False,
) -> dict[str, list[dict]]:
    """根据数据库记录构建「文件哈希 → 文件列表」映射。

    参数:
        db_records: 查询结果行，首列为素材 ID，第二列为 file_path
            （include_meta=True 时后续依次为 thumbnail_path、is_favorite、created_at）
        storage_root: 存储根目录
        include_meta: 是否附带缩略图路径、收藏、创建时间等元数据

    返回:
        {hash: [{id, file_path, size_bytes, ...}]}
    """
    hash_map: dict[str, list[dict]] = {}
    for row in db_records:
        rid = row[0]
        fpath = row[1]
        if not fpath:
            continue
        full = storage_root / fpath
        if not full.exists():
            continue
        fhash = file_hash(full)
        if fhash is None:
            continue
        entry: dict = {
            "id": rid,
            "file_path": fpath,
            "size_bytes": full.stat().st_size,
        }
        if include_meta and len(row) >= 5:
            entry["thumbnail_path"] = row[2]
            entry["is_favorite"] = row[3]
            entry["created_at"] = row[4]
        hash_map.setdefault(fhash, []).append(entry)
    return hash_map
