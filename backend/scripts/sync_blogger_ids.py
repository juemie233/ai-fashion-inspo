"""按「名字 + 平台 ID」清单回填/新建抖音博主，并可把已入库素材补绑到博主。

背景
----
库内抖音博主的 `platform_user_id` 多为 NULL、`profile_url` 为空（实测 24 个里只有
2 个有值，且不是 sec_uid），而 f2 导入按「归一化昵称」绑定博主——名单缺失会让素材
进库后没有博主（本项目已入库的 f2 素材里，有 5 个作者因为库内没有对应博主而没绑）。
本脚本把清单写进 `bloggers`，并可选把**已入库**的素材补绑上。

清单格式
--------
交替的「名字」「ID」两行（无表头），也支持单行 `名字<TAB>ID` / `名字,ID`：

    夕木.
    MS4wLjABAAAADCfmS4DNh9Q4FeGE-sVvhJ3S3ZCbS_0BkpmZFdgZ91ZKpAQpGhCtrtbrZ5NbfzA9

容忍：空行、`#` 注释行、包围的引号、行尾空白。名字与 ID 的配对以「ID 行」为界。

用法
----
    cd backend
    python -m scripts.sync_blogger_ids --file ids.txt                    # 预览（默认只读）
    python -m scripts.sync_blogger_ids --file ids.txt --apply             # 更新已有博主
    python -m scripts.sync_blogger_ids --file ids.txt --apply --create-missing
    python -m scripts.sync_blogger_ids --file ids.txt --apply --bind-materials
"""

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

from .import_f2_downloads import (  # noqa: E402
    AUTHOR_MATCH_CONFIDENCE,
    library_db_path,
    normalize_author,
)

"""抖音 sec_user_id 形态：MS4wLjABAAAA 开头 + 长串（f2/网页端都用这个做用户标识）。"""
SEC_UID_RE = re.compile(r"^MS4wLjABAAAA[\w-]{8,}$")

"""按清单回填时的平台（本脚本面向抖音；小红书博主主页是 user/profile/{id} 形态）"""
PLATFORM = "douyin"

"""抖音用户主页 URL 模板（f2 的 -u 参数直接用这个形式）"""
PROFILE_URL_FMT = "https://www.douyin.com/user/{uid}"


def utcnow() -> datetime:
    """当前 UTC 时间（naive，与库内 DATETIME 列口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clean(line: str) -> str:
    """去掉行首尾空白与包围的引号（粘贴来的清单常带引号）。"""
    text = (line or "").strip()
    for quote in ('"', "'", "“", "”", "‘", "’"):
        text = text.strip(quote).strip()
    return text


def parse_pairs(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """解析「名字 / ID」清单，返回 (配对列表, 警告列表)。

    规则：`#` 开头与空行跳过；含制表符或逗号的行走「名字,ID」单行形式；
    其余行按「名字行 → ID 行」配对（ID 行以 :data:`SEC_UID_RE` 判定）。

    Args:
        text: 清单全文。

    Returns:
        ([(名字, sec_user_id)], [警告文案])；警告用于提示落单的名字或非法 ID。
    """
    pairs: list[tuple[str, str]] = []
    warnings: list[str] = []
    pending_name: str | None = None

    for raw in (text or "").splitlines():
        line = _clean(raw)
        if not line or line.startswith("#"):
            continue

        # 单行形式：名字<TAB>ID / 名字,ID
        if "\t" in line or "," in line:
            sep = "\t" if "\t" in line else ","
            name, _, uid = line.partition(sep)
            name, uid = _clean(name), _clean(uid)
            if SEC_UID_RE.match(uid):
                pairs.append((name, uid))
                pending_name = None
                continue
            warnings.append(f"单行形式但 ID 不合法，已跳过：{line[:60]}")
            continue

        if SEC_UID_RE.match(line):
            if pending_name is None:
                warnings.append(f"ID 前没有名字，已跳过：{line[:32]}…")
                continue
            pairs.append((pending_name, line))
            pending_name = None
            continue

        # 普通行 → 视为名字；若上一个名字还没配到 ID，先记警告
        if pending_name is not None:
            warnings.append(f"名字「{pending_name}」没有对应 ID（下一个名字：{line}）")
        pending_name = line

    if pending_name is not None:
        warnings.append(f"清单末尾的名字「{pending_name}」没有对应 ID")
    return pairs, warnings


@dataclass
class BloggerSyncPlan:
    """回填计划（预览与执行共用一份结构）。"""

    updates: list[dict] = field(default_factory=list)  # 已有博主要写入 uid/profile_url
    creates: list[dict] = field(default_factory=list)  # 库内没有、按 --create-missing 新建
    unchanged: list[dict] = field(default_factory=list)  # 已是该 uid，无需改动
    conflicts: list[dict] = field(default_factory=list)  # 同名多个博主等需人工确认
    missing: list[dict] = field(default_factory=list)  # 库内无此博主且未开 --create-missing
    warnings: list[str] = field(default_factory=list)


def build_sync_plan(
    pairs: list[tuple[str, str]],
    db_path: Path | None = None,
    create_missing: bool = False,
) -> BloggerSyncPlan:
    """按清单与库内博主现状生成回填计划（只读，不写任何数据）。

    匹配顺序：先按 `platform_user_id` 判「已同步」，再按**归一化昵称**匹配库内抖音
    博主（与 f2 导入的绑定口径一致）；同名命中多个博主时记为冲突交人工处理。

    Args:
        pairs: :func:`parse_pairs` 的结果。
        db_path: 素材库路径（缺省取 settings）。
        create_missing: 库内缺该博主时是否计划新建。

    Returns:
        :class:`BloggerSyncPlan`
    """
    plan = BloggerSyncPlan()
    path = db_path or library_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, name, platform, platform_user_id FROM bloggers WHERE platform = ?",
            (PLATFORM,),
        ).fetchall()
    finally:
        conn.close()

    by_uid = {r[3]: r for r in rows if r[3]}
    by_name: dict[str, list[tuple]] = {}
    for row in rows:
        by_name.setdefault(normalize_author(row[1]), []).append(row)

    for name, uid in pairs:
        existing = by_uid.get(uid)
        if existing:
            plan.unchanged.append(
                {"id": existing[0], "name": existing[1], "uid": uid, "reason": "该 sec_user_id 已存在"}
            )
            continue

        candidates = by_name.get(normalize_author(name), [])
        if len(candidates) == 1:
            blogger_id, blogger_name, _platform, old_uid = candidates[0]
            plan.updates.append(
                {
                    "id": blogger_id,
                    "name": blogger_name,
                    "input_name": name,
                    "uid": uid,
                    "old_uid": old_uid,
                    "profile_url": PROFILE_URL_FMT.format(uid=uid),
                }
            )
        elif len(candidates) > 1:
            plan.conflicts.append(
                {
                    "input_name": name,
                    "uid": uid,
                    "candidates": [{"id": c[0], "name": c[1]} for c in candidates],
                    "reason": "同名多个博主，需人工指定",
                }
            )
        elif create_missing:
            plan.creates.append(
                {"name": name, "uid": uid, "profile_url": PROFILE_URL_FMT.format(uid=uid)}
            )
        else:
            plan.missing.append(
                {"input_name": name, "uid": uid, "reason": "库内无此博主（未开 --create-missing）"}
            )
    return plan


def apply_sync_plan(
    plan: BloggerSyncPlan,
    db_path: Path | None = None,
    bind_materials: bool = False,
) -> dict:
    """执行回填计划：更新/新建博主，可选把已入库素材补绑到博主。

    绑定时按 `source_author`（f2 导入写入的归一化作者名）匹配素材，置信度与导入
    路径一致（:data:`AUTHOR_MATCH_CONFIDENCE`，区别于人脸匹配）。

    Args:
        plan: :func:`build_sync_plan` 的结果。
        db_path: 素材库路径（缺省取 settings）。
        bind_materials: 是否把已入库素材补绑到这些博主（幂等，靠唯一约束去重）。

    Returns:
        {"updated", "created", "bound", "bind_skipped", "errors"}
    """
    path = db_path or library_db_path()
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(path))
    updated = created = bound = 0
    errors: list[dict] = []

    def _bind(blogger_id: int, blogger_name: str) -> int:
        """把 source_author 命中该博主的已入库素材补绑上（幂等）。"""
        key = normalize_author(blogger_name)
        cur = conn.execute(
            "INSERT OR IGNORE INTO inspiration_bloggers "
            "(inspiration_id, blogger_id, confidence) "
            "SELECT id, ?, ? FROM inspirations "
            "WHERE deleted_at IS NULL AND source_author = ?",
            (blogger_id, AUTHOR_MATCH_CONFIDENCE, key),
        )
        return cur.rowcount or 0

    try:
        for item in plan.updates:
            try:
                conn.execute(
                    "UPDATE bloggers SET platform_user_id = ?, profile_url = ?, "
                    "updated_at = ? WHERE id = ?",
                    (item["uid"], item["profile_url"], now_str, item["id"]),
                )
                conn.commit()
                updated += 1
                if bind_materials:
                    bound += _bind(item["id"], item["name"])
                    conn.commit()
            except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断整批
                conn.rollback()
                errors.append({"name": item.get("name"), "error": str(exc)[:200]})

        for item in plan.creates:
            try:
                cur = conn.execute(
                    "INSERT INTO bloggers (name, platform, platform_user_id, profile_url, "
                    "source, created_at, updated_at) VALUES (?, ?, ?, ?, 'manual', ?, ?)",
                    (item["name"], PLATFORM, item["uid"], item["profile_url"], now_str, now_str),
                )
                conn.commit()
                created += 1
                if bind_materials and cur.lastrowid:
                    bound += _bind(int(cur.lastrowid), item["name"])
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                errors.append({"name": item.get("name"), "error": str(exc)[:200]})

        # 已同步（unchanged）的博主同样可以补绑：清单里已存在但素材还没关联
        if bind_materials:
            for item in plan.unchanged:
                bound += _bind(item["id"], item["name"])
            conn.commit()
    finally:
        conn.close()

    return {"updated": updated, "created": created, "bound": bound, "errors": errors}


def print_plan(plan: BloggerSyncPlan) -> None:
    """打印回填计划（人类可读）。"""
    print("\n=== 博主 ID 回填计划 ===")
    print(f"更新已有博主 {len(plan.updates)} 个；新建 {len(plan.creates)} 个；"
          f"已同步 {len(plan.unchanged)} 个；冲突 {len(plan.conflicts)} 个；"
          f"库内缺失未新建 {len(plan.missing)} 个")
    if plan.updates:
        print("\n-- 将更新（id / 博主名 / 原值 → 新 sec_user_id）--")
        for item in plan.updates:
            old = (item["old_uid"] or "空")
            print(f"   id={item['id']:<5}{item['name'][:14]:<16}{old[:14]:<16}→ {item['uid'][:28]}…")
    if plan.creates:
        print("\n-- 将新建 --")
        for item in plan.creates:
            print(f"   {item['name']}  → {item['uid'][:28]}…")
    if plan.conflicts:
        print("\n-- 冲突（需人工处理，本次跳过）--")
        for item in plan.conflicts:
            names = "、".join(f"#{c['id']} {c['name']}" for c in item["candidates"])
            print(f"   清单名「{item['input_name']}」命中多个博主：{names}")
    if plan.missing:
        print("\n-- 库内缺失（加 --create-missing 可新建）--")
        for item in plan.missing:
            print(f"   {item['input_name']}")
    for warning in plan.warnings:
        print(f"   ⚠ {warning}")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 sys.argv[1:]）。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(
        description="按「名字 + sec_user_id」清单回填抖音博主属性（默认只预览）"
    )
    parser.add_argument("--file", type=Path, required=True, help="清单文件路径")
    parser.add_argument("--apply", action="store_true", help="真正写入（缺省只预览）")
    parser.add_argument(
        "--create-missing", action="store_true", help="库内没有的博主一并新建"
    )
    parser.add_argument(
        "--bind-materials",
        action="store_true",
        help="把已入库素材按 source_author 补绑到这些博主（幂等）",
    )
    parser.add_argument(
        "--db", type=Path, default=None, help="素材库路径（缺省取 settings）"
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"清单文件不存在：{args.file}")
        return 1

    pairs, warnings = parse_pairs(args.file.read_text(encoding="utf-8"))
    if not pairs:
        print("清单里没有解析到「名字 + 有效 sec_user_id」配对")
        for warning in warnings:
            print(f"   ⚠ {warning}")
        return 1

    db_path = args.db or library_db_path()
    if not db_path.exists():
        print(f"素材库不存在：{db_path}")
        return 1

    plan = build_sync_plan(pairs, db_path=db_path, create_missing=args.create_missing)
    plan.warnings = warnings
    print(f"清单解析：{len(pairs)} 对（库: {db_path}）")
    print_plan(plan)

    if not args.apply:
        print("\n提示：本命令只预览，未写入任何数据。确认后加 --apply 执行。")
        return 0

    result = apply_sync_plan(
        plan, db_path=db_path, bind_materials=args.bind_materials
    )
    print("\n=== 回填完成 ===")
    print(
        f"更新 {result['updated']} 个，新建 {result['created']} 个"
        + (f"，补绑素材 {result['bound']} 条" if args.bind_materials else "")
    )
    for err in result["errors"][:10]:
        print(f"   ✗ {err.get('name')}: {err['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
