"""手机图剪裁的 VLM 辅助判断（状态栏 / 进度条残留复核）。

从 `crop_service` 拆出：这一组函数**只依赖 httpx/PIL/settings**，不碰数据库与业务状态，
是剪裁链路里独立的「模型问询」层。拆出后 `crop_service` 只保留编排逻辑。

三态约定贯穿本模块（很重要）：
- True = 明确检出（阳性）
- False = 明确未检出（阴性）
- None = 判定失败/超时/解析漂移（**未知**）
调用方对 None 必须保守处理（保留候选），不能当作阴性 —— 否则服务异常会误删真残留。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from app.config import settings

logger = logging.getLogger(__name__)

_VLM_STRIP_PROMPT_TOP = (
    "这是某张图片顶部区域的裁条。请判断：这个条带里是否能看到手机状态栏的图标"
    "（时间数字、信号格、电量电池、运营商文字等系统 UI 元素）？"
    "注意：照片内容里的文字（招牌、衣服上的字）不算。"
    '只回答 JSON：{"bar": true} 或 {"bar": false}'
)
_VLM_STRIP_PROMPT_BOTTOM = (
    "这是某张手机截图底部区域的裁条。请判断：这个条带里是否有视频播放器的进度条"
    "（横贯画面的细亮线，通常白色或半透明）？"
    "注意：地砖缝、桌面边缘、照片内容里的横线不算。"
    '只回答 JSON：{"bar": true} 或 {"bar": false}'
)

_vlm_client: httpx.AsyncClient | None = None


def _parse_bar_answer(text: str) -> bool | None:
    """解析 VLM 条带回答（三态）。

    优先 JSON 解析，兼容输出格式漂移（markdown 围栏/空格变体）；
    - ``bar: true`` → True（阳性）；
    - ``bar: false`` → False（阴性）；
    - 无法解析（空响应/非 JSON/非布尔）→ None（未知，调用方保守处理）——
      不能把「解析失败」当作「明确阴性」，否则服务异常会误删候选。
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    try:
        v = json.loads(t).get("bar")
        return True if v is True else False if v is False else None
    except (ValueError, AttributeError):
        low = t.lower()
        if '"bar": true' in low:
            return True
        if '"bar": false' in low:
            return False
        return None


async def _vlm_strip_check(image_bytes_func, top: bool) -> bool | None:
    """VLM 单条带判断（顶部状态栏 / 底部进度条）。

    三态返回（供「VLM 主判」裁决使用，区分「明确阴性」与「判定失败」）：
    - True：VLM 明确检出系统 UI 元素（阳性）；
    - False：VLM 明确未检出（阴性）；
    - None：判定失败（服务不可用/超时/解析失败），调用方按「未知」保守处理。
    """
    global _vlm_client
    try:
        if _vlm_client is None:
            _vlm_client = httpx.AsyncClient(timeout=30)
        payload = image_bytes_func()
        b64 = base64.b64encode(payload).decode()
        prompt = _VLM_STRIP_PROMPT_TOP if top else _VLM_STRIP_PROMPT_BOTTOM
        resp = await _vlm_client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_vision_model,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 32, "num_ctx": 2048},
            },
        )
        text = (resp.json().get("message") or {}).get("content", "")
        return _parse_bar_answer(text)
    except Exception as e:
        logger.debug(f"VLM 条带复核失败（忽略，按未知处理）: {e}")
        return None


def _strip_jpeg_bytes(full: Path, top: bool, quality: int = 88) -> bytes:
    """裁出顶部 12% / 底部 15% 条带并编码 JPEG（条带放大到 768 宽提升图标可见性）。"""
    with Image.open(full) as im:
        img = ImageOps.exif_transpose(im.convert("RGB"))
        W, H = img.size
        if top:
            strip = img.crop((0, 0, W, int(H * 0.12)))
            strip = strip.resize((768, max(24, strip.height * 768 // W)), Image.Resampling.LANCZOS)
        else:
            strip = img.crop((0, int(H * 0.85), W, H))
            strip = strip.resize((768, max(24, strip.height * 768 // W)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        strip.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


async def _vlm_residue_review(full: Path) -> bool | None:
    """VLM 双条带残留复核（三态，供「VLM 主判」裁决）。

    顶部条带与底部条带**分别**提问（`_vlm_strip_check(..., top)` 各自独立
    判定，避免「顶部 OR 底部任一命中」的合并式提问在条带裁剪模糊时互相
    污染）：
    - True：顶部或底部任一明确检出系统 UI 残留（阳性——真残留/状态栏）；
    - False：顶部与底部都明确未检出（阴性——算法疑似但 VLM 排除，即
      「算法误判」的候选，按方案 A 从候选列表移除）；
    - None：任一判定失败/超时/解析漂移（未知——保守保留候选，不误伤）。

    实验口径（33 张用户标注样本）：顶部条带特异性 19/20，底部条带对底部
    细进度线命中 12/13。Ollama 不可用时返回 None（对应 caller 的「未知」
    兜底：保留候选并标注，而非静默当作阴性丢弃真残留）。
    """
    top_hit, bottom_hit = await asyncio.gather(
        _vlm_strip_check(lambda: _strip_jpeg_bytes(full, top=True), top=True),
        _vlm_strip_check(lambda: _strip_jpeg_bytes(full, top=False), top=False),
    )
    # 任一阳性 → 检出；双阴性 → 排除；含未知 → 保守返回 None
    if top_hit is True or bottom_hit is True:
        return True
    if top_hit is False and bottom_hit is False:
        return False
    return None
