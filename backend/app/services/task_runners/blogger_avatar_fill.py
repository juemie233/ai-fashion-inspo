"""博主头像批量补全任务：f2 用户库头像 URL → CDN 下载 → 落盘为人物头像。

为什么需要：人物头像此前只能手动挨个设置（详情页/编辑弹窗里从 TA 的素材挑一张，或
直接上传一张）。而「我的喜欢」自动登记的博主（400+ 位）几乎都没设头像，逐个点不现实。
抖音账号的**官方头像 URL 就在 f2 用户库** ``user_info_web.avatar_url`` 里（离线可读），
拿到直链直接下载即可——不需要登录、不需要浏览器、不需要人脸识别，实测一张
1080×1080 / 57 KB / 0.7 秒。

口径与约定：
- **默认只补空缺**：已有 ``avatar_path`` 的博主一律跳过（那是用户手动挑过的，不覆盖）；
  传 ``overwrite=True`` 才强制覆盖；
- **不做人脸裁剪**：平台头像本身就是头像特写，再送人脸子服务只增加失败面
  （``set_blogger_avatar`` 的裁剪是给「整张照片」用的）；
- **两阶段**：先并发下载（纯网络，不碰数据库），再顺序写盘 + 逐行提交——同一个
  AsyncSession 不能并发写，分开也让「下载快、写库慢」的耗时更可控；
- 单张失败（404/超时/不是图片）只记失败原因，保留原头像，不影响其它博主；
- 每补一位 task.done++，每批检查取消点（取消后已补的保留）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Blogger
from app.models.task import TaskQueue
from app.services.person.avatar import prepare_avatar_image, save_avatar_bytes
from app.services.task_runners.common import is_cancelled, utcnow

logger = logging.getLogger(__name__)

"""下载并发数：头像是 CDN 直链，4 路并发实测稳定；再往上对抖音 CDN 不礼貌。"""
DOWNLOAD_CONCURRENCY = 4

"""单张头像下载超时（秒）：头像几十 KB，20 秒已经是给慢网的余量。"""
DOWNLOAD_TIMEOUT_S = 20.0

"""下载请求头：缺 User-Agent/Referer 时抖音 CDN 会回 403。"""
_DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.douyin.com/",
}


def load_avatar_source() -> dict[str, str]:
    """读取 f2 用户库的头像 URL 表（阻塞式文件读取；调用方放线程池）。

    Returns:
        ``{sec_user_id: avatar_url}``；f2 库不存在/读取异常时返回空 dict
        （调用方按「没有可补的」处理，不抛）。
    """
    from scripts import import_f2_downloads as f2

    try:
        return f2.load_f2_avatars(f2.DEFAULT_F2_DIR)
    except Exception as exc:  # noqa: BLE001 —— 读不到头像来源不该让任务崩
        logger.warning(f"读取 f2 用户库头像失败（头像补全将没有来源）：{exc}")
        return {}


async def list_avatar_fill_targets(
    db: AsyncSession,
    blogger_ids: list[int] | None = None,
    overwrite: bool = False,
) -> list[Blogger]:
    """查询本次可补头像的博主（有 f2 头像来源、且当前没头像或允许覆盖）。

    Args:
        db: 数据库会话。
        blogger_ids: 限定范围（None/空 = 全部抖音博主）。
        overwrite: 为真时连已有头像的博主一起补（默认只补空缺）。

    Returns:
        待补博主列表（按 id 升序，结果可预期）。
    """
    avatars = await asyncio.to_thread(load_avatar_source)
    stmt = select(Blogger).where(Blogger.platform == "douyin").order_by(Blogger.id)
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    bloggers = list((await db.execute(stmt)).scalars().all())
    return [
        b
        for b in bloggers
        if (b.platform_user_id or "").strip() in avatars and (overwrite or not (b.avatar_path or "").strip())
    ]


async def create_avatar_fill_task(
    db: AsyncSession,
    blogger_ids: list[int] | None = None,
    overwrite: bool = False,
) -> tuple[TaskQueue | None, int]:
    """创建头像批量补全任务（返回 (任务, 目标数)；无可补博主返回 (None, 0)）。

    参数:
        blogger_ids: 限定补全范围（None = 全部有头像来源的抖音博主）
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


async def _download(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
) -> tuple[bytes | None, str]:
    """下载并编码一张头像（返回 (JPEG 字节, 失败原因)；成功时原因为空串）。"""
    async with semaphore:
        try:
            response = await client.get(url)
        except Exception as exc:  # noqa: BLE001 —— 单张失败只记账，不中断整批
            return None, f"下载失败：{type(exc).__name__}"
        if response.status_code != 200:
            return None, f"下载失败（HTTP {response.status_code}）"
        data = response.content
    if not data:
        return None, "下载到的头像为空文件"
    # 解码 + 压到 512：一律走「整张图」处理（平台头像本身就是头像特写，不裁人脸）
    avatar_bytes = await asyncio.to_thread(prepare_avatar_image, data)
    if avatar_bytes is None:
        return None, "头像图片无法解析（不是有效图片）"
    return avatar_bytes, ""


async def execute_avatar_fill(db: AsyncSession, task: TaskQueue) -> None:
    """执行头像批量补全：并发下载 → 顺序落盘写库，可取消，结果含逐位明细。"""
    payload = task.result or {}
    ids: list[int] = list(payload.get("blogger_ids") or [])
    total = len(ids)
    task.total = total
    if total == 0:
        task.progress = 100
        task.result = {**payload, "results": [], "message": "没有可补头像的博主"}
        await db.commit()
        return

    avatars = await asyncio.to_thread(load_avatar_source)
    bloggers = list(
        (await db.execute(select(Blogger).where(Blogger.id.in_(ids)).order_by(Blogger.id))).scalars().all()
    )

    results: list[dict] = []
    updated = 0
    skipped = 0
    failed = 0
    cancelled = False

    # ── 阶段 1：并发下载（纯网络，不碰数据库会话）──
    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    downloads: dict[int, tuple[bytes | None, str]] = {}
    urls = {b.id: avatars.get((b.platform_user_id or "").strip(), "") for b in bloggers}

    async def _fetch(blogger_id: int, url: str) -> tuple[int, tuple[bytes | None, str]]:
        """下载一张（异常也折成「单张失败」，不让 gather 整体炸掉）。"""
        try:
            return blogger_id, await _download(client, url, semaphore)
        except Exception as exc:  # noqa: BLE001 —— 兜底：异常按单张失败记账
            return blogger_id, (None, f"下载异常：{type(exc).__name__}: {exc}")

    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True, headers=_DOWNLOAD_HEADERS
    ) as client:
        pairs = await asyncio.gather(*(_fetch(bid, url) for bid, url in urls.items() if url))
    downloads = dict(pairs)

    # ── 阶段 2：顺序写盘 + 逐行提交（会话不能并发写）──
    for index, blogger in enumerate(bloggers, start=1):
        if await is_cancelled(db, task):
            cancelled = True
            break
        url = avatars.get((blogger.platform_user_id or "").strip(), "")
        detail = {"blogger_id": blogger.id, "name": blogger.name}
        if not url:
            skipped += 1
            results.append(
                {
                    **detail,
                    "status": "skipped",
                    "reason": "f2 用户库里没有这个账号的头像（让 f2 采一次 TA 的主页后再试）",
                }
            )
        else:
            avatar_bytes, reason = downloads.get(blogger.id, (None, "未下载"))
            if avatar_bytes is None:
                failed += 1
                results.append({**detail, "status": "failed", "reason": reason})
            else:
                await save_avatar_bytes(db, blogger, avatar_bytes)
                updated += 1
                results.append({**detail, "status": "updated"})
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
