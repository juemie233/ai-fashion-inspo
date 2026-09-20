"""f2「一键获取素材」的博主清单：白名单里到底有谁、各自入库了多少条素材。

为什么单独一个模块：这是**只读视图**，与任务执行（task_runners/f2_import.py）、
批次结果浏览（f2_results.py）都不是一回事。数据来自两处，且都必须是「下载白名单」
的同一口径：

- f2 用户库（`douyin_users.db` 的 `user_info_web`）：账号 sec_user_id / 昵称 /
  作品总数——f2 的下载目标只来自它自己的用户库，所以「能下谁」只能以它为准；
- 博主库 `bloggers`（platform=douyin、排除「自动登记」）＋ :func:`match_authors`
  的判定：与「一键获取素材」实际使用的白名单共用同一段匹配逻辑，不另写一套。

素材数按 `inspirations.source_author` 聚合（f2 导入写入的就是归一化作者名），
所以既能看出白名单里每个人贡献了多少，也能看出被跳过的账号本来会带进来多少。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)

# 抖音主页前缀：清单里给每个账号一个可直接点开的主页链接
_DOUYIN_USER_URL = "https://www.douyin.com/user/"


async def _material_counts(db: AsyncSession) -> dict[str, int]:
    """库内抖音素材数：归一化来源作者 → 条数（排除垃圾桶）。

    ``source_author`` 在 f2 导入时写入的就是归一化作者名，这里再归一化一次是为了
    兼容历史行（早期导入可能写的是带 `√`/编号的目录名）。
    """
    from scripts import import_f2_downloads as f2

    rows = (
        await db.execute(
            select(Inspiration.source_author, func.count())
            .where(
                Inspiration.source_type == "douyin",
                Inspiration.deleted_at.is_(None),
                Inspiration.source_author.is_not(None),
            )
            .group_by(Inspiration.source_author)
        )
    ).all()

    counts: dict[str, int] = defaultdict(int)
    for author, count in rows:
        key = f2.normalize_author(author or "")
        if key:
            counts[key] += int(count)
    return dict(counts)


def _author_sort_key(row: dict) -> tuple[int, str]:
    """清单排序键：素材多的在前，同数量按昵称（结果稳定可预期）。"""
    return (-int(row["materials"]), str(row["nickname"]))


def _author_row(author: dict, blogger: dict | None, materials: int) -> dict:
    """组装一行清单数据（f2 账号 + 命中的博主 + 素材数）。"""
    sec_user_id = str(author.get("sec_user_id") or "")
    return {
        "nickname": str(author.get("nickname") or ""),
        "sec_user_id": sec_user_id,
        "aweme_count": int(author.get("aweme_count") or 0),
        "materials": materials,
        "blogger_id": blogger.get("id") if blogger else None,
        "blogger_name": blogger.get("name") if blogger else None,
        "profile_url": f"{_DOUYIN_USER_URL}{sec_user_id}" if sec_user_id else "",
    }


async def get_f2_authors(db: AsyncSession) -> dict:
    """列出 f2 用户库里的账号，并标出哪些在「一键获取素材」的下载白名单里。

    Args:
        db: 数据库会话（用于统计各作者已入库的素材数）。

    Returns:
        ``{"available", "f2_dir", "filter_active", "registered", "unknown",
        "registered_count", "unknown_count", "note"}``。``filter_active=False``
        表示库里一个抖音博主都没登记，此时白名单不生效（全部账号都会被处理），
        前端据此说明——否则「全都是已登记」看起来像数据不对。
    """
    from scripts import import_f2_downloads as f2

    f2_dir = f2.DEFAULT_F2_DIR
    authors = await asyncio.to_thread(f2.load_f2_authors, f2_dir)
    bloggers = await asyncio.to_thread(f2.load_douyin_bloggers)
    counts = await _material_counts(db)

    # 与「一键获取素材」执行侧同一判定：有库内抖音博主才启用白名单，
    # 一个都没有时退回「不按作者过滤」的旧口径（见 execute_f2_import）
    filter_active = bool(bloggers)
    if filter_active:
        matched = f2.match_authors(authors, bloggers)
    else:
        matched = [(author, None) for author in authors]

    registered: list[dict] = []
    unknown: list[dict] = []
    for author, blogger in matched:
        key = f2.normalize_author(author.get("nickname") or "")
        row = _author_row(author, blogger, counts.get(key, 0))
        # 白名单未启用时不存在「未登记」这一档：全部账号都会被处理
        if blogger is not None or not filter_active:
            registered.append(row)
        else:
            unknown.append(row)

    # 素材多的排前面（看「谁的贡献最大」），其余按昵称稳定排序
    registered.sort(key=_author_sort_key)
    unknown.sort(key=_author_sort_key)

    if not authors:
        note = (
            f"f2 用户库为空或不存在（{f2_dir / f2.F2_AUTHOR_DB}）："
            "先手动跑一次 f2 完成首次下载，这里才会有账号"
        )
    elif not filter_active:
        note = (
            "库里还没有登记任何抖音博主，下载白名单尚未生效——"
            "当前「一键获取素材」会处理 f2 用户库里的全部账号"
        )
    elif unknown:
        note = (
            f"另有 {len(unknown)} 个 f2 账号未登记到博主库，默认会被跳过；"
            "勾选「包含 f2 里未登记到博主库的账号」才会处理它们"
        )
    else:
        note = "f2 用户库里的账号都已登记到博主库，白名单无遗漏"

    return {
        "available": bool(authors),
        "f2_dir": str(f2_dir),
        "filter_active": filter_active,
        "registered": registered,
        "unknown": unknown,
        "registered_count": len(registered),
        "unknown_count": len(unknown),
        "note": note,
    }
