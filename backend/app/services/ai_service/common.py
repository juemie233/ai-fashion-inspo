"""AI 服务子模块共用辅助。

- 图片读取与 base64 转换（含路径安全校验、格式转换、体积统计）
- 共享 logger（沿用原 app.services.ai_service 名称，保证日志输出与拆分前一致）
"""

import logging

from app.config import settings

# 支持的图片扩展名
_ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}

# 沿用原模块 logger 名称，保证日志输出与拆分前一致
logger = logging.getLogger("app.services.ai_service")


def _read_image_base64(file_path: str) -> tuple[str, float]:
    """读取图片并转为 base64（含路径校验和格式转换）。

    返回:
        (base64 字符串, 文件大小 MB)
    """
    storage_root = settings.storage_root.resolve()
    full_path = (storage_root / file_path).resolve()
    # 防御路径遍历攻击（按路径组件判定，Windows 下大小写不敏感）
    try:
        full_path.relative_to(storage_root)
    except ValueError:
        raise ValueError(f"非法的文件路径: {file_path}")
    if not full_path.exists():
        raise FileNotFoundError(f"图片不存在: {full_path}")
    if not full_path.is_file():
        raise ValueError(f"路径不是文件: {file_path}")

    # 图片预检：通过扩展名判断
    ext = full_path.suffix.lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise ValueError(f"不支持的图片格式: {ext}，支持: {', '.join(sorted(_ALLOWED_IMG_EXT))}")

    # 读取图片 —— WebP/BMP/GIF 统一转为 JPEG
    # 实测 qwen3-vl:8b-instruct 在 Ollama 下无法解码 WebP（报 "Failed to load image or audio file"），
    # JPEG 是所有视觉模型通用支持的格式，因此无论模型一律转换，保证兼容性。
    import base64
    image_bytes = full_path.read_bytes()
    if ext in {".webp", ".bmp", ".gif"}:
        try:
            from io import BytesIO
            from PIL import Image
            buf = BytesIO()
            Image.open(BytesIO(image_bytes)).convert("RGB").save(buf, "JPEG", quality=95)
            image_bytes = buf.getvalue()
            logger.info(f"{ext} → JPEG 转换完成 ({full_path.name})")
        except Exception as e:
            raise ValueError(f"{ext} 图片转换 JPEG 失败: {e}。文件可能已损坏。") from e
    image_data = base64.b64encode(image_bytes).decode("utf-8")
    file_size_mb = full_path.stat().st_size / (1024 * 1024)
    return image_data, file_size_mb
