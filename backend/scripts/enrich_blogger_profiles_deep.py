"""博主主页「深度补全」批处理：按作品 ID 让 f2 反查作者，补 sec_user_id / 主页链接 / IP 属地。

背景（2026-10-24 实测）：
- ``/api/bloggers/enrich-missing-profile`` 的两条离线路径对「我的喜欢」自动登记的博主
  无效——他们连 ``platform_user_id`` 都没有，而 f2 用户库（``douyin_users.db``）只有
  24 行：f2 **只在「主页作品」模式**写这张表，点赞/收藏模式只把作者名写进文件名，
  因此「按昵称唯一命中」几乎零命中；
- 唯一真正能补上的路是「拿一个真实作品 ID 跑一次 ``f2 -M one``」：f2 会把作者资料
  INSERT 进它自己的用户库（顺带 IP 属地），我们从**新增行**里取回 ``sec_user_id``；
  实测单次约 5 秒，436 位博主约 40 分钟。

为什么不复用任务队列：worker 默认串行（``worker_concurrency=1``），几百次联网反查会把
整个队列堵住几十分钟，采集与 AI 分析全部排队。本脚本是**独立进程**，不占 worker 槽位；
每处理完一位立刻提交，可随时 Ctrl-C，重跑自动跳过已补好的博主（按当前缺字段重新取目标）。

用法（在 ``backend`` 目录下执行）::

    python scripts/enrich_blogger_profiles_deep.py --dry-run
    python scripts/enrich_blogger_profiles_deep.py --limit 5
    nohup python scripts/enrich_blogger_profiles_deep.py > ../logs/enrich-deep.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, or_, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.inspiration import Inspiration  # noqa: E402
from app.models.person import Blogger, InspirationBlogger  # noqa: E402
from app.models.task import TaskQueue  # noqa: E402
from app.services.blogger_enrichment_service import (  # noqa: E402
    _DOUYIN_PROFILE_URL,
    find_blogger_awemes,
    resolve_douyin_profile,
)
from scripts import import_f2_downloads as f2  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="抖音博主主页深度补全（按作品 ID 让 f2 反查作者）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少位（0=全部）")
    parser.add_argument("--interval", type=float, default=3.0, help="每位之间的间隔秒数（默认 3）")
    parser.add_argument("--dry-run", action="store_true", help="只列出目标博主，不联网、不写库")
    parser.add_argument(
        "--no-wait-f2",
        action="store_true",
        help="不在有 f2 采集任务运行时等待（默认等待，避免两个浏览器实例抢 Cookie）",
    )
    return parser.parse_args()


def _missing_fields_clause():
    """「缺字段」判据：三个字段缺任一即视为待补全。"""
    return or_(
        Blogger.platform_user_id.is_(None),
        Blogger.platform_user_id == "",
        Blogger.profile_url.is_(None),
        Blogger.profile_url == "",
        Blogger.ip_location.is_(None),
        Blogger.ip_location == "",
    )


async def _load_targets(db: AsyncSession) -> list[tuple[Blogger, int]]:
    """取待补全的抖音博主（按有效素材数降序：素材多的先补，收益最大）。

    Returns:
        ``[(博主, 有效素材数)]``；只含**有素材**的博主（无素材的已在数据清理时删除）。
    """
    count_subq = (
        select(
            InspirationBlogger.blogger_id.label("bid"),
            func.count().label("n"),
        )
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .where(Inspiration.deleted_at.is_(None))
        .group_by(InspirationBlogger.blogger_id)
        .subquery()
    )
    stmt = (
        select(Blogger, func.coalesce(count_subq.c.n, 0).label("cnt"))
        .join(count_subq, count_subq.c.bid == Blogger.id)
        .where(Blogger.platform == "douyin", _missing_fields_clause())
        .order_by(func.coalesce(count_subq.c.n, 0).desc(), Blogger.id.asc())
    )
    rows = (await db.execute(stmt)).all()
    return [(row[0], int(row[1])) for row in rows]


async def _f2_task_active(db: AsyncSession) -> bool:
    """是否有 f2 采集任务在跑（running/pending）：有则本脚本等待，避免抢浏览器与 Cookie。"""
    total = (
        await db.execute(
            select(func.count())
            .select_from(TaskQueue)
            .where(
                TaskQueue.type.like("f2%"),
                TaskQueue.status.in_(("running", "pending")),
            )
        )
    ).scalar()
    return bool(total or 0)


async def _reverse_with_existing_id(db: AsyncSession, blogger: Blogger) -> dict:
    """已有 ``sec_user_id`` 但缺主页链接/IP 属地：直接按作品 ID 反查并**只补空缺**。

    ``resolve_douyin_profile`` 对「ID + 链接齐全」的博主会直接返回「无需解析」，
    因此这里绕过它、直接调用 f2 反查（与它第 3 条路径同一口径，含昵称交叉验证）。

    Args:
        db: 数据库会话。
        blogger: 已有 ``sec_user_id`` 的抖音博主。

    Returns:
        ``{"status": "updated"|"failed", "detail"|"reason": str}``。
    """
    works = await find_blogger_awemes(db, blogger.id, limit=1)
    if not works:
        return {"status": "failed", "reason": "素材里没有真实作品 ID，无法反查作者"}
    aweme_id, source_url = works[0]
    result = await asyncio.to_thread(
        f2.resolve_f2_author_by_aweme, f2.DEFAULT_F2_DIR, aweme_id, source_url or None
    )
    if not result.get("ok"):
        return {"status": "failed", "reason": str(result.get("reason") or "")}

    sec_user_id = str(result.get("sec_user_id") or "")
    nickname = str(result.get("nickname") or "")
    if sec_user_id and sec_user_id != (blogger.platform_user_id or "").strip():
        # 反查到的作者与已登记 ID 不一致：宁可不写，也不把别人的信息绑到 TA 头上
        return {
            "status": "failed",
            "reason": f"反查到的作者（{nickname or sec_user_id}）与已登记 sec_user_id 不一致，已放弃",
        }
    if f2.normalize_author(nickname) != f2.normalize_author(blogger.name or ""):
        return {
            "status": "failed",
            "reason": f"反查到的作者是「{nickname}」，与博主名「{blogger.name}」不一致，已放弃写入",
        }

    changed: list[str] = []
    if not (blogger.profile_url or "").strip():
        blogger.profile_url = f"{_DOUYIN_PROFILE_URL}{sec_user_id}"
        changed.append("主页链接")
    ip_location = str(result.get("ip_location") or "")
    if ip_location and not (blogger.ip_location or "").strip():
        blogger.ip_location = ip_location
        changed.append(f"IP 属地 {ip_location}")
    if not changed:
        return {"status": "failed", "reason": "反查成功但没有可补的空缺字段"}
    await db.commit()
    return {"status": "updated", "detail": "、".join(changed)}


async def _enrich_one(db: AsyncSession, blogger: Blogger) -> dict:
    """补全单个博主：无 ID 走「本地→昵称→作品反查」，已有 ID 走「作品反查补空缺」。

    Args:
        db: 数据库会话。
        blogger: 待补全的抖音博主。

    Returns:
        ``{"status", "detail"|"reason"}``；status ∈ updated / failed。
    """
    if not (blogger.platform_user_id or "").strip():
        result = await resolve_douyin_profile(db, blogger, max_works=1)
        if result.get("ok"):
            return {
                "status": "updated",
                "detail": f"来源 {result.get('source')}（ID + 主页链接）",
            }
        return {"status": "failed", "reason": str(result.get("reason") or "")}
    return await _reverse_with_existing_id(db, blogger)


async def _run(args: argparse.Namespace) -> int:
    """主流程：取目标 → 逐个补全 → 打印进度与失败原因汇总。"""
    started = time.time()
    ok = 0
    failed = 0
    reasons: Counter[str] = Counter()
    async with async_session() as db:
        targets = await _load_targets(db)
        total = len(targets)
        print(
            f"待补全抖音博主 {total} 位（缺 sec_user_id / 主页链接 / IP 属地，且有素材）",
            flush=True,
        )
        if args.dry_run:
            for blogger, count in targets:
                uid = "有" if (blogger.platform_user_id or "").strip() else "无"
                print(f"  #{blogger.id} {blogger.name} 素材 {count} · sec_user_id {uid}")
            return 0

        limit = args.limit or total
        for index, (blogger, count) in enumerate(targets[:limit], start=1):
            while not args.no_wait_f2 and await _f2_task_active(db):
                print("  f2 采集任务在进行中，等待 30 秒后再继续…", flush=True)
                await asyncio.sleep(30)
            began = time.time()
            try:
                result = await _enrich_one(db, blogger)
            except Exception as exc:  # noqa: BLE001 —— 单博主失败不中断整批
                await db.rollback()
                result = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            cost = time.time() - began
            if result["status"] == "updated":
                ok += 1
                extra = result.get("detail") or ""
            else:
                failed += 1
                extra = result.get("reason") or ""
                reasons[extra.split("：")[0][:40]] += 1
            print(
                f"[{index}/{limit}] #{blogger.id} {blogger.name}（素材 {count}）"
                f" → {result['status']} {cost:.1f}s {extra}",
                flush=True,
            )
            if index % 20 == 0:
                elapsed = time.time() - started
                eta = elapsed / index * (limit - index)
                print(
                    f"  —— 进度 {index}/{limit}，成功 {ok} 失败 {failed}，"
                    f"已用 {elapsed / 60:.1f} 分钟，预计剩余 {eta / 60:.1f} 分钟",
                    flush=True,
                )
            await asyncio.sleep(args.interval)

    elapsed = time.time() - started
    print(
        f"完成：成功 {ok} 位，失败 {failed} 位，耗时 {elapsed / 60:.1f} 分钟",
        flush=True,
    )
    for reason, count in reasons.most_common():
        print(f"  失败原因 ×{count}：{reason}", flush=True)
    return 0


def main() -> int:
    """脚本入口。"""
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
