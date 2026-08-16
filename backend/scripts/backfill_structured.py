"""回填 AI 分析结构化数据（ai_extracted_tags / prompt_version / model_version）。

一次性迁移脚本：把存量 ai_analysis_log（分析类型、成功、有原始响应）的
raw_response 重新解析，写入结构化标签快照，并回填版本字段，使历史数据
也能参与「多版本对比与追溯」。

用法:
    python scripts/backfill_structured.py            # 预览将处理多少条
    python scripts/backfill_structured.py --apply    # 实际写入

说明:
    - 质量审核日志（quality_check）无 raw_response，无法回填 ai_quality_review，跳过
    - 快照只引用已存在的标签（不创建新标签）；标签名无法匹配时跳过
"""

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

# 将 backend 目录加入 sys.path（脚本独立运行于 scripts/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database import async_session, init_db
from app.models.inspiration import AIAnalysisLog, AIAnalysisTag
from app.models.tag import Tag
from app.services.ai_parser import parse_analysis_response
from app.services.ai_tag_saver import iter_extracted_tags
from app.services.model_prompt import get_model_prompt
from app.config import settings


def _prompt_version(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]


async def _scan_targets(db) -> list[AIAnalysisLog]:
    """找出需要回填的日志：分析类型、成功、有原始响应、尚无结构化快照。"""
    result = await db.execute(
        select(AIAnalysisLog)
        .where(
            func.coalesce(AIAnalysisLog.log_type, "analysis") == "analysis",
            AIAnalysisLog.error.is_(None),
            AIAnalysisLog.raw_response.isnot(None),
            AIAnalysisLog.raw_response != "",
            ~AIAnalysisLog.id.in_(
                select(AIAnalysisTag.log_id).distinct()
            ),
        )
        .order_by(AIAnalysisLog.id)
    )
    return list(result.scalars().all())


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入（默认仅预览）")
    args = parser.parse_args()

    await init_db()
    async with async_session() as db:
        logs = await _scan_targets(db)
        print(f"待回填日志: {len(logs)} 条")

        if not args.apply:
            print("预览模式：加 --apply 实际写入")
            return

        written = 0
        skipped = 0
        for log in logs:
            tags_data = parse_analysis_response(log.raw_response or "")
            names = list(dict.fromkeys(name for name, _, _ in iter_extracted_tags(tags_data)))
            if not names:
                skipped += 1
                continue

            tag_result = await db.execute(select(Tag.id, Tag.name).where(Tag.name.in_(names)))
            tag_ids = {name: tag_id for tag_id, name in tag_result.all()}
            if not tag_ids:
                skipped += 1
                continue

            # 回填版本字段
            if not log.prompt_version:
                prompt = get_model_prompt(log.model_name)
                log.prompt_version = _prompt_version(prompt)
            if not log.model_version:
                log.model_version = log.model_name

            conf_map = {name: conf for name, _, conf in iter_extracted_tags(tags_data)}
            for name, tag_id in tag_ids.items():
                db.add(
                    AIAnalysisTag(
                        log_id=log.id,
                        tag_id=tag_id,
                        confidence=conf_map.get(name, 0.8),
                    )
                )
            written += 1

        await db.commit()
        print(f"回填完成：写入 {written} 条日志的结构化快照，跳过 {skipped} 条（无可匹配标签）")


if __name__ == "__main__":
    asyncio.run(main())
