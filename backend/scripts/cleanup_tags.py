"""数据库标签清洗脚本：清理 AI 解析产生的脏标签。

运行方式: cd backend && python scripts/cleanup_tags.py [--dry-run]

问题类型：
1. JSON 对象被 str() 存为标签名
2. 颜色 hex 值 (#000, #0000FF) 混入标签
3. 被截断的碎片标签（如 "修"、"身"）
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# 添加后端路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, delete, func
from app.database import async_session, engine, init_db
from app.db_migrations import ensure_schema
from app.models.tag import Tag, InspirationTag
from app.services.ai_parser import extract_tag_names
from app.services.ai_tag_saver import normalize_color


def is_json_like(name: str) -> bool:
    """检查是否是 JSON/dict 字符串表示的标签。"""
    s = name.strip()
    if s.startswith("{") and s.endswith("}"):
        return True
    if s.startswith("[") and s.endswith("]"):
        return True
    # 包含冒号键值对的特征
    if ": " in s and ("'" in s or '"' in s):
        return True
    return False


def is_hex_color(name: str) -> bool:
    """检查是否是 hex 颜色值。"""
    s = name.strip()
    if re.match(r'^#?[0-9A-Fa-f]{3,8}$', s):
        return True
    return False


def is_garbage(name: str) -> bool:
    """检查是否是垃圾标签（过短、无意义）。"""
    s = name.strip()
    if len(s) <= 1 and not s.isalpha():
        return True
    # 被截断的中文碎片
    if s in ("修", "身", "风", "款", "色", "系", "裙", "袖", "领"):
        return True
    return False


def extract_clean_name(bad_name: str) -> list[str]:
    """尝试从脏标签名中提取干净的标签。"""
    # 先尝试用 _extract_tag_names
    try:
        # 尝试解析为 JSON
        if bad_name.startswith("{") and bad_name.endswith("}"):
            # Python repr 的 dict 字符串, 尝试替换为 JSON
            json_str = bad_name.replace("'", '"')
            try:
                obj = json.loads(json_str)
                results = extract_tag_names(obj)
                return _filter_valid_tags(results)
            except json.JSONDecodeError:
                pass

        # 尝试直接用 ast.literal_eval 解析 Python repr
        try:
            import ast
            obj = ast.literal_eval(bad_name)
            results = extract_tag_names(obj)
            return _filter_valid_tags(results)
        except (ValueError, SyntaxError):
            pass
    except Exception:
        pass

    # 最后尝试正则提取中文/英文词
    results = []
    cn_words = re.findall(r'[一-鿿]{2,4}', bad_name)
    results.extend(cn_words)
    en_words = re.findall(r'[A-Za-z]{2,}', bad_name)
    results.extend(en_words)
    return _filter_valid_tags(results)


def _filter_valid_tags(names: list[str]) -> list[str]:
    """过滤掉不适合作为标签的值。"""
    filtered = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        # 跳过过长的（很可能是描述文本）
        if len(name) > 10:
            continue
        # 跳过包含逗号、句号的（描述文本）
        if '，' in name or '。' in name or ',' in name:
            continue
        # 斜杠分隔的值拆分为多个
        if '/' in name and not name.startswith('#'):
            parts = [p.strip() for p in name.split('/') if p.strip()]
            filtered.extend(_filter_valid_tags(parts))
            continue
        # 跳过纯数字
        if name.isdigit():
            continue
        filtered.append(name)
    return filtered


async def cleanup(dry_run: bool = True):
    """执行标签清洗。"""
    async with async_session() as db:
        all_tags = (await db.execute(select(Tag))).scalars().all()
        print(f"总标签数: {len(all_tags)}")

        bad_tags = []
        for tag in all_tags:
            issue = None
            clean_names = []

            if is_json_like(tag.name):
                issue = "JSON-like"
                clean_names = extract_clean_name(tag.name)
            elif is_hex_color(tag.name):
                issue = "hex-color"
                cn = normalize_color(tag.name)
                if cn:
                    clean_names = [cn]
            elif is_garbage(tag.name):
                issue = "garbage"
                clean_names = []

            if issue:
                bad_tags.append({
                    "tag": tag,
                    "issue": issue,
                    "clean_names": clean_names,
                })

        print(f"脏标签数: {len(bad_tags)}")
        print()

        if not bad_tags:
            print("没有需要清洗的标签。")
            return

        # 统计受影响的关联
        from app.services.tag_service import get_or_create_tag

        affected_inspirations = set()
        fixed_count = 0
        deleted_count = 0
        garbaged_count = 0

        for item in bad_tags:
            bad_tag = item["tag"]
            clean_names = item["clean_names"]
            issue = item["issue"]

            # 查找使用此标签的素材关联
            links = (await db.execute(
                select(InspirationTag).where(InspirationTag.tag_id == bad_tag.id)
            )).scalars().all()

            print(f"[{issue}] {bad_tag.name!r} (id={bad_tag.id}, category={bad_tag.category})"
                  f" -> {clean_names} ({len(links)} 关联)")

            if dry_run:
                continue

            for link in links:
                affected_inspirations.add(link.inspiration_id)

            if clean_names:
                # 有可提取的干净名称 → 迁移关联到正确标签
                for clean_name in clean_names:
                    if not clean_name:
                        continue
                    correct_tag = await get_or_create_tag(
                        db, clean_name, bad_tag.category
                    )
                    for link in links:
                        await _link_or_update(db, link.inspiration_id, correct_tag.id, link.confidence)
                fixed_count += len(links)
            else:
                garbaged_count += len(links)

            # 删除脏标签（先删关联，再删标签）
            for link in links:
                await db.delete(link)
            await db.delete(bad_tag)
            deleted_count += 1

        if dry_run:
            print(f"\n[DRY RUN] 将修复 {fixed_count} 个关联, "
                  f"删除 {deleted_count} 个脏标签, "
                  f"丢弃 {garbaged_count} 个无意义关联。")
            print("加上 --apply 参数执行实际清洗。")
        else:
            await db.commit()
            print(f"\n已修复 {fixed_count} 个关联, "
                  f"删除 {deleted_count} 个脏标签, "
                  f"丢弃 {garbaged_count} 个无意义关联。")
            print(f"受影响素材: {len(affected_inspirations)} 个")


async def _link_or_update(db, inspiration_id: str, tag_id: int, confidence: float):
    """关联或更新标签，避免重复（与 ai_service._link_tag 相同逻辑）。"""
    existing = (await db.execute(
        select(InspirationTag).where(
            InspirationTag.inspiration_id == inspiration_id,
            InspirationTag.tag_id == tag_id,
        )
    )).scalar_one_or_none()
    if existing:
        if confidence > existing.confidence:
            existing.confidence = confidence
    else:
        db.add(InspirationTag(
            inspiration_id=inspiration_id,
            tag_id=tag_id,
            confidence=confidence,
        ))


async def main():
    parser = argparse.ArgumentParser(description="清洗 AI 产生的脏标签")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=== DRY RUN 模式（不会修改数据）===\n")

    # 确保表结构与字段最新（独立脚本不经过服务端 lifespan）
    await init_db()
    await ensure_schema()

    await cleanup(dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
