"""独立脚本：为存量素材批量生成文本/图像向量（向量回填）。

用法（在 backend 目录下，或任意目录执行均可）：
    python scripts/backfill_vectors.py --mode all
    python scripts/backfill_vectors.py --mode text
    python scripts/backfill_vectors.py --mode image --limit 100
    python scripts/backfill_vectors.py --incremental   # 只回填缺失向量

参数:
    --mode         all | text | image（默认 all）
    --limit        处理条数上限，0 表示全部（默认 0）
    --incremental  增量模式：跳过「已存在向量」的素材，只回填缺失向量

说明:
    - 文本向量：基于素材标签拼接文本，走 Ollama all-minilm 生成 384 维向量
    - 图像向量：基于素材图片文件，走 CLIP clip-ViT-B/32 生成 512 维向量
      （需已安装 sentence-transformers，未安装时该部分自动跳过）
    - 同步执行，素材量大时耗时较长，建议在闲置时运行
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径（与 scripts/seed_tags.py 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.database import async_session, init_db  # noqa: E402
from app.services.vector_service import backfill_all_vectors  # noqa: E402


async def main() -> None:
    """执行向量回填。"""
    parser = argparse.ArgumentParser(description="存量素材向量回填")
    parser.add_argument("--mode", default="all", choices=["all", "text", "image"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--incremental", action="store_true",
        help="增量模式：跳过已存在向量的素材，只回填缺失向量",
    )
    args = parser.parse_args()

    print("正在初始化数据库...")
    await init_db()

    async with async_session() as db:
        stats = await backfill_all_vectors(
            db, mode=args.mode, limit=args.limit, incremental=args.incremental
        )

    if "error" in stats:
        print(f"[错误] {stats['error']}")
        sys.exit(1)

    print(f"回填完成（mode={args.mode}, limit={args.limit}, incremental={args.incremental}）:")
    for key in ("processed", "text_added", "text_failed", "text_skipped",
                "image_added", "image_failed", "image_skipped", "skipped_non_image"):
        print(f"  {key}: {stats.get(key, 0)}")


if __name__ == "__main__":
    asyncio.run(main())
