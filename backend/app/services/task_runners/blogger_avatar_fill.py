"""博主头像批量补全任务：从 TA **自己的素材**里裁出真实人像作为头像。

为什么用素材而不是平台头像：头像是用来认人的——要的是「这个人长什么样」，不是 TA 在
平台上挂的那张宣传图（可能是拼图、插画、滤镜自拍，甚至根本不是本人）。素材是 TA 自己
发的作品，画面主体通常就是本人；裁出来的人脸与人脸库里的特征也天然同源。

来源优先级（逐级降级，拿到就停）：
1. **已匹配到该博主的人脸检测**（``inspiration_face_detections.matched_blogger_id``，
   带 bbox）：身份已被匹配器或人工确认过，精度最高，且是本地裁剪、零网络开销；
   ``match_status='pending'``（AI 候选未审核）一律不采信；
2. **随机抽 TA 的 N 张素材**（``ORDER BY random()``）：送人脸子服务检测，取**主体脸**
   ——单张脸直接用；多张脸时只有「最大脸面积 ≥ 次大脸的 2 倍」才认（避免合照里把路人
   裁成头像）。抽样而不是「最近 N 张」：博主最近发的往往是细节图/背影/只拍衣服，脸在
   更早的作品里；随机抽样还能让每次重试换一批图，不会永远卡在同几张没脸的图上；
3. 都没有 → 跳过并写清原因（不写跳过表，补了素材/服务后可重试）。

质量门槛（两条，缺一不可）：
- 人脸面积 ≥ 画面 :data:`MIN_FACE_AREA_RATIO`（0.2%：穿搭素材多是全身街拍，脸本来就小）；
- 多脸场景要求主次分明（见上）。

其他口径：
- 默认只补空缺，``overwrite=True`` 才覆盖（那是用户手动挑过的头像）；
- 每补一位提交一次（进度可见、取消时已补的保留）；
- **人脸子服务不可用 → 整个任务直接失败**并给出人话原因，而不是把几百位全记成
  「素材里没检出人脸」——后者会让人误以为是素材问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO

from PIL import Image
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.face import InspirationFaceDetection
from app.models.inspiration import NOT_DELETED, Inspiration
from app.models.person import Blogger, InspirationBlogger
from app.models.task import TaskQueue
from app.services.face_client import FaceServiceHttpError, face_client
from app.services.face_crop import FACE_CROP_SIZE, crop_face
from app.services.face_image import load_inspiration_image
from app.services.person.avatar import save_avatar_bytes
from app.services.task_runners.common import PermanentTaskError, is_cancelled, utcnow

logger = logging.getLogger(__name__)

"""每位博主最多扫几张素材：前几张都没检出可用人脸就跳过（再多也基本是同样的构图）。"""
"""每位博主随机抽多少张素材送人脸检测（按需求定为 10 张）。"""
PICK_IMAGES = 10

"""人脸面积占画面比例下限。

**实测教训（2026-10-24）**：这里是穿搭素材库，主流是**全身街拍**（1080×1440 起），
人脸只占画面 0.25%~0.85%。初版把下限设成 1.5%，结果 452 位里 314 位被判「没检出
明确的人脸」——而诊断脚本显示他们**每张素材都恰好检出 1 张干净的脸**，只是小。
头像最终只在列表里显示 40~72px，0.2%（约 60×60 像素）足够看清，门槛按实际分布定。
"""
MIN_FACE_AREA_RATIO = 0.002

"""扫到这么大的脸就可以收工（0.8%：缩到 72px 已经很清楚，不必再翻后面几张）。"""
GOOD_FACE_AREA_RATIO = 0.008

"""多脸场景的「主次分明」判据：最大脸面积至少是次大脸的这么多倍。"""
DOMINANCE_RATIO = 2.0

"""单张素材里允许的最多检测人脸数：再多基本是合照/街拍，主体不明确。"""
MAX_FACES_FOR_CHECK = 6


def _bbox_list(bbox) -> list[float] | None:
    """取合法 bbox（[x1, y1, x2, y2]），非法返回 None。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _bbox_area(bbox: list[float] | None) -> float:
    """bbox 面积（非法按 0 处理）。"""
    if not bbox:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _image_size(data: bytes) -> tuple[int, int]:
    """图片尺寸（解不了返回 (0, 0)，由调用方按「面积不达标」跳过）。"""
    try:
        with Image.open(BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 —— 解不了就不裁，交给上层跳过
        return 0, 0


def _pick_dominant_face(faces: list[dict], width: int, height: int) -> list[float] | None:
    """从检测结果里挑「主体脸」的 bbox（不达标返回 None）。

    规则：面积最大的那张脸；多脸时要求它至少是次大脸的 :data:`DOMINANCE_RATIO` 倍；
    且面积占画面比例 ≥ :data:`MIN_FACE_AREA_RATIO`。
    """
    ranked: list[tuple[float, list[float]]] = []
    for face in faces:
        bbox = _bbox_list(face.get("bbox"))
        area = _bbox_area(bbox)
        if bbox is not None and area > 0:
            ranked.append((area, bbox))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    best_area, best_bbox = ranked[0]
    if len(ranked) > 1 and best_area < ranked[1][0] * DOMINANCE_RATIO:
        return None
    if width > 0 and height > 0 and best_area < width * height * MIN_FACE_AREA_RATIO:
        return None
    return best_bbox


async def _detect_faces(data: bytes) -> list[dict]:
    """送人脸子服务检测（无脸返回空列表；服务异常向上抛，由任务层统一处理）。"""
    try:
        result = await face_client.embed(data)
    except FaceServiceHttpError as exc:
        # 404 = 这张图没检出人脸（正常结果），其它状态码才是异常
        if exc.status_code == 404:
            return []
        raise
    return [f for f in (result.get("faces") or []) if isinstance(f, dict)]


async def _crop_avatar_from_inspiration(
    inspiration: Inspiration,
) -> tuple[bytes | None, float]:
    """从一张素材里裁出头像（返回 ``(字节, 人脸面积占比)``；不达标返回 (None, 0)）。

    返回占比是为了让上层在几张素材里挑**脸最大**的那张：同样是「有脸」，半身照的脸
    比远景全身体大得多，裁出来的头像也清楚得多。
    """
    data, _warning = await load_inspiration_image(inspiration)
    if not data:
        return None, 0.0
    faces = await _detect_faces(data)
    if not faces or len(faces) > MAX_FACES_FOR_CHECK:
        return None, 0.0
    width, height = await asyncio.to_thread(_image_size, data)
    bbox = _pick_dominant_face(faces, width, height)
    if bbox is None:
        return None, 0.0
    try:
        avatar = await asyncio.to_thread(crop_face, data, bbox, FACE_CROP_SIZE)
    except Exception as exc:  # noqa: BLE001 —— 单张裁剪失败按「这张不行」处理
        logger.debug("头像裁剪失败（按跳过处理）：%s", exc)
        return None, 0.0
    ratio = _bbox_area(bbox) / float(width * height) if width and height else 0.0
    return avatar, ratio


async def _avatar_from_matched_detection(db: AsyncSession, blogger: Blogger) -> tuple[bytes | None, str]:
    """优先用「已匹配到该博主的人脸检测」裁头像（返回 (字节, 来源说明)）。"""
    rows = (
        await db.execute(
            select(InspirationFaceDetection, Inspiration)
            .join(Inspiration, Inspiration.id == InspirationFaceDetection.inspiration_id)
            .where(
                InspirationFaceDetection.matched_blogger_id == blogger.id,
                InspirationFaceDetection.bbox.is_not(None),
                NOT_DELETED,
                or_(
                    InspirationFaceDetection.match_status.is_(None),
                    InspirationFaceDetection.match_status == "confirmed",
                ),
            )
            .order_by(
                InspirationFaceDetection.det_score.desc(),
                Inspiration.created_at.desc(),
            )
            .limit(3)
        )
    ).all()
    for detection, inspiration in rows:
        try:
            bbox = _bbox_list(json.loads(detection.bbox or "[]"))
        except (TypeError, ValueError):
            continue
        if bbox is None:
            continue
        data, _warning = await load_inspiration_image(inspiration)
        if not data:
            continue
        width, height = await asyncio.to_thread(_image_size, data)
        if width <= 0 or height <= 0:
            continue
        if _bbox_area(bbox) < width * height * MIN_FACE_AREA_RATIO:
            continue
        try:
            avatar = await asyncio.to_thread(crop_face, data, bbox, FACE_CROP_SIZE)
        except Exception as exc:  # noqa: BLE001 —— 单张裁剪失败继续试下一张
            logger.debug("已匹配人脸的头像裁剪失败（跳过）：%s", exc)
            continue
        return avatar, f"用人脸库里已匹配的人脸（素材 {inspiration.id}）"
    return None, ""


async def _avatar_from_sampled_materials(db: AsyncSession, blogger: Blogger) -> tuple[bytes | None, str]:
    """**随机抽** TA 的若干张素材，挑脸最大的那张裁头像（脸够清楚就早停）。

    为什么用 ``random()`` 而不是按时间取最新：博主最近发的常是细节图/背影/只拍衣服，
    脸往往在更早的作品里；随机抽样也让重复跑时换一批图试，不会永远卡在同几张没脸的图上
    （SQLite 的 ``random()`` 每行求值一次，配合 LIMIT 就是「随机抽样」）。
    """
    rows = (
        await db.execute(
            select(Inspiration)
            .join(InspirationBlogger, InspirationBlogger.inspiration_id == Inspiration.id)
            .where(InspirationBlogger.blogger_id == blogger.id, NOT_DELETED)
            .order_by(func.random())
            .limit(PICK_IMAGES)
        )
    ).scalars()
    best_avatar: bytes | None = None
    best_ratio = 0.0
    best_note = ""
    for inspiration in rows:
        avatar, ratio = await _crop_avatar_from_inspiration(inspiration)
        if avatar is None:
            continue
        if ratio > best_ratio:
            best_avatar = avatar
            best_ratio = ratio
            best_note = f"从素材 {inspiration.id} 裁出人脸（占画面 {ratio * 100:.1f}%）"
        if ratio >= GOOD_FACE_AREA_RATIO:
            break  # 已经够清楚，省下后面每张约 0.3 秒的人脸检测
    return best_avatar, best_note


async def list_avatar_fill_targets(
    db: AsyncSession,
    blogger_ids: list[int] | None = None,
    overwrite: bool = False,
) -> list[Blogger]:
    """查询本次可补头像的博主：**有素材**、且当前没有头像（或允许覆盖）。

    Args:
        db: 数据库会话。
        blogger_ids: 限定范围（None/空 = 全部）。
        overwrite: 为真时连已有头像的博主一起补（默认只补空缺）。

    Returns:
        待补博主列表（按 id 升序，结果可预期）。
    """
    with_materials = (
        select(InspirationBlogger.blogger_id)
        .join(Inspiration, Inspiration.id == InspirationBlogger.inspiration_id)
        .where(NOT_DELETED)
        .distinct()
    )
    stmt = select(Blogger).where(Blogger.id.in_(with_materials)).order_by(Blogger.id)
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    bloggers = list((await db.execute(stmt)).scalars().all())
    return [b for b in bloggers if overwrite or not (b.avatar_path or "").strip()]


async def create_avatar_fill_task(
    db: AsyncSession,
    blogger_ids: list[int] | None = None,
    overwrite: bool = False,
) -> tuple[TaskQueue | None, int]:
    """创建头像批量补全任务（返回 (任务, 目标数)；无可补博主返回 (None, 0)）。

    参数:
        blogger_ids: 限定补全范围（None = 全部有素材且没头像的博主）
        overwrite: 是否连已有头像的博主一起覆盖（默认只补空缺）
    """
    targets = await list_avatar_fill_targets(db, blogger_ids, overwrite)
    if not targets:
        return None, 0
    ids = [b.id for b in targets]
    task = TaskQueue(
        type="blogger_avatar_fill",
        status="pending",
        progress=0,
        total=len(ids),
        done=0,
        result={"blogger_ids": ids, "overwrite": overwrite},
        max_retries=1,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    logger.info(f"已创建博主头像批量补全任务: #{task.id}，{len(ids)} 个博主")
    return task, len(ids)


async def _ensure_face_service() -> None:
    """任务开跑前确认人脸子服务可用（不可用直接让任务失败并说明原因）。"""
    try:
        await face_client.health(timeout=5)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都按「服务不可用」处理
        raise PermanentTaskError(
            f"人脸识别子服务不可用（{type(exc).__name__}: {exc}）；"
            "头像要从素材里裁人脸，请先启动人脸服务再重试"
        ) from exc


async def execute_avatar_fill(db: AsyncSession, task: TaskQueue) -> None:
    """执行头像批量补全：逐位从素材裁头像，可取消，结果含逐位明细。"""
    payload = task.result or {}
    ids: list[int] = list(payload.get("blogger_ids") or [])
    total = len(ids)
    task.total = total
    if total == 0:
        task.progress = 100
        task.result = {**payload, "results": [], "message": "没有可补头像的博主"}
        await db.commit()
        return

    await _ensure_face_service()

    results: list[dict] = []
    updated = 0
    skipped = 0
    failed = 0
    cancelled = False

    for index, blogger_id in enumerate(ids, start=1):
        if await is_cancelled(db, task):
            cancelled = True
            break
        blogger = await db.get(Blogger, blogger_id)
        detail: dict = {"blogger_id": blogger_id}
        if blogger is None:
            failed += 1
            results.append(
                {
                    **detail,
                    "name": f"# {blogger_id}",
                    "status": "failed",
                    "reason": "博主不存在（可能已删除）",
                }
            )
        else:
            detail["name"] = blogger.name
            avatar, note = await _avatar_from_matched_detection(db, blogger)
            if avatar is None:
                avatar, note = await _avatar_from_sampled_materials(db, blogger)
            if avatar is None:
                skipped += 1
                results.append(
                    {
                        **detail,
                        "status": "skipped",
                        "reason": (
                            f"随机抽的 {PICK_IMAGES} 张素材里都没检出可用的主体脸"
                            "（背影/侧脸/多人合照主次不明/人脸小于画面 0.2% 都不算）；"
                            "可手动挑一张更清楚的"
                        ),
                    }
                )
            else:
                await save_avatar_bytes(db, blogger, avatar)
                updated += 1
                results.append({**detail, "status": "updated", "source": note})
        task.done = index
        task.progress = round(index / total * 100)
        task.updated_at = utcnow()
        await db.commit()

    task.result = {
        **payload,
        "results": results,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "cancelled": cancelled,
    }
    if cancelled:
        task.status = "cancelled"  # worker 见 status != running 不会覆盖为 success
    task.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"博主头像批量补全结束: #{task.id} 成功 {updated} 跳过 {skipped} 失败 {failed} cancelled={cancelled}"
    )
