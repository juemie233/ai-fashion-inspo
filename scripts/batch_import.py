"""批量导入脚本：将本地文件夹中的图片/视频批量导入素材库并逐张完成 AI 分析。"""

import asyncio
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.database import async_session, init_db
from app.models.inspiration import Inspiration
from app.services.ai_service import analyze_image
from app.services.file_service import save_upload
from app.services.inspiration_service import find_duplicate_by_hash

# 支持的扩展名 → (media_type, content_type)
_EXT_MEDIA = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
    ".gif": ("image", "image/gif"),
    ".mp4": ("video", "video/mp4"),
}

# 哈希计算分块大小（1MB，与 file_service._CHUNK_SIZE 一致），流式读取避免整文件驻留内存
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass
class FakeUploadFile:
    """模拟 UploadFile：基于已打开的二进制文件句柄分块返回内容，供 save_upload 流式读取。

    注意：read 直接委托底层文件句柄（句柄自身维护读取偏移并尊重 size 参数），
    save_upload 的分块循环读到 b"" 即结束；句柄由调用方持有并在
    save_upload 返回后关闭。
    """

    filename: str
    content_type: str = "image/jpeg"
    _file: BinaryIO | None = None

    async def read(self, size: int = -1) -> bytes:
        if self._file is None:
            return b""
        return self._file.read(size)


async def import_folder(folder_path: str, source_type: str = "manual_upload"):
    """递归读取文件夹中的所有图片，导入数据库并逐张完成 AI 分析。"""
    await init_db()

    path = Path(folder_path)
    if not path.exists():
        print(f"文件夹不存在: {folder_path}")
        return

    files = [f for f in path.rglob("*") if f.suffix.lower() in _EXT_MEDIA]

    if not files:
        print("文件夹中未找到支持的图片/视频格式")
        return

    print(f"找到 {len(files)} 个文件，开始导入...")

    async with async_session() as db:
        imported = 0
        skipped_dup = 0
        failed = 0
        imported_items: list[tuple[str, str]] = []  # (inspiration_id, rel_path)

        for i, file_path in enumerate(files, 1):
            try:
                ext = file_path.suffix.lower()
                media_type, content_type = _EXT_MEDIA[ext]

                # 内容哈希去重：分块流式计算，避免整文件读入内存（幂等）
                hasher = hashlib.sha256()
                with open(file_path, "rb") as f:
                    while chunk := f.read(_HASH_CHUNK_SIZE):
                        hasher.update(chunk)
                content_hash = hasher.hexdigest()
                if await find_duplicate_by_hash(db, content_hash):
                    skipped_dup += 1
                    print(f"  [{i}/{len(files)}] {file_path.name} 已存在（内容重复），跳过")
                    continue

                # 保存文件（基于文件句柄流式分块读取，内部校验真实类型与大小上限）
                with open(file_path, "rb") as f:
                    fake_file = FakeUploadFile(
                        filename=file_path.name,
                        content_type=content_type,
                        _file=f,
                    )
                    rel_path, thumb_path = await save_upload(fake_file)

                inspiration = Inspiration(
                    source_type=source_type,
                    file_path=rel_path,
                    thumbnail_path=thumb_path,
                    content_hash=content_hash,
                    media_type=media_type,
                )
                db.add(inspiration)
                await db.flush()
                await db.refresh(inspiration)

                imported += 1
                imported_items.append((inspiration.id, rel_path))
                print(f"  [{i}/{len(files)}] {file_path.name} → {inspiration.id}")

            except Exception as e:
                failed += 1
                print(f"  [{i}/{len(files)}] {file_path.name} 失败: {e}")

        # 显式提交导入记录：确保即使 AI 分析失败/脚本中断，已导入素材也不丢失
        await db.commit()
        print(f"\n导入完成！导入 {imported} 个，跳过重复 {skipped_dup} 个，失败 {failed} 个")

        # 逐张执行 AI 分析（串行 await，保证全部完成后脚本才退出；单张失败不中断）
        if imported_items:
            print("开始 AI 分析（逐张执行，耗时较长）...")
            ok = 0
            for idx, (insp_id, rel_path) in enumerate(imported_items, 1):
                try:
                    if await analyze_image(db, insp_id, rel_path):
                        ok += 1
                    else:
                        print(f"  分析失败（已记录错误日志）: {rel_path}")
                except Exception as e:
                    print(f"  分析异常: {insp_id} — {e}")
                print(f"  [{idx}/{len(imported_items)}] 分析完成")
            print(f"AI 分析完成：成功 {ok}/{len(imported_items)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: py scripts/batch_import.py <文件夹路径> [来源类型]")
        print("示例: py scripts/batch_import.py D:/穿搭图库 manual_upload")
        sys.exit(1)

    folder = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else "manual_upload"
    asyncio.run(import_folder(folder, source))
