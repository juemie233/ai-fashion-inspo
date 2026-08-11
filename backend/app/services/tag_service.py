"""标签服务：CRUD、标准化、合并以及预设数据导入。"""

import logging
from difflib import SequenceMatcher
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag, InspirationTag

logger = logging.getLogger(__name__)


# 预设标签体系（按类别组织）
SEED_TAGS: dict[str, list[str]] = {
    "style": [
        "JK制服", "汉服", "Lolita", "Y2K", "CleanFit", "法式", "日系",
        "韩系", "学院风", "Gorpcore", "街头", "新中式", "复古", "极简",
        "美式复古", "英伦风", "波西米亚", "运动风", "甜美风", "暗黑风",
    ],
    "item_type": [
        "百褶裙", "过膝袜", "水手服", "西装外套", "阔腿裤", "马丁靴",
        "贝雷帽", "白衬衫", "卫衣", "牛仔裤", "半身裙", "连衣裙",
        "针织衫", "风衣", "羽绒服", "T恤", "背心", "短裤", "高跟鞋",
        "运动鞋", "乐福鞋", "玛丽珍鞋", "帆布鞋", "包包", "腰带", "围巾",
    ],
    "color": [
        "白色", "黑色", "灰色", "米色", "棕色", "海军蓝", "酒红",
        "粉色", "红色", "橙色", "黄色", "绿色", "蓝色", "紫色",
        "卡其色", "牛仔蓝", "格纹", "条纹", "碎花", "豹纹",
    ],
    "body_part": [
        "过膝", "露腰", "高腰", "V领", "圆领", "高领", "一字肩",
        "七分袖", "长袖", "短袖", "无袖", "拖地", "九分", "七分",
        "及膝", "迷你", "中长款", "长款", "短款",
    ],
    "fit": [
        "宽松", "修身", "Oversized", "直筒", "紧身", "A字", "H型",
        "X型", "喇叭", "锥形", "阔腿",
    ],
    "occasion": [
        "日常", "通勤", "约会", "出游", "校园", "派对", "运动",
        "约会夜", "面试", "居家", "度假", "逛街",
    ],
    "season": [
        "春季", "夏季", "秋季", "冬季", "早春", "初秋",
    ],
    "attribute": [
        "露脸", "不露脸", "全身", "半身", "坐姿", "站姿",
        "对镜自拍", "他拍", "叠穿", "单穿", "街拍", "棚拍",
    ],
}


def _similarity(a: str, b: str) -> float:
    """计算两个字符串的相似度（0-1）。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def seed_tags(db: AsyncSession) -> int:
    """导入预设标签（跳过已存在的标签）。返回新增标签数量。"""
    added = 0
    for category, names in SEED_TAGS.items():
        for name in names:
            existing = await db.execute(select(Tag).where(Tag.name == name))
            if not existing.scalar_one_or_none():
                db.add(Tag(name=name, category=category))
                added += 1
    if added:
        await db.flush()
    return added


async def get_all_tags_grouped(db: AsyncSession) -> dict[str, list[dict]]:
    """获取所有标签，按类别分组，并包含使用次数统计。"""
    result = await db.execute(
        select(
            Tag,
            func.count(InspirationTag.inspiration_id).label("usage_count"),
        )
        .outerjoin(InspirationTag, Tag.id == InspirationTag.tag_id)
        .group_by(Tag.id)
        .order_by(Tag.category, func.count(InspirationTag.inspiration_id).desc())
    )
    grouped: dict[str, list[dict]] = {}
    for row in result:
        tag, count = row[0], row[1]
        tag_dict = {
            "id": tag.id,
            "name": tag.name,
            "category": tag.category,
            "source": tag.source,
            "created_at": tag.created_at,
            "usage_count": count,
        }
        grouped.setdefault(tag.category, []).append(tag_dict)
    return grouped


async def get_or_create_tag(
    db: AsyncSession, name: str, category: str = "free", source: str = "manual"
) -> Tag:
    """按名称查找已有标签，不存在则创建新标签。

    处理并发竞态：两任务同时创建同一标签时，先 flush 的一方成功，
    后 flush 的一方触发 IntegrityError。捕获后回滚当前事务并重新查询。
    """
    name = name.strip()
    result = await db.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if not tag:
        tag = Tag(name=name, category=category, source=source)
        db.add(tag)
        try:
            await db.flush()
        except IntegrityError:
            # 并发场景下对方已先创建，回滚当前插入，重新查询
            await db.rollback()
            logger.debug(f"并发创建标签冲突: {name!r}，回退查询")
            result = await db.execute(select(Tag).where(Tag.name == name))
            tag = result.scalar_one_or_none()
            if not tag:
                # 极端情况：重查仍未找到，再试一次（小概率）
                tag = Tag(name=name, category=category, source=source)
                db.add(tag)
                await db.flush()
    return tag


async def find_similar_tags(
    db: AsyncSession, name: str, threshold: float = 0.75
) -> list[Tag]:
    """查找与给定名称相似的已有标签（用于去重建议）。"""
    result = await db.execute(select(Tag))
    all_tags = result.scalars().all()
    similar = []
    for tag in all_tags:
        sim = _similarity(name, tag.name)
        if sim >= threshold and sim < 1.0:
            similar.append(tag)
    return sorted(similar, key=lambda t: _similarity(name, t.name), reverse=True)


async def merge_tags(db: AsyncSession, source_id: int, target_id: int):
    """将源标签合并到目标标签：重新关联所有素材，删除源标签。"""
    # 查找源标签的所有关联
    result = await db.execute(
        select(InspirationTag).where(InspirationTag.tag_id == source_id)
    )
    links = result.scalars().all()

    for link in links:
        # 检查该素材是否已关联目标标签
        existing = await db.execute(
            select(InspirationTag).where(
                InspirationTag.inspiration_id == link.inspiration_id,
                InspirationTag.tag_id == target_id,
            )
        )
        if existing.scalar_one_or_none():
            # 重复关联 — 删除源标签的关联
            await db.delete(link)
        else:
            # 重定向到目标标签
            link.tag_id = target_id

    # 删除源标签
    source_tag = await db.get(Tag, source_id)
    if source_tag:
        await db.delete(source_tag)

    await db.flush()
