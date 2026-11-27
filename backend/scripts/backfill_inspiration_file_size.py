"""回填 ``inspirations.file_size``（largest 排序不再全库 stat）。

默认只读预览；``--apply`` 才写库。只处理未删除且 ``file_size IS NULL`` 的行，
每批提交；文件缺失按 0 记录（视为已检查，避免反复 stat）。

用法：
    cd backend
    python -m scripts.backfill_inspiration_file_size               # 预览
    python -m scripts.backfill_inspiration_file_size --apply       # 写库
    python -m scripts.backfill_inspiration_file_size --apply --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select, update

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.database import async_session  # noqa: E402
from app.models.inspiration import Inspiration  # noqa: E402

logger = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填素材文件字节数 file_size")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只预览）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少行（0=全部）")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批提交行数"
    )
    return parser.parse_args()


def _stat_size(rel_path: str) -> int:
    """返回文件字节数；文件缺失/无权限按 0。"""
    try:
        return (settings.storage_root / rel_path).stat().st_size
    except OSError:
        return 0


async def main(args: argparse.Namespace) -> int:
    """扫描缺失 file_size 的素材并（可选）回填。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    processed = 0
    total = 0
    updated = 0
    async with async_session() as db:
        query = (
            select(Inspiration.id, Inspiration.file_path)
            .where(
                Inspiration.deleted_at.is_(None),
                Inspiration.file_size.is_(None),
            )
            .order_by(Inspiration.id)
        )
        if args.limit > 0:
            query = query.limit(args.limit)
        rows = (await db.execute(query)).all()
        total = len(rows)
        logger.info("待处理 %s 行（apply=%s）", total, args.apply)
        for start in range(0, total, max(1, args.batch_size)):
            batch = rows[start : start + max(1, args.batch_size)]
            sizes = await asyncio.gather(
                *(asyncio.to_thread(_stat_size, row.file_path) for row in batch)
            )
            if args.apply:
                for row, size in zip(batch, sizes, strict=True):
                    await db.execute(
                        update(Inspiration)
                        .where(
                            Inspiration.id == row.id,
                            Inspiration.file_size.is_(None),
                        )
                        .values(file_size=size)
                    )
                await db.commit()
            updated += len(batch)
            processed += len(batch)
            logger.info("进度 %s/%s", processed, total)
    logger.info("完成：扫描 %s，%s %s", total, "写入" if args.apply else "预览", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
