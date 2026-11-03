"""火山引擎方舟 · 豆包 Seedream 5.0 Flash 图生图客户端。

职责边界（刻意做窄）：
- 只做 HTTP 调用、错误归一化、结果下载与图片魔数校验；
- **不碰数据库、不碰文件系统**（由任务执行器准备图片、落盘候选图）；
- 不记录 API Key / base64 / 图片二进制；日志只留模型、尺寸、张数、耗时、request_id。

接口：``POST {base_url}/images/generations``，OpenAI 风格 JSON；参考图用
``data:image/<fmt>;base64,...``。Seedream 5.0 Flash 多图生图顺序即角色：
``image[0]=素材图``、``image[1]=姿态图``（见接入方案 §3）。

重试语义（2026-11-03 评审后修正）：
- **生成请求**失败（429/5xx/网络）才重发，最多 ``MAX_ATTEMPTS`` 次；
- 生成成功后**下载**失败只重试下载（``DOWNLOAD_ATTEMPTS`` 次），绝不重新生成——
  否则云端已计费的一张图会因下载抖动被丢弃，重试又生成一张（重复付费）。
  下载最终失败抛 ``code="download_failed"`` 的永久错误，任务不再自动重试；
- 429 优先遵守响应头 ``Retry-After``；
- 支持 ``should_cancel`` 回调：请求期间每 0.5 秒轮询一次，取消时尽快终止
  （已经发出的请求仍可能被云端计费，UI 已提示这一点）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 官方尺寸约束（显式像素）：总像素 921,600 ~ 4,624,220，宽高比 1/16 ~ 16
MIN_PIXELS = 921_600
MAX_PIXELS = 4_624_220
# 单张参考图上限 30 MB（base64 前）；执行器会先压到长边 ≤2048
MAX_INPUT_BYTES = 30 * 1024 * 1024
# 下载结果大小上限（正常 1.6~8 MB；异常大响应直接判失败，防止占内存）
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
# 生成请求的最大尝试次数（含第一次）；429/5xx/短时网络错误重试
MAX_ATTEMPTS = 3
# 生成成功后「只下载」的重试次数（不重新生成，避免重复计费）
DOWNLOAD_ATTEMPTS = 3
# 请求期间取消轮询间隔（秒）：取消后最多等这么久才终止请求
CANCEL_POLL_SECONDS = 0.5

"""取消回调：返回 True 表示任务已被取消/暂停，客户端应尽快终止。"""
CancelCheck = Callable[[], Awaitable[bool]]


class SeedreamError(Exception):
    """Ark 调用失败；``retryable`` 决定任务是否值得重试。"""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status: int | None = None,
        code: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code
        # 429 响应头 Retry-After（秒）；调用方据此决定退避时长
        self.retry_after = retry_after


@dataclass
class SeedreamResult:
    """一次云端出图结果。"""

    image: bytes | None = None
    size: str = ""
    output_format: str = "png"
    request_id: str | None = None
    usage: dict | None = None
    elapsed_s: float = 0.0
    # 生成成功但还没下载时的图片 URL（下载失败时保留在错误信息里供人工恢复）
    url: str | None = None


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

    注意：档位名（1K 等）的宽高是**兼容展示值**；实际输出尺寸以响应里的
    ``data[0].size`` 为准，执行器会把响应尺寸回写进参数快照（P2-3）。
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


def _retry_after_seconds(value: str | None) -> float | None:
    """解析 Retry-After 头（支持秒数与 HTTP 日期两种格式，只取秒数形式）。"""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        # HTTP 日期格式不解析（方舟实际只返回秒数）；记一条 debug 便于排查限流
        logger.debug("Retry-After 头不是秒数，忽略：%r", value)
        return None
    return max(0.0, min(seconds, 60.0))


def _error_from_response(
    status: int, body: dict | None, *, retry_after: float | None = None
) -> SeedreamError:
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
    # 审核拦截：英文关键词 + 中文关键词都要匹配（方舟中文错误文案常见「内容审核」）
    moderation_keywords = (
        "sensitive",
        "audit",
        "policy",
        "moderation",
        "block",
        "审核",
        "敏感",
        "违规",
        "内容安全",
        "不合规",
    )
    if any(key in lower for key in moderation_keywords):
        return SeedreamError(
            "云端审核未通过：请更换素材或姿态图，或改用本地出图链路（不会绕过审核）",
            retryable=False,
            status=status,
            code=code or None,
        )
    if status == 429:
        return SeedreamError(
            "方舟限流（429），已按退避重试",
            retryable=True,
            status=status,
            code=code or None,
            retry_after=retry_after,
        )
    if status >= 500:
        return SeedreamError(
            f"方舟服务异常（HTTP {status}），可稍后重试",
            retryable=True,
            status=status,
            code=code or None,
            retry_after=retry_after,
        )
    detail = f"：{message}" if message else ""
    return SeedreamError(
        f"云端参数不合法（HTTP {status}）{detail}",
        retryable=False,
        status=status,
        code=code or None,
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
        self._client_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        """懒建并复用连接池（并发调用时只建一个客户端）。"""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self.timeout, follow_redirects=True
                )
        return self._client

    async def generate(
        self,
        *,
        prompt: str,
        images: list[bytes],
        size: str,
        output_format: str = "png",
        watermark: bool = False,
        max_attempts: int | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> SeedreamResult:
        """调用方舟图片生成，返回图片二进制与元信息；失败抛 :class:`SeedreamError`。

        Args:
            prompt: 已包装好的中文提示词（完整长度由调用方控制）。
            images: 参考图二进制，顺序必须是 [素材图, 姿态图]。
            size: ``1K``/``1.5K``/``2K`` 或显式 ``宽x高``。
            output_format: ``png`` / ``jpeg``。
            watermark: 是否加水印（本地自用默认关）。
            max_attempts: 生成请求重试次数（默认 :data:`MAX_ATTEMPTS`，可由配置覆盖）。
            should_cancel: 取消检查回调；请求期间每 0.5s 轮询一次。
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
        attempts = max(1, int(max_attempts or MAX_ATTEMPTS))

        # 1) 生成：失败才重试（429/5xx/网络）；成功后不再重发生成请求
        result = await self._generate_with_retry(body, attempts, should_cancel)
        # 2) 取图：b64 已内联；url 只重试下载（绝不重新生成 → 不重复计费）
        if result.image is None:
            result.image = await self._download_with_retry(
                result.url or "", should_cancel
            )
        if _mime_of(result.image) is None:
            raise SeedreamError(
                "方舟返回的内容不是有效 PNG/JPEG 图片", retryable=False
            )
        result.elapsed_s = round(time.monotonic() - started, 2)
        logger.info(
            "Seedream 出图完成 model=%s size=%s images=%d elapsed=%.2fs request_id=%s",
            self.model,
            result.size or normalized_size,
            len(images),
            result.elapsed_s,
            result.request_id or "-",
        )
        return result

    async def _generate_with_retry(
        self, body: dict, attempts: int, should_cancel: CancelCheck | None
    ) -> SeedreamResult:
        """生成请求重试循环；返回结果时**图片可能还没下载**（由调用方决定）。"""
        last_error: SeedreamError | None = None
        for attempt in range(1, attempts + 1):
            if should_cancel is not None and await should_cancel():
                raise SeedreamError(
                    "云端请求已取消（已发出的请求仍可能计费）",
                    retryable=False,
                    code="cancelled",
                )
            try:
                return await self._request_once(body, should_cancel=should_cancel)
            except SeedreamError as exc:
                last_error = exc
                if exc.code == "cancelled":
                    raise
                if not exc.retryable or attempt >= attempts:
                    raise
                delay = max(
                    _backoff_seconds(attempt), float(exc.retry_after or 0.0)
                )
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = SeedreamError(f"连接方舟失败：{exc}", retryable=True)
                if attempt >= attempts:
                    raise last_error from exc
                await asyncio.sleep(_backoff_seconds(attempt))
        raise last_error or SeedreamError("云端出图失败", retryable=True)

    async def _await_cancellable(self, request: Awaitable[httpx.Response], should_cancel: CancelCheck) -> httpx.Response:
        """等待 HTTP 响应，同时每 0.5 秒检查取消；取消时关闭请求并抛永久取消错误。"""
        task = asyncio.ensure_future(request)
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
                if done:
                    return task.result()
                if await should_cancel():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
                    raise SeedreamError(
                        "云端请求已取消（已发出的请求仍可能计费）",
                        retryable=False,
                        code="cancelled",
                    )
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            raise

    async def _request_once(
        self, body: dict, *, should_cancel: CancelCheck | None = None
    ) -> SeedreamResult:
        """发一次生成请求，返回「结果元信息 + 图片 URL/b64」，不下载 URL。"""
        client = await self._get_client()
        request = client.post(
            f"{self.base_url}/images/generations",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp = (
            await request
            if should_cancel is None
            else await self._await_cancellable(request, should_cancel)
        )
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            raise _error_from_response(
                resp.status_code,
                payload,
                retry_after=_retry_after_seconds(resp.headers.get("retry-after")),
            )
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
        result = SeedreamResult(
            size=str(item.get("size") or body.get("size") or ""),
            output_format=str(item.get("output_format") or body.get("output_format") or "png"),
            request_id=str(request_id) if request_id else None,
            usage=usage if isinstance(usage, dict) else {},
            url=str(item["url"]) if item.get("url") else None,
        )
        if item.get("b64_json"):
            try:
                result.image = base64.b64decode(item["b64_json"])
            except (ValueError, TypeError) as exc:
                raise SeedreamError("方舟返回的 b64 图片无法解码", retryable=False) from exc
        if result.image is None and not result.url:
            raise SeedreamError("方舟未返回可用图片（缺 url/b64_json）", retryable=True)
        return result

    async def _download_with_retry(self, url: str, should_cancel: CancelCheck | None) -> bytes:
        """只重试下载（生成已经成功；重生成会重复计费）。最终失败为永久错误。"""
        if not url:
            raise SeedreamError("方舟未返回图片 URL", retryable=False, code="download_failed")
        last_error: SeedreamError | None = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            if should_cancel is not None and await should_cancel():
                raise SeedreamError(
                    "云端请求已取消（已发出的请求仍可能计费）",
                    retryable=False,
                    code="cancelled",
                )
            try:
                return await self._download(url, should_cancel=should_cancel)
            except SeedreamError as exc:
                last_error = exc
                if exc.code == "cancelled":
                    raise
                if not exc.retryable or attempt >= DOWNLOAD_ATTEMPTS:
                    break
                await asyncio.sleep(_backoff_seconds(attempt))
        raise SeedreamError(
            "云端已生成但下载结果失败（为避免重复计费不自动重新生成；"
            f"可稍后从云端 URL 手动下载）：{url}"
            + (f"（{last_error}）" if last_error else ""),
            retryable=False,
            code="download_failed",
        )

    async def _download(
        self, url: str, *, should_cancel: CancelCheck | None = None
    ) -> bytes:
        """立即下载云端 URL（24h 失效）；校验 content-type、大小上限与图片魔数。"""
        client = await self._get_client()
        request = client.get(url)
        try:
            resp = (
                await request
                if should_cancel is None
                else await self._await_cancellable(request, should_cancel)
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # 网络抖动属于可重试的下载失败：交给 _download_with_retry 处理，
            # 不能让它冒泡成「准备阶段未知异常」而直接判永久失败
            raise SeedreamError(
                f"下载云端结果失败：{exc}", retryable=True
            ) from exc
        if resp.status_code >= 400:
            raise SeedreamError(
                f"下载云端结果失败（HTTP {resp.status_code}）", retryable=True
            )
        content_type = resp.headers.get("content-type", "")
        if content_type and not content_type.startswith("image/"):
            raise SeedreamError("云端结果下载返回的不是图片", retryable=True)
        content_length = resp.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_OUTPUT_BYTES:
            raise SeedreamError(
                f"云端结果超过 {MAX_OUTPUT_BYTES // 1024 // 1024} MB 上限，已拒绝下载",
                retryable=False,
            )
        content = resp.content
        if len(content) > MAX_OUTPUT_BYTES:
            raise SeedreamError(
                f"云端结果超过 {MAX_OUTPUT_BYTES // 1024 // 1024} MB 上限，已拒绝保存",
                retryable=False,
            )
        return content


def _backoff_seconds(attempt: int) -> float:
    """指数退避 1/2/4 秒 + 抖动；429 场景由调用方叠加 Retry-After 取较大值。"""
    base = 2 ** (attempt - 1)
    return base + random.uniform(0, 0.5)


def build_prompt(prompt: str, negative: str | None = None, *, limit: int = 300) -> str:
    """云端没有负向词：把 negative 改写成正向 prompt 末尾的「避免出现：…」并截断。

    默认截断到 **300 字**（官方对中文提示词的建议长度）；超长会分散模型注意力。
    负向内容优先级最低，超出时自然被截掉（调用方负责把用户要求排在前面）。
    """
    text = str(prompt or "").strip()
    neg = str(negative or "").strip()
    if neg:
        text = f"{text}\n避免出现：{neg}" if text else f"避免出现：{neg}"
    return text[: max(1, int(limit))]
