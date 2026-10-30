"""姿势参考图库（ai-pose-gallery-zh）目录：拉取、缓存、筛选、按需下载姿势图。

为什么由后端做、而不是前端直连源站：

- 源站是 GitHub Pages 静态站，``/data/pose-zh.json`` **没有 CORS 头**（实测），
  前端跨域 ``fetch`` 会被浏览器直接拦掉；
- 后端拉一次缓存到 ``storage/pose_gallery/``，前端只跟我们的 ``/api/pose-gallery``
  打交道：源站抖动/临时不可用时还有本地缓存兜底，出图时也不必依赖浏览器网络；
- 只读：不修改源站、不写仓库，图片按需下载到 storage 缓存（不随仓库分发）。

**不做内容分级过滤**：本项目是个人本地使用，源站全部 4 个体系的姿势都可选
（包括成人向体系）。图片与原始数据的版权归原作者、代码 MIT 不覆盖图片 ——
因此仍坚持两点：**只在本地使用、不随仓库分发图片**（按需下载到 storage 缓存）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

"""姿势 id 白名单：源站 id 形如 ``full_sexy_P001_front``，只允许字母数字下划线连字符。
既防路径穿越（id 会被拼进缓存文件名），也避免脏数据把缓存目录写乱。
"""
POSE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

"""图片最小字节数与 PNG 魔数：源站原图是 900×1200 PNG（最小 73 KB），
低于 2 KB 或不是 PNG 说明拿到的是错误页/半截文件，不能当参考图用。
"""
MIN_IMAGE_BYTES = 2048
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

"""上游响应大小硬上限：源站是第三方静态站，异常/被篡改时不能把整段响应读进内存。

目录正常约 0.6 MB；原图最大约 917 KB。上限留足余量，同时挡住「错误页/超大文件」。
"""
MAX_CATALOG_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024

"""目录接口的固定说明（前端直接展示，避免文案各写一份）。"""
GALLERY_NOTE = (
    "图片来自 ai-pose-gallery-zh（含成人向内容，仅限成年人本地个人使用，不随项目分发）"
)


def cache_dir() -> Path:
    """姿势库本地缓存根目录（storage 下，gitignore 覆盖）。"""
    folder = settings.storage_root / "pose_gallery"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def catalog_cache_path() -> Path:
    """目录缓存文件路径。"""
    return cache_dir() / "catalog.json"


def _base_url() -> str:
    """源站根地址（去掉尾部斜杠，拼 URL 时统一加 ``/``）。"""
    return settings.pose_gallery_base_url.rstrip("/")


def _safe_relative_path(value: Any) -> str | None:
    """校验源站给的图片相对路径：只允许站内相对路径，拒绝绝对 URL 与路径穿越。

    源站数据虽然可信，但它来自第三方且会被写进 ``<img src>`` 与下载请求：
    一旦上游被篡改/换源，``https://evil``、``../`` 这类值不能原样拼进 URL。
    """
    path = str(value or "").strip()
    if (
        not path
        or path.startswith("/")  # 绝对路径/协议相对地址一律拒绝
        or "://" in path
        or "\\" in path
        or any(part == ".." for part in path.split("/"))
    ):
        return None
    return path


def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """把源站一条记录归一化；字段残缺/id/路径非法的返回 None（脏数据不进目录）。

    Args:
        raw: 源站 ``pose-zh.json`` 的一条记录（中文键：体系/分类/姿势名称/角度/缩略图/原图）。

    Returns:
        归一化后的姿势（id/name/pose_no/system/category/angle/thumb_url/image_url），
        不满足条件时返回 None（调用方负责过滤）。
    """
    pose_id = str(raw.get("id") or "").strip()
    if not pose_id or not POSE_ID_RE.match(pose_id):
        return None
    system = str(raw.get("体系") or "").strip()
    if not system:
        return None
    thumb = _safe_relative_path(raw.get("缩略图"))
    original = _safe_relative_path(raw.get("原图"))
    if not thumb or not original:
        return None
    base = _base_url()
    return {
        "id": pose_id,
        "name": str(raw.get("姿势名称") or "").strip(),
        "pose_no": str(raw.get("姿势编号") or "").strip(),
        "system": system,
        "category": str(raw.get("分类") or "").strip(),
        "angle": str(raw.get("角度") or "").strip(),
        "thumb_url": f"{base}/{thumb}",
        "image_url": f"{base}/{original}",
    }


async def _fetch_limited(
    url: str,
    max_bytes: int,
    *,
    timeout: float = 20.0,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """流式下载并限制大小：上游异常返回超大文件时及时中止，避免占满内存。

    Args:
        url: 目标地址。
        max_bytes: 允许的最大字节数。
        timeout: 超时秒数。
        client: 可选注入的 AsyncClient（测试用 MockTransport）；缺省自建并负责关闭。
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        async with client.stream(
            "GET", url, headers={"User-Agent": "fashion-inspo-pose-gallery/1.0"}
        ) as resp:
            resp.raise_for_status()
            length = resp.headers.get("content-length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise ValueError(f"响应过大（{length} 字节，上限 {max_bytes}）")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"响应超过上限（{max_bytes} 字节）")
                chunks.append(chunk)
            return b"".join(chunks)
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_catalog() -> list[dict[str, Any]]:
    """从源站拉取中文目录并归一化（失败抛异常，由 ``load_catalog`` 兜底）。

    Raises:
        httpx.HTTPError: 网络/状态码异常。
        ValueError: 返回过大/不是数组、或过滤后为空（说明上游格式变了，不能当成"没有姿势"）。
    """
    url = f"{_base_url()}/data/pose-zh.json"
    data = await _fetch_limited(url, MAX_CATALOG_BYTES)
    try:
        raw = json.loads(data)
    except ValueError as exc:
        raise ValueError("姿势目录不是合法 JSON（上游格式可能变了）") from exc
    if not isinstance(raw, list):
        raise ValueError("姿势目录格式异常：顶层不是数组")
    items = [item for item in (_normalize(row) for row in raw if isinstance(row, dict)) if item]
    if not items:
        raise ValueError("姿势目录为空（上游格式可能变了）")
    return items


def _read_cache() -> list[dict[str, Any]] | None:
    """读本地目录缓存；缺失/损坏返回 None（当作没有缓存，而不是抛错）。"""
    path = catalog_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list) or not data:
        return None
    return [item for item in data if isinstance(item, dict)]


def _write_cache(items: list[dict[str, Any]]) -> None:
    """原子写目录缓存（唯一 .part 再 replace）：半截 JSON 会被下次读成"没有缓存"。

    .part 文件名带随机后缀：并发回源（多个请求同时遇到过期缓存）不能共用同一个
    临时文件，否则两个写入会交错出损坏的 JSON。
    """
    path = catalog_cache_path()
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.part")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


async def load_catalog(*, force: bool = False) -> list[dict[str, Any]]:
    """返回姿势目录（优先本地缓存，过期才回源；回源失败用旧缓存兜底）。

    Args:
        force: 忽略缓存强制回源（手动刷新/测试用）。

    Returns:
        归一化后的姿势列表（约 1439 条）。

    Raises:
        HTTPException(503): 既没有可用缓存、回源也失败 —— 前端据此给出可执行提示。
    """
    path = catalog_cache_path()
    ttl_seconds = max(1, int(settings.pose_gallery_cache_hours)) * 3600
    if not force and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < ttl_seconds:
            cached = _read_cache()
            if cached:
                return cached

    try:
        items = await _fetch_catalog()
    except Exception as exc:  # noqa: BLE001 —— 任何回源失败都要能用旧缓存兜底
        cached = _read_cache()
        if cached:
            logger.warning(f"姿势目录回源失败，使用本地缓存（{len(cached)} 条）：{exc!r}")
            return cached
        raise HTTPException(
            status_code=503,
            detail=f"姿势库暂时不可用（{_base_url()}）：{exc}",
        ) from exc

    try:
        await asyncio.to_thread(_write_cache, items)
    except OSError as exc:  # 缓存写失败不影响本次返回，只是下次要重新拉
        logger.warning(f"写姿势目录缓存失败（忽略）：{exc!r}")
    return items


def filter_poses(
    items: list[dict[str, Any]],
    *,
    q: str | None = None,
    system: str | None = None,
    category: str | None = None,
    angle: str | None = None,
) -> list[dict[str, Any]]:
    """按关键字/体系/分类/角度筛选（大小写不敏感；空值不筛）。"""
    keyword = (q or "").strip().lower()
    result = items
    if keyword:
        result = [
            item
            for item in result
            if keyword in f"{item['id']} {item['name']} {item['category']} {item['pose_no']}".lower()
        ]
    if system:
        result = [item for item in result if item["system"] == system]
    if category:
        result = [item for item in result if item["category"] == category]
    if angle:
        result = [item for item in result if item["angle"] == angle]
    return result


async def list_poses(
    *,
    q: str | None = None,
    system: str | None = None,
    category: str | None = None,
    angle: str | None = None,
    page: int = 1,
    size: int = 40,
) -> dict[str, Any]:
    """分页查询姿势（服务端筛选+分页；目录约 1439 条，内存过滤足够快）。"""
    items = await load_catalog()
    filtered = filter_poses(items, q=q, system=system, category=category, angle=angle)
    page = max(1, int(page))
    size = min(max(1, int(size)), 100)
    start = (page - 1) * size
    return {
        "items": filtered[start : start + size],
        "total": len(filtered),
        "page": page,
        "size": size,
        "note": GALLERY_NOTE,
    }


def _counts(values: list[str]) -> list[dict[str, Any]]:
    """统计取值频次，按数量降序（筛选项下拉用）。"""
    counter: dict[str, int] = {}
    for value in values:
        if value:
            counter[value] = counter.get(value, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def meta() -> dict[str, Any]:
    """姿势库元信息：总数 + 体系/分类/角度筛选项（前端下拉用）。"""
    items = await load_catalog()
    return {
        "total": len(items),
        "systems": _counts([item["system"] for item in items]),
        "categories": _counts([item["category"] for item in items]),
        "angles": _counts([item["angle"] for item in items]),
        "note": GALLERY_NOTE,
        "source_url": _base_url(),
    }


async def get_pose(pose_id: str) -> dict[str, Any]:
    """按 id 取一条姿势（生成前校验用）。

    Raises:
        HTTPException(400): id 非法（防路径穿越）。
        HTTPException(404): 姿势不存在（源站没有这个 id）。
    """
    if not pose_id or not POSE_ID_RE.match(pose_id):
        raise HTTPException(status_code=400, detail=f"姿势 id 非法：{pose_id!r}")
    items = await load_catalog()
    for item in items:
        if item["id"] == pose_id:
            return item
    raise HTTPException(status_code=404, detail=f"姿势不存在：{pose_id}")


async def _fetch_image(url: str) -> bytes:
    """下载一张姿势原图（流式 + 大小上限，避免错误页/超大文件占满内存）。"""
    return await _fetch_limited(url, MAX_IMAGE_BYTES, timeout=60.0)


def _is_png(path: Path) -> bool:
    """缓存命中时也要验 PNG 魔数：只有大小达标可能是上次写坏的半截文件/错误页。"""
    try:
        with path.open("rb") as fh:
            return fh.read(len(PNG_MAGIC)) == PNG_MAGIC
    except OSError:
        return False


async def ensure_pose_image(pose_id: str) -> Path:
    """确保姿势原图已下载到本地缓存，返回文件路径（出图执行器用）。

    缓存按 ``<pose_id>.png`` 存：同一姿势反复使用只下载一次；文件存在、大小达标且
    PNG 魔数正确才直接复用（损坏缓存会重新下载）。

    Raises:
        HTTPException: id 非法/姿势不存在（400/404）或下载失败（503/502）。
    """
    pose = await get_pose(pose_id)
    folder = cache_dir() / "images"
    target = folder / f"{pose_id}.png"
    if target.exists() and target.stat().st_size >= MIN_IMAGE_BYTES and _is_png(target):
        return target

    try:
        data = await _fetch_image(pose["image_url"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"姿势图下载失败：{exc}") from exc
    if len(data) < MIN_IMAGE_BYTES or not data.startswith(PNG_MAGIC):
        raise HTTPException(status_code=502, detail="姿势图格式异常（非 PNG 或文件过小）")

    folder.mkdir(parents=True, exist_ok=True)
    # 唯一临时名：同一姿势被两个任务并发下载时不能共用同一个 .part 文件
    tmp = target.with_name(f"{target.name}.{uuid4().hex}.part")
    await asyncio.to_thread(tmp.write_bytes, data)
    await asyncio.to_thread(tmp.replace, target)
    logger.info(f"姿势图已缓存：{pose_id}（{len(data)} 字节）")
    return target
