"""火山引擎方舟 · 豆包 Seedream 5.0 Flash 图生图客户端。

职责边界（刻意做窄）：
- 只做 HTTP 调用、错误归一化、结果下载与图片魔数校验；
- **不碰数据库、不碰文件系统**（由任务执行器准备图片、落盘候选图）；
- 不记录 API Key / base64 / 图片二进制；日志只留模型、尺寸、张数、耗时、request_id。

接口：``POST {base_url}/images/generations``，OpenAI 风格 JSON；参考图用
``data:image/<fmt>;base64,...``。Seedream 5.0 Flash 多图生图顺序即角色：
``image[0]=素材图``、``image[1]=姿态图``（见接入方案 §3）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 官方尺寸约束（显式像素）：总像素 921,600 ~ 4,624,220，宽高比 1/16 ~ 16
MIN_PIXELS = 921_600
MAX_PIXELS = 4_624_220
# 单张参考图上限 30 MB（base64 前）；执行器会先压到长边 ≤2048
MAX_INPUT_BYTES = 30 * 1024 * 1024
# 429/5xx/超时的最大尝试次数（含第一次）
MAX_ATTEMPTS = 3

_MIME_BY_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),  # 需再判 WEBP 标记；见 _mime_of
)


class SeedreamError(Exception):
    """Ark 调用失败；``retryable`` 决定任务是否值得重试。"""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code


@dataclass
class SeedreamResult:
    """一次云端出图结果。"""

    image: bytes
    size: str
    output_format: str
    request_id: str | None
    usage: dict
    elapsed_s: float


def _mime_of(data: bytes) -> str | None:
    """按文件魔数判断图片 MIME（不信任扩展名）。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _data_uri(data: bytes) -> str:
    """本地图片 → Ark 接受的 data URI；格式名必须小写。"""
    if len(data) > MAX_INPUT_BYTES:
        raise SeedreamError(
            f"参考图超过 {MAX_INPUT_BYTES // 1024 // 1024} MB 上限", retryable=False
        )
    mime = _mime_of(data)
    if mime is None:
        raise SeedreamError("参考图格式不支持（仅 PNG/JPEG/WebP）", retryable=False)
    payload = base64.b64encode(data).decode()
    return f"data:{mime};base64,{payload}"


def validate_size(size: str) -> tuple[str, int, int]:
    """校验输出尺寸，返回 ``(规范字符串, 宽, 高)``。

    支持 ``1K`` / ``1.5K`` / ``2K`` 三种档位，或 ``宽x高`` 显式像素。
    显式像素需满足总像素 921,600~4,624,220、宽高比 1/16~16。
    """
    raw = str(size or "").strip().lower()
    if raw in ("1k", "1024"):
        return "1K", 1024, 1024
    if raw in ("1.5k", "1536"):
        return "1.5K", 1536, 1536
    if raw in ("2k", "2048"):
        return "2K", 2048, 2048
    if "x" not in raw:
        raise SeedreamError("尺寸需为 1K/1.5K/2K 或「宽x高」", retryable=False)
    try:
        width_s, height_s = raw.split("x", 1)
        width, height = int(width_s), int(height_s)
    except (TypeError, ValueError):
        raise SeedreamError("尺寸格式错误，应为「宽x高」", retryable=False) from None
    if width <= 14 or height <= 14:
        raise SeedreamError("尺寸过小：宽高需大于 14", retryable=False)
    total = width * height
    if not MIN_PIXELS <= total <= MAX_PIXELS:
        raise SeedreamError(
            f"尺寸像素数需在 {MIN_PIXELS:,}~{MAX_PIXELS:,} 之间（当前 {total:,}）",
            retryable=False,
        )
    if not (1 / 16 <= width / height <= 16):
        raise SeedreamError("宽高比需在 1/16~16 之间", retryable=False)
    return f"{width}x{height}", width, height


def _error_from_response(status: int, body: dict | None) -> SeedreamError:
    """把 Ark 的 HTTP 错误映射成中文 + 是否可重试。"""
    err = (body or {}).get("error") or {}
    code = str(err.get("code") or "")
    message = str(err.get("message") or "").strip()
    lower = f"{code} {message}".lower()
    if status in (401, 403):
        return SeedreamError(
            "ARK_API_KEY 未配置或已失效：请检查 backend/.env 的 ARK_API_KEY 与控制台模型开通状态",
            retryable=False,
            status=status,
            code=code or None,
        )
    if status == 404:
        return SeedreamError(
            "Seedream 模型不存在或未开通：请核对 SEEDREAM_MODEL 与控制台开通状态",
            retryable=False,
            status=status,
            code=code or None,
        )
    if any(key in lower for key in ("sensitive", "audit", "policy", "moderation", "block")):
        return SeedreamError(
            "云端审核未通过：请更换素材或姿态图，或改用本地出图链路（不会绕过审核）",
            retryable=False,
            status=status,
            code=code or None,
        )
    if status == 429:
        return SeedreamError("方舟限流（429），已按退避重试", retryable=True, status=status, code=code or None)
    if status >= 500:
        return SeedreamError(
            f"方舟服务异常（HTTP {status}），可稍后重试", retryable=True, status=status, code=code or None
        )
    detail = f"：{message}" if message else ""
    return SeedreamError(
        f"云端参数不合法（HTTP {status}）{detail}", retryable=False, status=status, code=code or None
    )


class SeedreamClient:
    """Ark 图片生成 HTTP 客户端（异步，可注入 httpx.AsyncClient 供测试）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or settings.ark_base_url).rstrip("/")
        self.model = model or settings.seedream_model
        self.timeout = float(timeout or settings.seedream_timeout)
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._client

    async def generate(
        self,
        *,
        prompt: str,
        images: list[bytes],
        size: str,
        output_format: str = "png",
        watermark: bool = False,
    ) -> SeedreamResult:
        """调用方舟图片生成，返回图片二进制与元信息；失败抛 :class:`SeedreamError`。

        Args:
            prompt: 已包装好的中文提示词（完整长度由调用方控制）。
            images: 参考图二进制，顺序必须是 [素材图, 姿态图]。
            size: ``1K``/``1.5K``/``2K`` 或显式 ``宽x高``。
            output_format: ``png`` / ``jpeg``。
            watermark: 是否加水印（本地自用默认关）。
        """
        if not self.api_key:
            raise SeedreamError("ARK_API_KEY 未配置，云端出图不可用", retryable=False)
        if not 2 <= len(images) <= 10:
            raise SeedreamError("云端多图生图需要 2~10 张参考图", retryable=False)
        normalized_size, _w, _h = validate_size(size)
        data_images = [_data_uri(item) for item in images]
        body = {
            "model": self.model,
            "prompt": prompt,
            "image": data_images,
            "size": normalized_size,
            "output_format": output_format,
            "response_format": "url",
            "watermark": bool(watermark),
        }
        started = time.monotonic()
        last_error: SeedreamError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await self._request_once(body)
            except SeedreamError as exc:
                last_error = exc
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = SeedreamError(f"连接方舟失败：{exc}", retryable=True)
                if attempt >= MAX_ATTEMPTS:
                    raise last_error from exc
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            result.elapsed_s = round(time.monotonic() - started, 2)
            logger.info(
                "Seedream 出图完成 model=%s size=%s images=%d elapsed=%.2fs request_id=%s",
                self.model,
                normalized_size,
                len(images),
                result.elapsed_s,
                result.request_id or "-",
            )
            return result
        raise last_error or SeedreamError("云端出图失败", retryable=True)

    async def _request_once(self, body: dict) -> SeedreamResult:
        client = await self._get_client()
        resp = await client.post(
            f"{self.base_url}/images/generations",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            raise _error_from_response(resp.status_code, payload)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SeedreamError("方舟返回非 JSON 响应", retryable=True) from exc
        items = data.get("data") or []
        if not items or not isinstance(items[0], dict):
            raise SeedreamError("方舟未返回图片数据", retryable=True)
        item = items[0]
        request_id = resp.headers.get("x-request-id") or data.get("request_id")
        usage = data.get("usage") or {}
        image_bytes: bytes | None = None
        if item.get("b64_json"):
            try:
                image_bytes = base64.b64decode(item["b64_json"])
            except (ValueError, TypeError) as exc:
                raise SeedreamError("方舟返回的 b64 图片无法解码", retryable=False) from exc
        elif item.get("url"):
            image_bytes = await self._download(item["url"])
        if not image_bytes:
            raise SeedreamError("方舟未返回可用图片（缺 url/b64_json）", retryable=True)
        if _mime_of(image_bytes) is None:
            raise SeedreamError("方舟返回的内容不是有效 PNG/JPEG 图片", retryable=False)
        return SeedreamResult(
            image=image_bytes,
            size=str(item.get("size") or body.get("size") or ""),
            output_format=str(item.get("output_format") or body.get("output_format") or "png"),
            request_id=str(request_id) if request_id else None,
            usage=usage if isinstance(usage, dict) else {},
            elapsed_s=0.0,
        )

    async def _download(self, url: str) -> bytes:
        """立即下载云端 URL（24h 失效）；校验 content-type 与图片魔数。"""
        client = await self._get_client()
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise SeedreamError(f"下载云端结果失败（HTTP {resp.status_code}）", retryable=True)
        content_type = resp.headers.get("content-type", "")
        if content_type and not content_type.startswith("image/"):
            raise SeedreamError("云端结果下载返回的不是图片", retryable=True)
        return resp.content


def _backoff_seconds(attempt: int) -> float:
    """指数退避 1/2/4 秒 + 抖动；遵守 Retry-After 由调用方上层按需扩展。"""
    base = 2 ** (attempt - 1)
    return base + random.uniform(0, 0.5)


def build_prompt(prompt: str, negative: str | None = None) -> str:
    """云端没有负向词：把 negative 改写成正向 prompt 末尾的「避免出现：…」并截断。

    截断到 600 字（官方中文建议 ≤300 字，但平台只是建议；这里给包装模板留余量）。
    负向内容优先级低，超出时保留前半段（更重要的保持/姿势约束）。
    """
    text = str(prompt or "").strip()
    neg = str(negative or "").strip()
    if neg:
        text = f"{text}\n避免出现：{neg}" if text else f"避免出现：{neg}"
    return text[:600]
