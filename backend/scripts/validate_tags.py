"""标签质量检查与清理脚本。

运行方式:
  cd backend
  python scripts/validate_tags.py              # 仅检查，列出问题标签
  python scripts/validate_tags.py --fix        # 检查并自动修复可修复的标签
  python scripts/validate_tags.py --fix --force # 修复 + 删除无法修复的标签

检查规则:
  1. 不含中文 → 无效
  2. 过长 (>8字) → 可能是描述文本
  3. 含句号/感叹号 → 描述句
  4. 含描述词汇 (这是一/图片中/背景为等) → 描述句
  5. 纯英文 → 无效
  6. hex 颜色值 → 应删除
  7. 含逗号/顿号 → 可拆分
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, func
from app.database import async_session
from app.models.tag import Tag, InspirationTag
from app.utils.tag_normalizer import validate_tag_name


def split_compound_name(name: str) -> list[str]:
    """尝试拆分复合标签名（逗号/顿号/空格分隔）。"""
    parts = []
    # 按逗号拆分
    if '，' in name or ',' in name:
        parts = [p.strip() for p in name.replace('，', ',').split(',') if p.strip()]
    # 按顿号拆分
    elif '、' in name:
        parts = [p.strip() for p in name.split('、') if p.strip()]
    else:
        return []

    # 验证每个部分
    valid = []
    for p in parts:
        ok, _ = validate_tag_name(p)
        if ok:
            valid.append(p)
    return valid


async def check_tags():
    """检查所有标签，返回问题列表。"""
    async with async_session() as db:
        result = await db.execute(select(Tag).order_by(Tag.category, Tag.name))
        all_tags = result.scalars().all()

        issues = []
        for tag in all_tags:
            ok, reason = validate_tag_name(tag.name)
            if ok:
                continue

            fixable = False
            split_names = []
            if any(c in tag.name for c in ('，', ',', '、')):
                split_names = split_compound_name(tag.name)
                if split_names:
                    fixable = True

            issues.append({
                "tag": tag,
                "reason": reason,
                "fixable": fixable,
                "split_names": split_names,
            })

        return issues


async def fix_tags(issues: list[dict], force_delete: bool = False):
    """修复问题标签：拆分或删除。"""
    async with async_session() as db:
        fixed = 0
        deleted = 0
        from app.services.tag_service import get_or_create_tag

        for item in issues:
            tag = item["tag"]

            if item["fixable"]:
                # 可拆分：为每个拆分出的名称创建新标签
                links = (await db.execute(
                    select(InspirationTag).where(InspirationTag.tag_id == tag.id)
                )).scalars().all()

                for clean_name in item["split_names"]:
                    new_tag = await get_or_create_tag(db, clean_name, tag.category, "manual")
                    for link in links:
                        existing = (await db.execute(
                            select(InspirationTag).where(
                                InspirationTag.inspiration_id == link.inspiration_id,
                                InspirationTag.tag_id == new_tag.id,
                            )
                        )).scalar_one_or_none()
                        if not existing:
                            db.add(InspirationTag(
                                inspiration_id=link.inspiration_id,
                                tag_id=new_tag.id,
                                confidence=link.confidence,
                            ))

                # 删除原标签
                for link in links:
                    await db.delete(link)
                await db.delete(tag)
                print(f"  [拆分] {tag.name!r} → {item['split_names']}")
                fixed += 1

            elif force_delete:
                # 无法修复，直接删除
                links = (await db.execute(
                    select(InspirationTag).where(InspirationTag.tag_id == tag.id)
                )).scalars().all()
                for link in links:
                    await db.delete(link)
                await db.delete(tag)
                print(f"  [删除] {tag.name!r} ({item['reason']})")
                deleted += 1
            else:
                print(f"  [跳过] {tag.name!r} ({item['reason']})")

        await db.commit()
        return fixed, deleted


async def main():
    parser = argparse.ArgumentParser(description="标签质量检查与清理")
    parser.add_argument("--fix", action="store_true", help="自动修复可拆分的标签")
    parser.add_argument("--force", action="store_true", help="删除无法修复的问题标签")
    args = parser.parse_args()

    issues = await check_tags()

    total = 0
    async with async_session() as db:
        total = (await db.execute(select(func.count()).select_from(Tag))).scalar()

    print(f"总标签数: {total}")
    print(f"问题标签: {len(issues)}")
    print()

    if not issues:
        print("✅ 所有标签通过检查")
        return

    by_reason = {}
    for item in issues:
        by_reason.setdefault(item["reason"].split(':')[0], []).append(item)

    for reason, items in sorted(by_reason.items()):
        print(f"--- {reason} ({len(items)} 个) ---")
        for item in items:
            tag = item["tag"]
            fix_mark = " [可拆分]" if item["fixable"] else ""
            print(f"  [{tag.category}] {tag.name!r} (id={tag.id}, 使用{len(items)}次){fix_mark}")
        print()

    fixable_count = sum(1 for i in issues if i["fixable"])
    unfixable_count = len(issues) - fixable_count

    if args.fix:
        print(f"开始修复: {fixable_count} 可拆分, {unfixable_count} 需删除")
        fixed, deleted = await fix_tags(issues, force_delete=args.force)
        print(f"\n结果: 修复 {fixed} 个, 删除 {deleted} 个")
    else:
        print(f"可自动修复: {fixable_count} 个, 需手动处理: {unfixable_count} 个")
        print("运行 --fix 自动修复, --fix --force 同时删除不可修复的标签")


if __name__ == "__main__":
    asyncio.run(main())
