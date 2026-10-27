"""为历史 rejected 素材回填 `quality_reason_code`（一次性脚本，可重复执行）。

背景：`quality_reason_code` 是 2026-10 新增字段，此前的 rejected 素材只有自由文本原因，
导致「按原因筛选 / 灰区复审 / prompt A/B」三项功能没有数据。本脚本把已有文本翻译成枚举，
**不改变任何判定**（不重跑模型）。

用法：
    cd backend
    python scripts/oneoff/backfill_quality_reason_code.py            # 预览（默认 dry-run）
    python scripts/oneoff/backfill_quality_reason_code.py --apply    # 真正写入

输出：按原因码统计的分布 + 无法归类（落到 `other`）的样本文本，便于调整规则。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from pathlib import Path

# 允许 `python scripts/oneoff/xxx.py` 直接跑（把 backend 加入 sys.path）
# parents[2] = 仓库根（scripts/oneoff/xxx.py → oneoff → scripts → 根）
BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402  (需先插 sys.path)

from app.database import async_session  # noqa: E402
from app.models.inspiration import Inspiration  # noqa: E402
from app.services.quality_reason_codes import FALLBACK_CODE, classify_quality_reason  # noqa: E402


async def run(apply: bool) -> int:
    """扫描并（可选）写入原因码。

    Args:
        apply: True 时写库；False 只统计预览

    Returns:
        进程退出码（0 成功）
    """
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Inspiration.id, Inspiration.quality_reason).where(
                    Inspiration.quality_status == "rejected",
                    Inspiration.quality_reason_code.is_(None),
                )
            )
        ).all()

        print(f"待回填的 rejected 素材：{len(rows)} 条")
        if not rows:
            print("无需回填。")
            return 0

        counter: collections.Counter[str] = collections.Counter()
        samples: dict[str, list[str]] = collections.defaultdict(list)
        updates: list[tuple[str, str]] = []
        for insp_id, reason in rows:
            code = classify_quality_reason(reason)
            counter[code] += 1
            if len(samples[code]) < 3:
                samples[code].append((reason or "（无原因文本）")[:60])
            updates.append((insp_id, code))

        print("\n按原因码分布：")
        for code, count in counter.most_common():
            print(f"  {count:5d}  {code}")
            for text in samples[code]:
                print(f"         例：{text}")

        if counter.get(FALLBACK_CODE):
            print(
                f"\n其中 {counter[FALLBACK_CODE]} 条落到 `{FALLBACK_CODE}`（无法归类）："
                "如需更细，请扩充 app/services/quality_reason_codes.py 的关键词表。"
            )

        if not apply:
            print("\n[dry-run] 未写库。确认分布合理后加 --apply 执行。")
            return 0

        from sqlalchemy import update

        for insp_id, code in updates:
            await db.execute(
                update(Inspiration)
                .where(Inspiration.id == insp_id)
                .values(quality_reason_code=code)
            )
        await db.commit()
        print(f"\n[apply] 已写入 {len(updates)} 条。")
        return 0


def main() -> int:
    """解析参数并执行。"""
    parser = argparse.ArgumentParser(description="回填历史 rejected 素材的 quality_reason_code")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    args = parser.parse_args()
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
