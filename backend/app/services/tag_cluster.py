"""自动聚类：名称相似优先（含共现辅助），产出候选合并组（人工确认后应用）。

聚类策略：
- 名称归一化（全角→半角、小写、去空白标点）后比较；
- 候选对：同类别内首字相同；归一化后同名视为相似度 1.0；
  包含/前缀匹配（短名被长名包含且长度差 ≤ 4）视为 0.8；
  其余用 string_similarity（rapidfuzz，复用 tag_normalizer）；
- 共现辅助：候选对在同一素材共现 ≥ 2 次时相似度 + 0.1（可关闭）；
- 并查集把候选对连通成组（组内成员 ≥ min_group_size），
  组内选「使用次数最高」为建议主标签，其余为建议合并源；
- 仅产出「合并建议」，是否执行由用户确认后调 apply_tag_clusters。
"""

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import Inspiration
from app.models.tag import InspirationTag, Tag
from app.services.task_runners.common import _chunked
from app.utils.tag_normalizer import string_similarity

# 全角→半角区间：\uFF01-\uFF5E 为全角标点/字母/数字，\u3000 为全角空格。
# 全角空格在 _normalize_for_compare 中特判转半角空格（不参与去空白）。
_FULLWIDTH_RE = re.compile("[\uFF01-\uFF5E\u3000]")


def _normalize_for_compare(name: str) -> str:
    """名称归一化（仅用于相似度比较，不落库）：全角转半角、小写、去空白与标点。"""
    s = name.strip().lower()
    s = _FULLWIDTH_RE.sub(
        lambda m: " " if m.group(0) == "\u3000" else chr(ord(m.group(0)) - 0xFEE0),
        s,
    )
    # \W 匹配非「单词字符」：中文/字母/数字之外的标点、空白等全部去除
    return re.sub(r"[\s\W_]+", "", s)


def _pair_similarity(norm_a: str, norm_b: str) -> float:
    """名称相似度：短名包含于长名且长度差 ≤ 4 视为 0.8，否则用编辑距离相似度。"""
    if not norm_a or not norm_b:
        return 0.0
    if len(norm_a) > len(norm_b):
        long_n, short_n = norm_a, norm_b
    else:
        long_n, short_n = norm_b, norm_a
    if short_n and len(long_n) - len(short_n) <= 4 and short_n in long_n:
        return 0.8
    return string_similarity(norm_a, norm_b)


class _UnionFind:
    """并查集：把候选相似对连通成聚类组。"""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


async def _cooccurrence_boost(
    db: AsyncSession, pairs: list[tuple[int, int, float, str]]
) -> list[tuple[int, int, float, str]]:
    """共现辅助：候选对在同一素材共现 ≥ 2 次，相似度 + 0.1（封顶 1.0）。

    共现口径与使用次数一致：仅统计未删除素材的关联（垃圾桶素材不计）。
    """
    if not pairs:
        return pairs
    involved = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    links: dict[int, set[str]] = {}
    for chunk in _chunked(involved):
        result = await db.execute(
            select(InspirationTag.inspiration_id, InspirationTag.tag_id)
            .join(Inspiration, InspirationTag.inspiration_id == Inspiration.id)
            .where(
                InspirationTag.tag_id.in_(chunk),
                Inspiration.deleted_at.is_(None),
            )
        )
        for insp_id, tag_id in result.all():
            links.setdefault(tag_id, set()).add(insp_id)

    boosted: list[tuple[int, int, float, str]] = []
    for a, b, sim, reason in pairs:
        common = len(links.get(a, set()) & links.get(b, set()))
        if common >= 2:
            boosted.append((a, b, min(1.0, sim + 0.1), f"{reason}（共现加成）"))
        else:
            boosted.append((a, b, sim, reason))
    return boosted


async def scan_tag_clusters(
    db: AsyncSession,
    threshold: float = 0.75,
    use_cooccurrence_boost: bool = True,
    min_group_size: int = 2,
) -> dict:
    """执行自动聚类扫描，返回候选合并组列表。

    返回结构（写回任务 result，供前端展示与 apply 时解析）::

        {
            "total": 候选组数量,
            "threshold": 相似度阈值,
            "use_cooccurrence_boost": 是否启用共现加成,
            "min_group_size": 最小组成员数,
            "groups": [
                {
                    "id": "g1",
                    "reason": "名称相似，相似度 0.85",
                    "suggested_target": {"id", "name", "category", "usage_count"},
                    "members": [{"id", "name", "category", "usage_count"}, ...],
                },
            ],
        }
    """
    result = await db.execute(select(Tag))
    tags = result.scalars().all()
    total = len(tags)
    if total == 0:
        return {
            "total": 0,
            "threshold": threshold,
            "use_cooccurrence_boost": use_cooccurrence_boost,
            "min_group_size": min_group_size,
            "groups": [],
        }

    from app.services.tag_health import _usage_counts

    usage = await _usage_counts(db)
    name_map = {t.id: t.name for t in tags}
    category_map = {t.id: t.category for t in tags}

    # 按类别分组 + 名称归一化（跨类别不比，与重复扫描口径一致）
    by_category: dict[str, list[tuple[int, str]]] = {}
    for t in tags:
        by_category.setdefault(t.category, []).append(
            (t.id, _normalize_for_compare(t.name))
        )

    def _compute_pairs() -> list[tuple[int, int, float, str]]:
        """CPU 密集的候选对计算（放线程池执行，避免阻塞事件循环）。"""
        pairs: list[tuple[int, int, float, str]] = []
        for category, cat_tags in by_category.items():
            n = len(cat_tags)
            if n < 2:
                continue
            for i in range(n):
                id_a, norm_a = cat_tags[i]
                if not norm_a:
                    continue
                for j in range(i + 1, n):
                    id_b, norm_b = cat_tags[j]
                    if not norm_b or norm_a[0] != norm_b[0]:
                        continue
                    if norm_a == norm_b:
                        pairs.append((id_a, id_b, 1.0, "名称归一化后一致"))
                        continue
                    sim = _pair_similarity(norm_a, norm_b)
                    if sim >= threshold:
                        pairs.append((id_a, id_b, sim, "名称相似"))
        return pairs

    pairs = await asyncio.to_thread(_compute_pairs)
    if use_cooccurrence_boost:
        pairs = await _cooccurrence_boost(db, pairs)

    if not pairs:
        return {
            "total": 0,
            "threshold": threshold,
            "use_cooccurrence_boost": use_cooccurrence_boost,
            "min_group_size": min_group_size,
            "groups": [],
        }

    # 并查集聚类
    involved = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
    index = {tid: i for i, tid in enumerate(involved)}
    uf = _UnionFind(len(involved))
    for a, b, _sim, _reason in pairs:
        uf.union(index[a], index[b])

    group_members: dict[int, list[int]] = {}
    for tid in involved:
        group_members.setdefault(uf.find(index[tid]), []).append(tid)

    groups_out: list[dict] = []
    for member_ids in group_members.values():
        if len(member_ids) < min_group_size:
            continue
        members = [
            {
                "id": tid,
                "name": name_map[tid],
                "category": category_map[tid],
                "usage_count": usage.get(tid, 0),
            }
            for tid in member_ids
        ]
        members.sort(key=lambda m: (-m["usage_count"], m["id"]))
        # 组理由：组内相似度最高的一对
        best_sim, best_reason = 0.0, "名称相似"
        member_set = set(member_ids)
        for a, b, sim, reason in pairs:
            if a in member_set and b in member_set and sim > best_sim:
                best_sim, best_reason = sim, reason
        groups_out.append(
            {
                "reason": f"{best_reason}，相似度 {best_sim:.2f}",
                "suggested_target": members[0],
                "members": members,
                "_sim": best_sim,
            }
        )

    # 组排序：成员多优先、组内相似度高优先；再赋稳定组号
    groups_out.sort(key=lambda g: (-len(g["members"]), -g["_sim"]))
    for i, g in enumerate(groups_out, 1):
        g["id"] = f"g{i}"
        g.pop("_sim")

    return {
        "total": len(groups_out),
        "threshold": threshold,
        "use_cooccurrence_boost": use_cooccurrence_boost,
        "min_group_size": min_group_size,
        "groups": groups_out,
    }


async def apply_tag_clusters(
    db: AsyncSession,
    groups: list[dict],
    batch_id: str | None = None,
) -> dict:
    """应用候选合并组：组内源标签合并到目标（可保留源名为别名），全部写操作历史。

    参数:
        groups: [{"group_id"?, "target_tag_id": int, "source_tag_ids": [...],
                  "keep_as_alias": bool}]
        batch_id: 历史批次 ID；不传由服务端生成

    返回:
        {"applied", "merged", "aliases_created", "errors", "batch_id"}
    """
    from app.services.tag_alias import TagConflictError as AliasConflictError
    from app.services.tag_alias import create_alias
    from app.services.tag_crud import TagConflictError, TagNotFoundError, merge_tags
    from app.services.tag_history_service import new_batch_id

    batch_id = batch_id or new_batch_id("cluster")
    merged = 0
    aliases_created = 0
    errors: list[dict] = []
    processed_groups = 0

    for g in groups:
        target_id = g.get("target_tag_id")
        source_ids = g.get("source_tag_ids") or []
        keep_alias = g.get("keep_as_alias", True)
        group_label = g.get("group_id") or f"target={target_id}"

        if not target_id or not source_ids:
            errors.append(
                {"group": group_label, "message": "缺少 target_tag_id 或 source_tag_ids"}
            )
            continue

        # 校验源/目标存在并记下源标签名（合并后源被删，保留别名需要原名）
        tags_result = await db.execute(
            select(Tag).where(Tag.id.in_([target_id] + list(source_ids)))
        )
        tag_map = {t.id: t for t in tags_result.scalars().all()}
        if target_id not in tag_map:
            errors.append({"group": group_label, "message": f"目标标签 {target_id} 不存在"})
            continue
        missing = [sid for sid in source_ids if sid not in tag_map]
        if missing:
            errors.append({"group": group_label, "message": f"源标签不存在: {missing}"})
            continue

        processed_groups += 1
        for source_id in source_ids:
            if source_id == target_id:
                errors.append({"group": group_label, "message": f"源标签 {source_id} 与目标相同"})
                continue
            source_name = tag_map[source_id].name
            try:
                await merge_tags(db, source_id, target_id, batch_id=batch_id)
                merged += 1
            except Exception as e:  # noqa: BLE001 单组失败不阻断其余组，错误汇总返回
                errors.append({"group": group_label, "message": f"合并 {source_id}→{target_id} 失败: {e}"})
                continue

            if keep_alias:
                try:
                    await create_alias(db, target_id, source_name, batch_id=batch_id)
                    aliases_created += 1
                except (AliasConflictError, TagConflictError, TagNotFoundError) as e:
                    errors.append(
                        {"group": group_label, "message": f"保留别名「{source_name}」失败: {e}"}
                    )
            await db.commit()

    return {
        "applied": processed_groups,
        "merged": merged,
        "aliases_created": aliases_created,
        "errors": errors,
        "batch_id": batch_id,
    }
