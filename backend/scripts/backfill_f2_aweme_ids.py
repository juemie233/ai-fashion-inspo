"""按对齐报告把历史 f2 素材回填真实作品 ID 与原帖链接。

只做两件事，**不改动任何其他字段、不删除任何行**：

1. ``source_platform_id``：``f2:{文件名哈希}#image1`` → ``f2:{真实aweme_id}#image1``
2. ``source_url``：留空 → ``https://www.douyin.com/video|note/{aweme_id}``

依赖
----
``backend/storage/f2_id_alignment_report.json``（由
``python -m scripts.report_f2_id_alignment`` 产出）。**只回填报告里「唯一对齐」的
作品**——多义与未命中一律不动（严格口径，宁可少填不可填错）。

对齐键与来源
------------
文件 → 真实作品 ID 的映射按「归一化作者 + 发布时间 + 描述」查报告，因此
**发布目录与点赞目录的文件都能覆盖**（两者只是文件名前缀不同，作者已归一化）。

安全设计
--------
- **默认只读**（dry-run），必须显式 ``--apply`` 才写库
- 只 ``UPDATE`` 现存行，**从不 INSERT / DELETE**；``WHERE`` 条件带上原平台 ID，
  确保只改预期的那一行
- ``ix_inspirations_source_platform_id`` 是部分唯一索引：同一作品被重复下载时
  两行会映射到同一个新 ID，此时**只补 source_url、不改 ID**（先到先得）
- 只处理未删除素材；垃圾桶素材保持现状（130 行，其链接无意义且不该扰动垃圾桶状态）
- 落一批清单（含每行的旧值/新值），可用 ``--rollback`` 原样回滚
- 幂等：重复运行第二次无事可做（旧 ID 已不存在）

用法
----
    cd backend
    python -m scripts.backfill_f2_aweme_ids                    # 预览（只读）
    python -m scripts.backfill_f2_aweme_ids --apply            # 落库
    python -m scripts.backfill_f2_aweme_ids --rollback storage/import_batches/<清单>.json
"""

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

from .import_f2_downloads import (  # noqa: E402
    DEFAULT_F2_DIR,
    DEFAULT_F2_LIKE_ROOT,
    DEFAULT_F2_ROOT,
    IMPORT_BATCH_DIRNAME,
    legacy_platform_id_for,
    library_db_path,
    platform_id_for,
    scan_directory,
    source_url_for,
    utcnow,
)

"""默认的对齐报告位置（storage 已 gitignore，属数据产物而非源码）。"""
DEFAULT_REPORT = Path("storage/f2_id_alignment_report.json")

"""每批提交的行数：分批 commit，便于中断后观察进度、也避免长事务占锁。"""
COMMIT_BATCH = 500


def load_alignment(report_path: Path) -> dict[str, dict[str, str]]:
    """读对齐报告 → ``{归一化作者: {文件名主干: 真实作品ID}}``（只取唯一对齐）。

    Args:
        report_path: 报告 JSON 路径。

    Returns:
        对齐映射。

    Raises:
        FileNotFoundError: 报告不存在（提示先跑 report_f2_id_alignment）。
    """
    if not report_path.is_file():
        raise FileNotFoundError(
            f"找不到对齐报告 {report_path}；"
            "请先运行 python -m scripts.report_f2_id_alignment --output <该路径>"
        )
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        author: {stem: aweme_id for stem, aweme_id in result["unique"]}
        for author, result in (data.get("authors") or {}).items()
    }


def collect_items(f2_dir: Path) -> list:
    """扫描发布 + 点赞两个目录，返回全部可入库的已解析文件。

    两个目录都要扫：点赞产物的原作者只在文件名前缀里，但作者已归一化，
    与发布目录用同一套对齐键。

    Args:
        f2_dir: f2 工作目录（非缺省值时按同样结构推导两个子目录）。

    Returns:
        ParsedFile 列表。
    """
    if f2_dir == DEFAULT_F2_DIR:
        roots = [DEFAULT_F2_ROOT, DEFAULT_F2_LIKE_ROOT]
    else:
        roots = [
            f2_dir / "Download/douyin/post",
            f2_dir / "Download/douyin/like",
        ]
    items: list = []
    for root in roots:
        if root.is_dir():
            items.extend(scan_directory(root))
    return items


def build_plan(
    items: list,
    alignment: dict[str, dict[str, str]],
    rows: list[tuple[str, str, str | None]],
) -> tuple[list[dict], dict[str, int]]:
    """构造回填计划（纯函数：只算不改，便于单测与预览）。

    Args:
        items: :func:`collect_items` 的结果。
        alignment: :func:`load_alignment` 的结果。
        rows: 库内未删除的 f2 行 ``[(inspiration_id, source_platform_id, source_url)]``。

    Returns:
        ``(计划列表, 跳过原因计数)``。计划项 ``kind`` 取 ``full``（改 ID + 补链接）
        或 ``url_only``（只补链接）。
    """
    skipped: Counter = Counter()
    by_pid = {pid: (insp_id, url) for insp_id, pid, url in rows}
    video_works = {item.work_key for item in items if item.kind == "video"}

    plan: list[dict] = []
    claimed: dict[str, str] = {}  # 本次已占用的新 ID → inspiration_id
    seen_rows: set[str] = set()

    for item in items:
        stem = f"{item.created}_{item.body}"
        aweme_id = alignment.get(item.author_key, {}).get(stem)
        if not aweme_id:
            skipped["不在报告的唯一对齐里（多义/未命中/非报告作者）"] += 1
            continue

        legacy_id = legacy_platform_id_for(item)
        row = by_pid.get(legacy_id)
        if row is None:
            skipped["库内无对应行（未导入该文件 / 已删除）"] += 1
            continue
        insp_id, old_url = row

        if insp_id in seen_rows:
            # 同一行被多个文件命中（跨目录同作品）：只处理一次
            skipped["同一行被多个文件命中（已去重）"] += 1
            continue

        enriched = replace(item, aweme_id=aweme_id)
        new_id = platform_id_for(enriched)
        new_url = source_url_for(enriched, is_video_work=item.work_key in video_works)
        if new_url is None:  # 理论不可达（有 aweme_id 就一定有 URL）
            skipped["无法构造原帖链接"] += 1
            continue

        entry = {
            "inspiration_id": insp_id,
            "source_file": str(item.path),
            "old_platform_id": legacy_id,
            "old_source_url": old_url,
            "new_source_url": new_url,
        }

        if new_id in claimed:
            # 同一作品被重复下载成多行：新 ID 只能给一行，其余只补链接
            entry.update(kind="url_only", new_platform_id=legacy_id)
            plan.append(entry)
            seen_rows.add(insp_id)
            skipped["同作品重复行（仅补链接，不改ID）"] += 1
            continue

        if new_id in by_pid and by_pid[new_id][0] != insp_id:
            # 防御：库里已存在该新 ID（本次实测为 0）
            skipped["目标新ID已被其他行占用（跳过）"] += 1
            continue

        entry.update(
            kind="full",
            new_platform_id=new_id,
            aweme_id=aweme_id,
        )
        claimed[new_id] = insp_id
        plan.append(entry)
        seen_rows.add(insp_id)

    return plan, dict(skipped)


def _load_rows(db_path: Path) -> list[tuple[str, str, str | None]]:
    """读未删除的 f2 行（垃圾桶不动）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT id, source_platform_id, source_url FROM inspirations "
            "WHERE deleted_at IS NULL AND source_platform_id LIKE 'f2:%'"
        ).fetchall()
    finally:
        conn.close()


def apply_plan(plan: list[dict], db_path: Path, batch_dir: Path) -> dict:
    """执行回填并落一批清单（可按清单回滚）。

    **清单先写、再改库**：素材库可能正被运行中的后端/worker 访问，若中途异常退出，
    清单仍完整记录每行的旧值 → 崩溃也能回滚（清单后写会丢掉回滚依据）。

    连接设 ``busy_timeout``：活库上并发写会偶发 database is locked，等服务端事务
    结束即可，不该直接失败。

    Args:
        plan: :func:`build_plan` 的结果（只处理其中确有变化的项）。
        db_path: 素材库路径。
        batch_dir: 批次清单目录。

    Returns:
        统计字典：updated / url_only / unchanged / manifest / seconds。
    """
    todo = [
        e
        for e in plan
        if e["kind"] == "full" or (e["old_source_url"] or None) != e["new_source_url"]
    ]

    batch_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    manifest = batch_dir / f"backfill_f2_aweme_{stamp}.json"
    manifest.write_text(
        json.dumps(
            {
                "created_at": utcnow().isoformat(timespec="seconds"),
                "kind": "backfill_f2_aweme_ids",
                "planned": len(todo),
                "applied": 0,
                "entries": todo,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(str(db_path))
    applied: list[dict] = []
    url_only = 0
    started = time.monotonic()
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        for index, entry in enumerate(todo, 1):
            if entry["kind"] == "full":
                # WHERE 带原平台 ID：只改预期的那一行（幂等 + 防误伤）
                cur = conn.execute(
                    "UPDATE inspirations SET source_platform_id = ?, source_url = ?, "
                    "updated_at = ? WHERE id = ? AND source_platform_id = ?",
                    (
                        entry["new_platform_id"],
                        entry["new_source_url"],
                        utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        entry["inspiration_id"],
                        entry["old_platform_id"],
                    ),
                )
            else:
                cur = conn.execute(
                    "UPDATE inspirations SET source_url = ?, updated_at = ? WHERE id = ?",
                    (
                        entry["new_source_url"],
                        utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        entry["inspiration_id"],
                    ),
                )
                url_only += 1
            if cur.rowcount == 0:
                continue  # 已被别处改过 → 跳过，不动
            applied.append(entry)
            if index % COMMIT_BATCH == 0:
                conn.commit()
        conn.commit()
    finally:
        conn.close()

    # 回填实际生效条数（entries 已是计划全量，回滚不依赖它）
    manifest.write_text(
        json.dumps(
            {
                "created_at": utcnow().isoformat(timespec="seconds"),
                "kind": "backfill_f2_aweme_ids",
                "planned": len(todo),
                "applied": len(applied),
                "url_only": url_only,
                "entries": todo,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "updated": len([e for e in applied if e["kind"] == "full"]),
        "url_only": url_only,
        "unchanged": len(plan) - len(applied),
        "manifest": str(manifest),
        "seconds": round(time.monotonic() - started, 1),
    }


def rollback(manifest_path: Path, db_path: Path) -> dict:
    """按批次清单原样回滚（新值 → 旧值）。

    Args:
        manifest_path: :func:`apply_plan` 落下的清单。
        db_path: 素材库路径。

    Returns:
        统计字典：reverted / missing。
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    conn = sqlite3.connect(str(db_path))
    reverted = 0
    missing = 0
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        for entry in entries:
            if entry["kind"] == "full":
                cur = conn.execute(
                    "UPDATE inspirations SET source_platform_id = ?, source_url = ? "
                    "WHERE id = ? AND source_platform_id = ?",
                    (
                        entry["old_platform_id"],
                        entry["old_source_url"],
                        entry["inspiration_id"],
                        entry["new_platform_id"],
                    ),
                )
            else:
                cur = conn.execute(
                    "UPDATE inspirations SET source_url = ? WHERE id = ?",
                    (entry["old_source_url"], entry["inspiration_id"]),
                )
            if cur.rowcount == 0:
                missing += 1
            else:
                reverted += 1
        conn.commit()
    finally:
        conn.close()
    return {"reverted": reverted, "missing": missing}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按对齐报告回填历史 f2 素材的真实作品 ID 与原帖链接（默认只读）"
    )
    parser.add_argument("--f2-dir", default=str(DEFAULT_F2_DIR), help="f2 工作目录")
    parser.add_argument("--db", default="", help="素材库路径（缺省按 settings 推导）")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="对齐报告 JSON 路径")
    parser.add_argument("--apply", action="store_true", help="真正写库（缺省只预览）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少行（试跑用）")
    parser.add_argument("--sample", type=int, default=5, help="预览时打印示例条数")
    parser.add_argument("--rollback", default="", help="按批次清单回滚，不做其他事")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = Path(args.db) if args.db else library_db_path()

    if args.rollback:
        result = rollback(Path(args.rollback), db_path)
        print(f"已回滚 {result['reverted']} 行，未命中 {result['missing']} 行")
        return 0

    f2_dir = Path(args.f2_dir)
    print(f"素材库：{db_path}")
    alignment = load_alignment(Path(args.report))
    print(f"对齐报告：{sum(len(v) for v in alignment.values())} 个唯一对齐作品，"
          f"{len(alignment)} 个作者")

    items = collect_items(f2_dir)
    print(f"磁盘文件：{len(items)} 个（发布 + 点赞）")

    rows = _load_rows(db_path)
    print(f"库内未删除的 f2 行：{len(rows)} 行")

    plan, skipped = build_plan(items, alignment, rows)
    full = [e for e in plan if e["kind"] == "full"]
    url_only = [e for e in plan if e["kind"] == "url_only"]
    print()
    print(f"计划：改 ID + 补链接 {len(full)} 行 | 仅补链接 {len(url_only)} 行")
    for reason, count in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  跳过 {count:>6}  {reason}")

    if args.limit:
        plan = plan[: args.limit]
        print(f"\n（--limit {args.limit}：本次只处理前 {len(plan)} 行）")

    for entry in plan[: args.sample]:
        print(
            f"  · {entry['old_platform_id']} → {entry['new_platform_id']}"
            f"  {entry['new_source_url']}"
        )

    if not args.apply:
        print("\n（预览模式，未写库。加 --apply 落库）")
        return 0

    # 批次清单落在 storage/import_batches（与 f2 导入同一处，便于统一回滚管理）
    batch_dir = settings.storage_root / IMPORT_BATCH_DIRNAME
    result = apply_plan(plan, db_path, batch_dir)
    print(
        f"\n✅ 已落库：改 ID {result['updated']} 行、仅补链接 {result['url_only']} 行，"
        f"耗时 {result['seconds']} 秒"
    )
    print(f"批次清单（可回滚）：{result['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
