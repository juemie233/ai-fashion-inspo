"""f2 导入的「库内作品 ID 索引」：下载前判重与合集补齐共用。

从 `task_runners/f2_import.py`（2004 行）拆出的第一片：这两个函数只依赖 DB 会话与
`inspirations.source_platform_id`，不碰任务状态、不下载、不写库，是整条链路里最内聚的部分。

平台 ID 形态约定（两处函数都依赖）：`f2:<19 位作品 ID>#<类型><序号>`；
旧素材存的是「文件名算出的合成哈希」，与作品 ID 无法互推——那部分只能靠内容哈希在
入库阶段判重，这里不假装知道（`known_library_aweme_ids` 只认 19 位数字）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def known_library_aweme_ids(db: AsyncSession) -> set[str]:
    """库内已登记的**真实作品 ID**集合（含垃圾桶），用于「下载前跳过已入库作品」。

    为什么含垃圾桶：垃圾桶是五层判重里独立的一层（被丢弃的内容不复活），它的作品
    同样不该再下一遍——下了也会被判重跳过，纯浪费带宽。

    只认 `f2:<19 位数字>#…` 这一种平台 ID：旧素材存的是「文件名算出来的合成哈希」，
    与作品 ID 无法互推，那部分只能靠内容哈希在入库阶段判重（这里不假装知道）。

    Returns:
        作品 ID 字符串集合（19 位数字）。
    """
    from app.models.inspiration import Inspiration

    rows = await db.execute(
        select(Inspiration.source_platform_id).where(
            Inspiration.source_platform_id.like("f2:%")
        )
    )
    ids: set[str] = set()
    for (platform_id,) in rows:
        head = str(platform_id).split("#", 1)[0]  # f2:<作品 ID>
        value = head[3:]
        if len(value) == 19 and value.isdigit():
            ids.add(value)
    return ids


"""合集补齐时单条 SQL 的 IN 规模（SQLite 变量上限 999，留余量）。"""
_EXTRA_ID_CHUNK = 400


async def collect_library_ids_by_aweme(
    db: AsyncSession, aweme_ids: list[str] | set[str]
) -> list[str]:
    """把作品 ID 解析成库内**未删除**素材 ID（供「抖音收藏」合集补齐）。

    为什么需要它：「先扫描、后下载」会把**已在库**的作品整件跳过（省一次下载），
    而这些作品明明在你的收藏里，却永远进不了「抖音收藏」合集——合集只收「本批新入库」
    的那部分。这里按平台 ID 前缀把它们找回来一并入合集（E）。

    只取未删除的素材：合集里不该出现垃圾桶内容（垃圾桶是负样本区）。

    Args:
        db: 数据库会话。
        aweme_ids: 作品 ID（19 位数字）。

    Returns:
        命中的素材 ID 列表（去重）。
    """
    from sqlalchemy import func

    from app.models.inspiration import Inspiration

    wanted = sorted({str(a) for a in aweme_ids if a})
    if not wanted:
        return []
    # 平台 ID 形如 `f2:<作品 ID>#<类型><序号>`：用 substr/instr 把中间的 ID 抠出来做
    # IN 比较（一次查询搞定一批；逐条 LIKE 拼 OR 会退化成几百次扫描）。
    # `instr`/`substr` 是 SQLite 方言——本项目数据库就是 SQLite（见 CLAUDE.md），
    # 若将来换库，这里要改成对应的字符串函数。
    aweme_of_platform = func.substr(
        Inspiration.source_platform_id,
        4,
        func.instr(Inspiration.source_platform_id, "#") - 4,
    )
    found: list[str] = []
    for start in range(0, len(wanted), _EXTRA_ID_CHUNK):
        chunk = wanted[start : start + _EXTRA_ID_CHUNK]
        rows = await db.execute(
            select(Inspiration.id).where(
                Inspiration.deleted_at.is_(None),
                Inspiration.source_platform_id.like("f2:%#%"),
                aweme_of_platform.in_(chunk),
            )
        )
        found.extend(str(row[0]) for row in rows)
    return sorted(set(found))
