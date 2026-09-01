"""向量编码模块：生成文本向量与图像向量，供语义搜索、以图搜图使用。

两条链路：
1. 文本向量：通过 Ollama /api/embeddings 使用 all-minilm 模型生成（384 维）。
2. 图像向量：优先使用本地 CLIP 模型（sentence-transformers 的 clip-ViT-B-32，
   512 维）。CLIP 依赖 torch / sentence-transformers 较重，项目默认不强制安装；
   当依赖缺失或模型未下载时，图像向量能力自动降级（返回 None / 明确的状态说明），
   不会导致其他功能崩溃。

安装方式（需用户手动执行，见 README / TODO）：
    pip install sentence-transformers
    # 首次使用时自动下载 clip-ViT-B-32 模型（约 600MB，需科学上网或离线放置）

说明：相似度计算（``cosine_similarity``）与标签兜底检索（``find_similar_images``）
已迁移至本包的 ``similarity`` 模块。
"""

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import httpx

from app.config import settings
from app.models.inspiration import Inspiration

logger = logging.getLogger(__name__)

# 文本向量维度（取决于 Ollama embedding 模型）
TEXT_EMBEDDING_DIM = settings.lancedb_text_dim
# 图像向量维度（取决于 CLIP 模型）
IMAGE_EMBEDDING_DIM = settings.lancedb_image_dim

# CLIP 模型缓存与加载错误状态（模块级单例，避免重复加载）
_image_model = None
_image_model_error: str | None = None
# 加载失败的时间戳：失败被缓存，但带时效——超过 _CLIP_RETRY_AFTER_SECONDS
# 后允许重新尝试加载。否则一次临时性失败（如 CUDA 显存被其它进程占满、
# 模型文件读取抖动）会让整个进程后续所有图像向量永久失败，批量回填任务
# 整体被标记失败，用户视角即为「新素材向量几乎全部缺失」。
_image_model_error_at: float | None = None
# CLIP 模型懒加载锁：_encode_image_sync 在 asyncio.to_thread 线程池中调用，
# 并发首屏可能重复加载（模型约 600MB），加锁 + 双重检查保证只初始化一次。
_clip_model_lock = threading.Lock()
# 加载失败后的重试间隔（秒）：模型加载是重操作（约 600MB），失败后不宜
# 每次调用都重试；间隔过后才清除错误重新尝试，兼顾自愈与开销。
_CLIP_RETRY_AFTER_SECONDS = 60

# 文本向量缓存（进程内，按 query 文本缓存，避免相同文本重复调用 Ollama）。
# generate_text_embedding 为 async 函数、仅事件循环内调用，无跨线程竞争。
_text_cache: dict[str, list[float]] = {}
_TEXT_CACHE_MAX = 500

# 图像向量缓存（按图片文件路径缓存；_encode_image_sync 在线程池运行，需加锁）
_image_cache: dict[str, list[float]] = {}
_IMAGE_CACHE_MAX = 300
_image_cache_lock = threading.Lock()


def _cache_text_embedding(text: str, vec: list[float]) -> None:
    """写入文本向量缓存，超过上限时淘汰最旧条目。"""
    if len(_text_cache) >= _TEXT_CACHE_MAX:
        try:
            _text_cache.pop(next(iter(_text_cache)))
        except StopIteration:
            pass
    _text_cache[text] = list(vec)


def _get_cached_image_embedding(file_path: str) -> list[float] | None:
    """读取图像向量缓存（命中时返回副本，避免调用方修改缓存内容）。"""
    with _image_cache_lock:
        cached = _image_cache.get(file_path)
        if cached is None:
            return None
        return list(cached)


def _cache_image_embedding(file_path: str, vec: list[float]) -> None:
    """写入图像向量缓存，超过上限时淘汰最旧条目。"""
    with _image_cache_lock:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            try:
                _image_cache.pop(next(iter(_image_cache)))
            except StopIteration:
                pass
        _image_cache[file_path] = list(vec)


# ==================== 文本向量（Ollama all-minilm） ====================


async def generate_text_embedding(text: str) -> list[float] | None:
    """通过 Ollama embedding 模型生成文本向量（带进程内缓存）。

    Ollama 的 all-minilm 等模型有 context 上限，超长文本会返回 HTTP 500
    （"the input length exceeds the context length"），导致「文本太长 →
    嵌入失败 → 向量永久缺失」。本函数按 1024/512/256 字符逐级截断重试，
    保证长文本素材也能得到向量（语义损失可接受，优于直接缺失）。

    参数:
        text: 待嵌入的文本

    返回:
        向量列表；Ollama 未启动、模型缺失或调用失败时返回 None
    """
    text = (text or "").strip()
    if not text:
        return None
    cached = _text_cache.get(text)
    if cached is not None:
        return list(cached)
    # 逐级截断重试：None=全文，超长报 context 错误时依次减半
    for limit in (None, 1024, 512, 256):
        payload = text if limit is None else text[:limit]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/embeddings",
                    json={
                        "model": settings.ollama_embedding_model,
                        "prompt": payload,
                    },
                )
                if resp.status_code == 200:
                    embedding = resp.json().get("embedding")
                    if not embedding:
                        logger.warning(f"文本嵌入返回空结果: {payload[:50]}")
                        return None
                    result = list(embedding)
                    _cache_text_embedding(text, result)
                    return result
                body = resp.text or ""
                if "context length" in body.lower():
                    logger.warning(
                        f"文本嵌入超长（{len(text)} 字符），截断到 {limit} 字符重试: "
                        f"{body[:120]}"
                    )
                    continue  # 下一轮截断重试
                logger.error(
                    f"文本嵌入模型失败 (HTTP {resp.status_code}): {body[:200]}"
                )
                return None
        except Exception as e:
            logger.error(f"生成文本向量失败: {e}")
            return None
    return None


def get_text_embedding_status() -> dict:
    """返回文本向量能力状态（供 /api/search/vector/status 使用）。"""
    return {
        "model": settings.ollama_embedding_model,
        "dim": TEXT_EMBEDDING_DIM,
        "note": "使用 Ollama /api/embeddings 生成文本向量，需已安装 all-minilm 模型",
    }


# ==================== 图像向量（CLIP） ====================


def _check_clip_dependency() -> str | None:
    """检测 CLIP 依赖是否可用，返回错误原因（可用时返回 None）。"""
    try:
        import torch  # noqa: F401
        import sentence_transformers  # noqa: F401
        return None
    except ImportError as e:
        return (
            "图像向量不可用：缺少 CLIP 依赖（sentence-transformers / torch）。"
            f"请手动安装：pip install sentence-transformers。({e})"
        )


def get_image_embedding_status() -> dict:
    """返回图像向量能力状态（CLIP 是否可用，供前端提示与 /vector/status 使用）。"""
    reason = _check_clip_dependency()
    available = reason is None
    return {
        "available": available,
        "model": settings.clip_model_name,
        "dim": IMAGE_EMBEDDING_DIM,
        "reason": reason or "图像向量可用（CLIP 已安装）",
    }


def _load_clip_model() -> object | None:
    """懒加载 CLIP 模型（仅在调用图像向量时加载一次，线程安全）。

    返回:
        模型对象；依赖缺失或加载失败时返回 None（并记录错误原因）。
        失败为「带时效缓存」：超过 _CLIP_RETRY_AFTER_SECONDS 后清除错误
        重新尝试，临时性故障（CUDA 显存被占等）可自动恢复，而非永久降级。
    """
    global _image_model, _image_model_error, _image_model_error_at
    if _image_model is not None:
        return _image_model
    if _image_model_error is not None:
        if _image_model_error_at is None or time.monotonic() - _image_model_error_at < _CLIP_RETRY_AFTER_SECONDS:
            return None
        # 重试窗口已过：清除错误，本调用重新尝试加载
        _image_model_error = None
        _image_model_error_at = None

    with _clip_model_lock:
        # 双重检查：避免线程池并发首屏重复加载（CLIP 模型约 600MB）
        if _image_model is not None:
            return _image_model
        if _image_model_error is not None and (
            _image_model_error_at is None or time.monotonic() - _image_model_error_at < _CLIP_RETRY_AFTER_SECONDS
        ):
            return None
        if _image_model_error is not None:
            # 另一线程刚清除但尚未成功：本次直接尝试（不提前返回）
            _image_model_error = None
            _image_model_error_at = None

        reason = _check_clip_dependency()
        if reason:
            _image_model_error = reason
            _image_model_error_at = time.monotonic()
            logger.warning(reason)
            return None

        try:
            from sentence_transformers import SentenceTransformer

            # local_files_only=True：仅从本地缓存加载，禁止联网检查/下载。
            # 默认行为会向 huggingface 发 HEAD 请求校验版本，无网络时按重试
            # 退避阻塞数分钟，导致详情页「相似推荐」请求卡死。本地已缓存时直接
            # 加载（毫秒级），未缓存时快速失败并降级到标签匹配兜底。
            logger.info(f"正在加载 CLIP 图像模型: {settings.clip_model_name}")
            # device="cuda"：CLIP 图像编码走 GPU（RTX 5060 Ti，CUDA 13.2）。
            # 显式指定避免默认回落到 CPU；若 CUDA 不可用则加载失败，交由下方
            # except 记录错误并降级到标签匹配兜底，不影响其他功能。
            _image_model = SentenceTransformer(
                settings.clip_model_name, local_files_only=True, device="cuda"
            )
            return _image_model
        except Exception as e:
            _image_model_error = (
                f"图像向量不可用：CLIP 模型未下载或加载失败（本地无缓存）。"
                f"请先离线放置或联网下载模型 {settings.clip_model_name}。({e})"
            )
            _image_model_error_at = time.monotonic()
            logger.error(_image_model_error)
            return None


def _encode_image_sync(file_path: str | None, image_bytes: bytes | None) -> list[float] | None:
    """同步执行图像向量编码（CPU 密集，调用方需放入线程池）。

    参数:
        file_path: 图片文件绝对路径（与 image_bytes 二选一）
        image_bytes: 图片原始字节（与 file_path 二选一）

    返回:
        512 维向量列表；失败返回 None
    """
    # 仅文件路径输入时可走缓存（按路径缓存，避免重复编码）
    if file_path is not None and image_bytes is None:
        cached = _get_cached_image_embedding(file_path)
        if cached is not None:
            return cached

    model = _load_clip_model()
    if model is None:
        return None

    try:
        from PIL import Image

        if image_bytes is not None:
            from io import BytesIO

            img = Image.open(BytesIO(image_bytes))
        else:
            if not file_path or not Path(file_path).exists():
                logger.warning(f"图片不存在，无法生成图像向量: {file_path}")
                return None
            img = Image.open(file_path)

        if img.mode != "RGB":
            img = img.convert("RGB")

        embedding = model.encode(img)
        result = [float(x) for x in embedding.tolist()]
        if file_path is not None and image_bytes is None:
            _cache_image_embedding(file_path, result)
        return result
    except Exception as e:
        logger.error(f"生成图像向量失败: {e}")
        return None


async def generate_image_embedding(
    file_path: str | None = None, image_bytes: bytes | None = None
) -> list[float] | None:
    """为图片生成 CLIP 图像向量（自动降级）。

    参数:
        file_path: 图片文件绝对路径（与 image_bytes 二选一）
        image_bytes: 图片原始字节（与 file_path 二选一）

    返回:
        512 维向量列表；CLIP 依赖缺失、模型未下载或图片无效时返回 None
    """
    if file_path is None and image_bytes is None:
        return None
    return await asyncio.to_thread(_encode_image_sync, file_path, image_bytes)


# ==================== 工具函数 ====================

# 文本向量内容公式版本：build_inspiration_text 的拼接公式变更时递增。
# LanceDB 中只存向量不存原文，无法感知「向量是用旧公式算的」；版本号写入
# lancedb 目录的标记文件（见 store.get_stored_text_formula_version），
# 管理页据此提示「文本向量已过期，建议重建」。
TEXT_EMBEDDING_FORMULA_VERSION = 2  # v2: 正文 caption 参与嵌入

# caption 参与嵌入的最大长度（正文本身有语义价值，但过长会稀释标签信号，
# 且 Ollama 嵌入有 context 上限——generate_text_embedding 内部还有截断重试兜底）
_CAPTION_MAX_CHARS = 500


def get_text_formula_version() -> int:
    """返回当前文本向量内容公式版本号。"""
    return TEXT_EMBEDDING_FORMULA_VERSION


def build_inspiration_text(inspiration: Inspiration) -> str:
    """为素材构建语义搜索用文本（标签名 + 主色调 + 作者 + 正文 caption）。

    正文 caption（笔记描述）是标签之外的重要语义来源——用户常按描述词
    （如「白色亚麻衬衫」「通勤穿搭」）搜索，而标签未必覆盖这些词。
    caption 过长时截断，避免稀释标签信号。

    返回:
        拼接后的文本，无内容时返回空字符串
    """
    parts: list[str] = [t.tag.name for t in inspiration.tags]

    if inspiration.dominant_colors:
        try:
            colors = json.loads(inspiration.dominant_colors)
            if isinstance(colors, list):
                parts.extend([f"颜色 {c}" for c in colors if isinstance(c, str)])
        except Exception:
            pass

    if inspiration.source_author:
        parts.append(inspiration.source_author)

    text = "、".join(parts)

    caption = (inspiration.caption or "").strip()
    if caption:
        if len(caption) > _CAPTION_MAX_CHARS:
            caption = caption[:_CAPTION_MAX_CHARS]
        text = f"{text}\n{caption}" if text else caption

    return text
