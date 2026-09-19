"""「我的喜欢」入库后补建来源作者博主，并把本批素材绑定到对应博主。

为什么单独一个模块：这是 f2 点赞链路专有的**归属补登记**规则，与「结果浏览/
审查」和「任务执行」都不是一回事，放一起会让两边都变厚。

三条约定：
  1. **只补未绑定的作者**：批次清单里 `blogger_id` 非空的条目在导入时就已匹配到
     已登记博主（见 `build_import_plan` 的唯一候选绑定），这里不再插手
  2. **自动登记的博主不进下载白名单**：新建的博主写
     ``source=AUTO_BLOGGER_SOURCE``，`load_douyin_bloggers` 默认排除它们——
     点赞作者动辄几百个（实测 585 个原作者），若直接进白名单，下次「一键获取
     素材」会去翻几百个主页的全部历史（f2 每页固定 sleep，小时/天级 + 风控风险）。
     用户在博主列表确认后点「纳入追踪」即改回 manual。
  3. **幂等**：博主按**归一化名**匹配复用（与导入侧的绑定口径一致），素材-博主
     关联走 `blogger_service.link_batch`（已存在自动跳过），重复执行不会产生重复记录。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Blogger
from app.services.person.services import blogger_service

logger = logging.getLogger(__name__)


async def register_batch_bloggers(db: AsyncSession, entries: list[dict]) -> dict:
    """把本批素材里**未绑定博主**的来源作者补建成抖音博主，并绑定素材。

    Args:
        db: 数据库会话。
        entries: 批次清单的 imported 条目（含 ``inspiration_id`` / ``author_dir`` /
            ``blogger_id``）。

    Returns:
        {"authors": 待补登记的来源作者数, "created": 新建博主数,
         "reused": 复用了已有博主的作者数, "ambiguous": 同名多候选而跳过的作者数,
         "linked": 新增关联的素材数, "existing": 已有（本次跳过）的素材数,
         "failed": 素材已不存在而绑定失败的条数}
    """
    from scripts import import_f2_downloads as f2

    # 只处理「未绑定 + 有来源作者」的条目：已绑定的在导入阶段就已匹配到已登记博主
    grouped: dict[str, list[str]] = defaultdict(list)
    display: dict[str, str] = {}
    for entry in entries:
        inspiration_id = str(entry.get("inspiration_id") or "")
        author = str(entry.get("author_dir") or "").strip()
        if not inspiration_id or not author or entry.get("blogger_id"):
            continue
        key = f2.normalize_author(author)
        if not key:
            continue
        display.setdefault(key, author)
        grouped[key].append(inspiration_id)

    stats = {
        "authors": len(grouped),
        "created": 0,
        "reused": 0,
        "ambiguous": 0,
        "linked": 0,
        "existing": 0,
        "failed": 0,
    }
    if not grouped:
        return stats

    # 现有抖音博主（含自动登记）：归一化名 → 候选列表
    rows = (
        await db.execute(
            select(Blogger.id, Blogger.name, Blogger.source).where(
                Blogger.platform == "douyin"
            )
        )
    ).all()
    by_key: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for blogger_id, name, source in rows:
        by_key[f2.normalize_author(name or "")].append((blogger_id, source or "manual"))

    for key, inspiration_ids in grouped.items():
        candidates = by_key.get(key) or []
        if len(candidates) > 1:
            # 同名多候选（库里本来就有重名博主）：不猜，留给用户手工绑定
            stats["ambiguous"] += 1
            logger.warning(
                f"f2 自动登记博主：{display[key]} 在库里有 {len(candidates)} 条同名记录，跳过"
            )
            continue
        if len(candidates) == 1:
            blogger_id = candidates[0][0]
            stats["reused"] += 1
        else:
            blogger = Blogger(
                name=display[key], platform="douyin", source=f2.AUTO_BLOGGER_SOURCE
            )
            db.add(blogger)
            await db.flush()  # 取 id；后面按素材逐条绑定
            blogger_id = blogger.id
            by_key[key] = [(blogger_id, f2.AUTO_BLOGGER_SOURCE)]
            stats["created"] += 1

        for inspiration_id in inspiration_ids:
            try:
                # 与导入侧的作者匹配口径一致：置信度同 AUTHOR_MATCH_CONFIDENCE。
                # 用 link_batch 而不是 link：它区分散「新建关联」与「已存在跳过」，
                # 手工回填重复点时的统计才不会虚报
                outcome = await blogger_service.link_batch(
                    db, inspiration_id, [blogger_id], confidence=f2.AUTHOR_MATCH_CONFIDENCE
                )
                if outcome["links"]:
                    stats["linked"] += 1
                elif outcome["inspiration_exists"]:
                    stats["existing"] += 1
                else:
                    stats["failed"] += 1
            except Exception as exc:  # noqa: BLE001 —— 单条失败不影响其余素材
                stats["failed"] += 1
                logger.warning(
                    f"f2 自动登记博主：素材 {inspiration_id} 绑定博主 #{blogger_id} 失败：{exc}"
                )

    await db.commit()
    return stats
