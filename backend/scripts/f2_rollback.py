"""批次回滚（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  按批次回滚（--rollback）
# ═══════════════════════════════════════════════════════════════


def latest_batch_file(batch_dir: Path) -> Path | None:
    """取批次目录里最新的一份清单（--rollback latest 用）。

    Args:
        batch_dir: 批次清单目录。

    Returns:
        最新的清单路径；目录不存在或无清单时返回 None。
    """
    if not batch_dir.exists():
        return None
    files = sorted(batch_dir.glob("*.json"))
    return files[-1] if files else None


def _table_exists(conn, name: str) -> bool:
    """表是否存在（回滚要在「迁移未跑全的库副本」上也能给出可读结果）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def load_batch_manifest(batch_file: Path | str | None) -> dict:
    """读取一份批次清单（:func:`apply_import` 落盘的 JSON）。

    清单是「本批导入了哪些素材」的唯一依据：f2 素材没有 scraper_task_id
    （该列外键指向 scraper_tasks 表），回溯、结果浏览与 ``--rollback`` 都靠它。

    Args:
        batch_file: 清单路径（可为 None / 不存在的路径）。

    Returns:
        {"imported": [...], "errors": [...], "batch_id": str, "created_at": str,
         "storage_root": str, "db": str}；文件缺失或损坏时返回空结构
        （调用方按「本批无结果」处理，不抛异常）。
    """
    empty = {
        "imported": [],
        "errors": [],
        "batch_id": "",
        "created_at": "",
        "storage_root": "",
        "db": "",
    }
    if not batch_file:
        return empty
    try:
        data = json.loads(Path(batch_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    imported = data.get("imported")
    errors = data.get("errors")
    return {
        "imported": imported if isinstance(imported, list) else [],
        "errors": errors if isinstance(errors, list) else [],
        "batch_id": str(data.get("batch_id") or ""),
        "created_at": str(data.get("created_at") or ""),
        "storage_root": str(data.get("storage_root") or ""),
        "db": str(data.get("db") or ""),
    }


def batch_storage_root(batch_file: Path) -> Path | None:
    """读取批次清单里记录的导入期存储根（回滚优先用它，而不是当前配置）。

    清单在导入时写入了 `storage_root`；若导入用了 `--storage-root` 临时目录，
    回滚时用当前配置去找文件会全部找不到（孤儿文件），只用当前根定位批次还可能
    选错批次。这里把它读出来供调用方做默认值。

    Args:
        batch_file: 批次清单路径。

    Returns:
        清单记录的存储根；缺失或无法解析时返回 None。
    """
    root = load_batch_manifest(batch_file)["storage_root"]
    return Path(root) if root else None


def plan_rollback(
    batch_file: Path,
    db_path: Path,
    force: bool = False,
) -> tuple[list[dict], list[dict]]:
    """按批次清单区分「可删除」与「需保留」的条目（不写任何数据）。

    保留判断（未被导入动作之外的改动污染才删）：素材若已经有标签关联、被收藏、
    有评分、或质量状态不再是 pending，说明用户已经用过它——默认不删，避免把
    人的后续工作一起抹掉；`force=True` 时才连这些一起删。

    查询按批进行（`IN (...)` 分片）：一次回滚可能涉及上万条，逐条 SELECT 会变成
    数万次查询。

    Args:
        batch_file: 导入时落盘的批次清单。
        db_path: 素材库路径。
        force: 是否连「已被改动」的素材一起删。

    Returns:
        (可删除列表, 需保留列表)，每项含 inspiration_id / file_path /
        thumbnail_path / 以及保留原因。
    """
    batch = json.loads(Path(batch_file).read_text(encoding="utf-8"))
    rows = batch.get("imported") or []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    deletable: list[dict] = []
    kept: list[dict] = []
    try:
        # 一次性取回素材状态与标签计数（分片以避开 SQLite 变量数上限）
        ids = [r.get("inspiration_id") for r in rows if r.get("inspiration_id")]
        states: dict[str, tuple] = {}
        tag_counts: dict[str, int] = {}
        has_tag_table = _table_exists(conn, "inspiration_tags")
        for chunk in (ids[i : i + 900] for i in range(0, len(ids), 900)):
            marks = ",".join("?" * len(chunk))
            for insp_id, is_favorite, rating, quality_status in conn.execute(
                "SELECT id, is_favorite, rating, quality_status FROM inspirations "
                f"WHERE deleted_at IS NULL AND id IN ({marks})",
                chunk,
            ):
                states[insp_id] = (is_favorite, rating, quality_status)
            if has_tag_table:
                for insp_id, count in conn.execute(
                    "SELECT inspiration_id, COUNT(*) FROM inspiration_tags "
                    f"WHERE inspiration_id IN ({marks}) GROUP BY inspiration_id",
                    chunk,
                ):
                    tag_counts[insp_id] = count

        for row in rows:
            insp_id = row.get("inspiration_id")
            if not insp_id:
                continue
            if insp_id not in states:
                kept.append({**row, "keep_reason": "素材已不存在（删除过或已回滚）"})
                continue
            tag_links = tag_counts.get(insp_id, 0)
            is_favorite, rating, quality_status = states[insp_id]
            if not force and (
                tag_links or is_favorite or rating or quality_status not in (None, "pending")
            ):
                kept.append(
                    {
                        **row,
                        "keep_reason": (
                            f"已被改动（标签 {tag_links} / 收藏 {is_favorite} / "
                            f"评分 {rating} / 质量 {quality_status}）"
                        ),
                    }
                )
                continue
            deletable.append(row)
    finally:
        conn.close()
    return deletable, kept


def apply_rollback(
    deletable: list[dict],
    db_path: Path,
    storage_root: Path | None = None,
) -> dict:
    """实际删除（物理删除行 + 文件 + 缩略图 + 关键帧目录 + 关联表），逐条提交。

    为什么是物理删除而不是进垃圾桶：本操作语义是「撤销一次错误导入」，
    而垃圾桶素材会作为负样本参与质量学习——把误导入的素材当成「质量差」
    负样本会污染训练数据。

    单条失败只记录并继续（例如文件已被外部删掉）。

    Args:
        deletable: :func:`plan_rollback` 的「可删除」列表。
        db_path: 素材库路径。
        storage_root: 存储根目录（缺省 settings.storage_root）。

    Returns:
        {"deleted", "failed", "removed_files", "errors"}。
    """
    storage_root = storage_root or settings.storage_root
    conn = sqlite3.connect(str(db_path))
    deleted = 0
    removed_files = 0
    errors: list[dict] = []
    has_tag_table = _table_exists(conn, "inspiration_tags")
    has_blogger_table = _table_exists(conn, "inspiration_bloggers")
    for row in deletable:
        insp_id = row.get("inspiration_id")
        try:
            # 顺序与项目既有约定一致（inspiration_trash 的物理删除）：先删库并提交，
            # 再清理磁盘文件——反过来若 DB 删除失败，文件已不可逆丢失而记录还在，
            # 素材会变成指向缺失文件的悬空条目
            if has_tag_table:
                conn.execute(
                    "DELETE FROM inspiration_tags WHERE inspiration_id = ?", (insp_id,)
                )
            if has_blogger_table:
                conn.execute(
                    "DELETE FROM inspiration_bloggers WHERE inspiration_id = ?", (insp_id,)
                )
            conn.execute("DELETE FROM inspirations WHERE id = ?", (insp_id,))
            conn.commit()
            deleted += 1

            for rel in (row.get("file_path"), row.get("thumbnail_path")):
                if not rel:
                    continue
                target = storage_root / rel
                try:
                    if target.exists():
                        target.unlink()
                        removed_files += 1
                except Exception:
                    pass
            # 视频关键帧目录（详情页懒提取可能已生成）
            frames_dir = storage_root / "keyframes" / str(insp_id)
            if frames_dir.exists():
                shutil.rmtree(frames_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断整批
            try:
                conn.rollback()
            except Exception:
                pass
            errors.append({"inspiration_id": insp_id, "error": str(exc)[:200]})
    conn.close()
    return {
        "deleted": deleted,
        "failed": len(errors),
        "removed_files": removed_files,
        "errors": errors,
    }
