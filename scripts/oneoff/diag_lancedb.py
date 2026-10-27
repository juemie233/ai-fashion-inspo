"""一次性诊断：LanceDB 向量库实际向量数 vs 素材数（只读）。"""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{BACKEND / 'fashion_inspo.db'}")
os.environ.setdefault("STORAGE_ROOT", str(BACKEND / "storage"))

from app.config import settings  # noqa: E402
from app.services.vector import store as vector_store  # noqa: E402


def main() -> None:
    import asyncio

    print("lancedb 目录:", settings.lancedb_dir)
    if not settings.lancedb_dir.exists():
        # 自定义存储根（如 G:/fashion-inspo-storage）时，默认值会指到 backend/storage；
        # 直接给可操作的提示，省得误判成「向量库没建」
        print(
            "  （目录不存在：若项目用了自定义存储根，请先设置环境变量 "
            "STORAGE_ROOT=<存储根> 再跑本脚本）"
        )
        print("目录不存在")
        return
    # 目录内容
    for entry in sorted(settings.lancedb_dir.iterdir()):
        if entry.is_dir():
            n = sum(1 for _ in entry.rglob("*"))
            print(f"  目录 {entry.name}/: {n} 个文件")
        else:
            print(f"  文件 {entry.name}: {entry.stat().st_size} bytes")

    if not vector_store.is_lancedb_available():
        print("\nlancedb 未安装，无法统计向量数")
        return

    async def _counts():
        return (
            await vector_store.count_vectors("text"),
            await vector_store.count_vectors("image"),
        )

    text_n, image_n = asyncio.run(_counts())
    print(f"\n文本向量表条数: {text_n}")
    print(f"图像向量表条数: {image_n}")


if __name__ == "__main__":
    main()
