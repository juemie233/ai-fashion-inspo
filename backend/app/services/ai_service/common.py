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

    # 图片预检：通过扩展名做白名单校验（防路径/类型误用）
    ext = full_path.suffix.lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise ValueError(f"不支持的图片格式: {ext}，支持: {', '.join(sorted(_ALLOWED_IMG_EXT))}")

    import base64
    image_bytes = full_path.read_bytes()

    # 按「实际内容格式」而非扩展名判断是否需要转换：
    # 小红书等来源常把 WebP 内容存成 .jpg 扩展名，仅按扩展名判断会漏掉这类文件，
    # 导致 WebP 字节直接交给 Ollama 而报 "Failed to load image or audio file"（审核/分析整批失败）。
    # 用 PIL 读文件头识别真实格式，凡 Ollama 无法解码的格式（WebP/BMP/GIF）统一转 JPEG。
    from io import BytesIO
    from PIL import Image

    try:
        with Image.open(BytesIO(image_bytes)) as im:
            actual_format = (im.format or "").upper()
            if actual_format in {"WEBP", "BMP", "GIF"}:
                buf = BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=95)
                image_bytes = buf.getvalue()
                logger.info(
                    f"实际格式 {actual_format}（扩展名 {ext}）→ JPEG 转换完成 ({full_path.name})"
                )
    except Exception as e:
        raise ValueError(f"图片解析失败（无法识别格式）: {e}。文件可能已损坏。") from e

    image_data = base64.b64encode(image_bytes).decode("utf-8")
    file_size_mb = full_path.stat().st_size / (1024 * 1024)
    return image_data, file_size_mb
