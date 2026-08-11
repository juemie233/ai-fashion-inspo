"""批量导入脚本：将本地文件夹中的图片批量导入素材库并触发 AI 分析。"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.database import async_session, init_db
from app.services.file_service import save_upload
from app.services.ai_service import analyze_image
from app.models.inspiration import Inspiration

# 模拟 UploadFile 用于 save_upload
from dataclasses import dataclass


@dataclass
class FakeUploadFile:
    filename: str
    content_type: str = "image/jpeg"
    _content: bytes = b""

    async def read(self) -> bytes:
        return self._content


async def import_folder(folder_path: str, source_type: str = "manual_upload"):
    """递归读取文件夹中的所有图片，导入数据库。"""
    await init_db()

    path = Path(folder_path)
    if not path.exists():
        print(f"文件夹不存在: {folder_path}")
        return

    # 支持的图片格式
    extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"}
    files = [f for f in path.rglob("*") if f.suffix.lower() in extensions]

    if not files:
        print(f"文件夹中未找到支持的图片格式")
        return

    print(f"找到 {len(files)} 个文件，开始导入...")

    async with async_session() as db:
        for i, file_path in enumerate(files, 1):
            try:
                # 读取文件内容
                with open(file_path, "rb") as f:
                    content = f.read()

                fake_file = FakeUploadFile(
                    filename=file_path.name,
                    _content=content,
                )

                # 保存文件
                rel_path, thumb_path = await save_upload(fake_file)

                # 创建数据库记录
                inspiration = Inspiration(
                    source_type=source_type,
                    file_path=rel_path,
                    thumbnail_path=thumb_path,
                    media_type="image",
                )
                db.add(inspiration)
                await db.flush()
                await db.refresh(inspiration)

                print(f"  [{i}/{len(files)}] {file_path.name} → {inspiration.id}")

                # 异步触发 AI 分析（非阻塞）
                asyncio.create_task(analyze_image(db, inspiration.id, rel_path))

            except Exception as e:
                print(f"  [{i}/{len(files)}] {file_path.name} 失败: {e}")

        print(f"\n导入完成！共导入 {len(files)} 个文件")
        print("AI 分析正在后台进行中...")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: py scripts/batch_import.py <文件夹路径> [来源类型]")
        print("示例: py scripts/batch_import.py D:/穿搭图库 manual_upload")
        sys.exit(1)

    folder = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else "manual_upload"
    asyncio.run(import_folder(folder, source))
