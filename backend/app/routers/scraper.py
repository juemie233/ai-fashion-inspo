"""采集引擎管理的 REST API 路由。"""

import asyncio

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scraper import ScraperSchedule
from app.schemas.scraper import (
    ScraperScheduleCreate,
    ScraperScheduleOut,
    ScraperScheduleUpdate,
    ScraperTaskCreate,
    ScraperTaskOut,
)
from app.services import scraper_service
from app.services.chrome_manager import chrome_manager

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


@router.get("/sources")
async def scraper_sources() -> dict:
    """列出所有可用的采集源及其状态。"""
    return await scraper_service.get_scraper_sources()


@router.post("/f2-import")
async def create_f2_import(
    fetch: bool = Query(True, description="是否先调 f2 增量下载（否则只入库已下载文件）"),
    authors: str | None = Query(None, description="只处理这些作者（逗号分隔，归一化名）"),
    limit: int | None = Query(None, ge=1, description="最多导入多少个作品"),
    skip_live: bool = Query(False, description="跳过 live 实况的分段视频"),
    fetch_limit: int | None = Query(None, ge=1, description="下载阶段最多处理多少个作者"),
    make_thumbnails: bool = Query(True, description="是否生成缩略图"),
    since_days: int | None = Query(
        None, ge=0, le=3650, description="f2 日期窗口天数（0=全历史；缺省取配置）"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """一键获取素材：调 f2 增量下载抖音作品 → 去重 → 入库。

    与 CLI（``python -m scripts.import_f2_downloads --fetch --apply``）同一条链路，
    区别是走任务队列：创建任务后立即返回 task_id，由独立 worker 异步执行，
    前端轮询 ``GET /api/tasks/{task_id}`` 获取进度。

    两条约定：**导入不做标签分析/不建向量**（素材以未打标状态入库，打标请另行
    触发批量分析任务）；**入库前五层去重**（内容哈希 / 垃圾桶 / 批次内 /
    合成平台 ID / 参数过滤）。

    参数:
        fetch: 是否先调 f2 下载（需要本机已装 f2 且其用户库里有作者）。
        authors: 只处理这些作者（与下载、导入阶段共用同一归一化口径）。
        limit: 最多导入多少个作品（按作品计，一个图集只吃一个配额）。
        skip_live: 跳过 live 实况的分段视频。
        fetch_limit: 下载阶段最多处理多少个作者（试跑用）。
        make_thumbnails: 是否生成缩略图（关掉更快，但列表页缺预览图）。
        since_days: f2 只翻最近 N 天的作品。**别轻易用 0**：`-i all` 会让 f2 把
            作者全部历史翻完且每页固定等 timeout 秒（实测单作者 84% 的时间花在
            翻页等待上）；窗口会按「该作者上次下载时间」自动放大，长时间不跑
            也不会漏作品。
    """
    from app.services.task_runner import create_f2_import_task, f2_import_status

    if fetch:
        status = f2_import_status()
        if not status["available"]:
            return {"message": status["reason"], "task_id": None}

    # 并发保护：同一时刻只允许一个「一键获取素材」任务（连点会起多个任务 →
    # f2 子进程并发下载、同一平台 ID 撞唯一索引堆失败）。已有进行中的任务时
    # 直接复用它，不新建。
    from sqlalchemy import select

    from app.models.task import TaskQueue

    running = (
        (
            await db.execute(
                select(TaskQueue)
                .where(
                    TaskQueue.type == "f2_import",
                    TaskQueue.status.in_(("pending", "running", "paused")),
                )
                .order_by(TaskQueue.id.desc())
            )
        )
        .scalars()
        .first()
    )
    if running:
        return {
            "message": f"已有进行中的一键获取素材任务（#{running.id}），请等待完成或先取消",
            "task_id": running.id,
            "reused": True,
        }

    author_list = [a.strip() for a in (authors or "").split(",") if a.strip()]
    task = await create_f2_import_task(
        db,
        authors=author_list,
        limit=limit,
        skip_live=skip_live,
        fetch=fetch,
        fetch_limit=fetch_limit,
        make_thumbnails=make_thumbnails,
        since_days=since_days,
    )
    return {
        "message": "已提交「一键获取素材」任务",
        "task_id": task.id,
        "fetch": fetch,
        "authors": author_list,
    }


@router.get("/f2-status")
async def f2_status(db: AsyncSession = Depends(get_db)) -> dict:
    """「一键获取素材」状态：可用性 + 每日自动获取的配置与到期信息。

    供采集管理页的按钮置灰、开关与「上次/下次运行」提示使用。
    """
    from app.services.task_runner import f2_import_status, get_f2_auto_status

    info = f2_import_status()
    info["auto"] = await get_f2_auto_status(db)
    return info


@router.put("/f2-auto")
async def set_f2_auto(
    enabled: bool = Query(..., description="是否开启每日自动增量入库"),
    interval_hours: int | None = Query(None, ge=1, le=720, description="最小间隔（小时）"),
    skip_live: bool | None = Query(None, description="自动获取是否跳过 live 实况分段"),
    persist: bool = Query(True, description="是否持久化写入 .env 文件"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """开关 f2 每日自动获取素材（f2 增量下载 → 去重 → 入库）。

    与手动入口同一条链路，区别是由后端调度循环按间隔自动创建任务；到期判定见
    `maybe_schedule_auto_import`（已有任务在跑或未到间隔则跳过）。
    配置写入 settings 并（默认）持久化到 .env，重启后保持。
    """
    from app.config import settings
    from app.routers.ai_shared import _update_env_file
    from app.services.task_runner import get_f2_auto_status

    settings.f2_import_auto_enabled = enabled
    if interval_hours is not None:
        settings.f2_import_interval_hours = interval_hours
    if skip_live is not None:
        settings.f2_import_auto_skip_live = skip_live

    if persist:
        updates = {"F2_IMPORT_AUTO_ENABLED": "true" if enabled else "false"}
        if interval_hours is not None:
            updates["F2_IMPORT_INTERVAL_HOURS"] = str(interval_hours)
        if skip_live is not None:
            updates["F2_IMPORT_AUTO_SKIP_LIVE"] = "true" if skip_live else "false"
        await _update_env_file(updates)

    status = await get_f2_auto_status(db)
    return {
        "message": f"每日自动获取素材已{'开启' if enabled else '关闭'}"
        f"（间隔 {status['interval_hours']} 小时）",
        "auto": status,
    }


@router.get("/hashtags")
async def scraper_hashtags(
    sort: str = Query("count", pattern="^(count|recent)$"),
    min_count: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """采集话题标签存档：详情页提取的 #话题 全局去重统计。

    供「话题 → 定时采集关键词」闭环：定时计划配置页按热度勾选话题
    写入计划 keywords。sort=count（按出现次数）/ recent（按最近出现）。
    """
    from sqlalchemy import select

    from app.models.person import Blogger
    from app.models.scraper import ScraperHashtag

    stmt = (
        select(
            ScraperHashtag.name,
            ScraperHashtag.seen_count,
            ScraperHashtag.last_seen_at,
            ScraperHashtag.source_kind,
            ScraperHashtag.source_id,
            Blogger.name.label("blogger_name"),
        )
        .outerjoin(Blogger, Blogger.id == ScraperHashtag.source_id)
        .where(ScraperHashtag.seen_count >= min_count)
    )
    if sort == "recent":
        stmt = stmt.order_by(
            ScraperHashtag.last_seen_at.desc(), ScraperHashtag.seen_count.desc()
        )
    else:
        stmt = stmt.order_by(
            ScraperHashtag.seen_count.desc(), ScraperHashtag.last_seen_at.desc()
        )
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "name": r[0],
                "seen_count": r[1],
                "last_seen_at": r[2].isoformat() if r[2] else None,
                "source_kind": r[3],
                "source_id": r[4],
                "blogger_name": r[5],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/stats")
async def scraper_stats(days: int = 30) -> dict:
    """采集任务统计看板：近 N 天的总量/成功率/按平台与按日分布。"""
    if days < 1 or days > 365:
        days = 30
    return await scraper_service.get_scraper_stats(days)


@router.get("/cdp-check/{port}")
async def check_cdp_endpoint(port: int) -> dict:
    """检查指定端口的 Chrome 调试连接是否就绪。"""
    return await scraper_service.check_cdp(port)


# ============ Chrome 生命周期管理 ============


@router.post("/chrome/start")
async def chrome_start() -> dict:
    """由后端拉起采集专用 Chrome（调试模式）。

    启动流程含子进程拉起与最长 chrome_startup_timeout 秒的就绪轮询
    （同步 sleep），放入线程池执行，避免阻塞整条事件循环。
    """
    return await asyncio.to_thread(chrome_manager.start)


@router.post("/chrome/stop")
async def chrome_stop() -> dict:
    """停止由后端拉起的采集专用 Chrome（含 taskkill 与等待，走线程池）。"""
    return await asyncio.to_thread(chrome_manager.stop)


@router.get("/chrome/status")
async def chrome_status() -> dict:
    """查询采集专用 Chrome 的连接状态（端口探测含 socket 超时，走线程池）。"""
    return await asyncio.to_thread(chrome_manager.status)


# ============ Cookie 管理 ============


@router.get("/cookie-status")
async def cookie_status(platform: str = "xiaohongshu") -> dict:
    """检查指定平台的 Cookie 文件状态。"""
    return await scraper_service.get_cookie_status(platform)


@router.post("/cookie-verify/{platform}")
async def cookie_verify(platform: str) -> dict:
    """真实校验平台 Cookie 登录态（携带 Cookie 请求平台轻量登录态接口）。

    强制探测（不走缓存）；无 Cookie 文件返回 no_file，网络/风控等
    不确定因素返回 unknown，只有确定性证据才判 invalid。
    """
    return await scraper_service.verify_platform_cookie(platform, force=True)


@router.post("/cookie-import")
async def cookie_import(payload: dict) -> dict:
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    return await scraper_service.import_cookies(payload)


@router.delete("/cookie/{platform}")
async def delete_cookie(platform: str) -> dict:
    """删除指定平台的 Cookie 文件。"""
    return await scraper_service.delete_cookies(platform)


# ============ 定时采集计划 ============


@router.get("/schedules", response_model=list[ScraperScheduleOut])
async def list_schedules(db: AsyncSession = Depends(get_db)) -> list[ScraperSchedule]:
    """列出全部定时采集计划。"""
    return await scraper_service.list_schedules(db)


@router.post("/schedules", response_model=ScraperScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(data: ScraperScheduleCreate, db: AsyncSession = Depends(get_db)) -> ScraperSchedule:
    """创建定时采集计划。"""
    return await scraper_service.create_schedule(db, data)


@router.patch("/schedules/{schedule_id}", response_model=ScraperScheduleOut)
async def update_schedule(schedule_id: int, data: ScraperScheduleUpdate, db: AsyncSession = Depends(get_db)) -> ScraperSchedule:
    """更新定时采集计划（启用/停用/改间隔/改关键词等）。"""
    return await scraper_service.update_schedule(db, schedule_id, data)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除定时采集计划。"""
    return await scraper_service.delete_schedule(db, schedule_id)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """立即执行一次定时采集计划。"""
    return await scraper_service.run_schedule_now(db, schedule_id)


# ============ 浏览器插件任务记录 ============


@router.post("/extension-tasks", status_code=status.HTTP_201_CREATED)
async def create_extension_task(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """浏览器插件采集会话开始：创建任务记录并返回 task_id。"""
    task = await scraper_service.create_extension_task(db, payload)
    return {"id": task.id}


@router.post("/extension-tasks/{task_id}/complete")
async def complete_extension_task(task_id: int, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """浏览器插件采集会话结束：汇总发现/入库数量并标记任务完成。"""
    return await scraper_service.complete_extension_task(db, task_id, payload)


# ============ 任务日志 ============


@router.get("/tasks/{task_id}/log")
async def task_log(task_id: int) -> dict:
    """获取采集任务的日志内容（最近 200 行）。"""
    return await scraper_service.get_task_log(task_id)


# ============ 任务取消 ============


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """取消运行中或等待中的采集任务（发送终止信号给子进程）。"""
    return await scraper_service.cancel_scraper_task(db, task_id)


# ============ 创建任务 ============


@router.post(
    "/tasks", response_model=ScraperTaskOut, status_code=status.HTTP_201_CREATED
)
async def create_scraper_task(
    data: ScraperTaskCreate,
    db: AsyncSession = Depends(get_db),
) -> ScraperTaskOut:
    """创建并启动一个新的采集任务。

    CDP 模式下会预先检测 Chrome 调试端口，不可用时返回明确的错误提示。
    """
    task = await scraper_service.create_scraper_task(db, data)
    return ScraperTaskOut.model_validate(task)


@router.get("/tasks")
async def list_scraper_tasks(
    platform: str | None = None,
    status: str | None = None,
    sort: str = "newest",  # newest | oldest | most_found | most_added
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取采集任务列表，支持筛选、排序与分页（返回 items + total + stats）。"""
    tasks, total, stats = await scraper_service.list_scraper_tasks(
        db, platform, status, sort, page, size
    )
    return {
        "items": [ScraperTaskOut.model_validate(t) for t in tasks],
        "total": total,
        "page": page,
        "size": size,
        "stats": stats,
    }


@router.delete("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def delete_single_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。"""
    return await scraper_service.delete_single_scraper_task(db, task_id)


@router.delete("/tasks", status_code=status.HTTP_200_OK)
async def clear_all_scraper_tasks(db: AsyncSession = Depends(get_db)) -> dict:
    """物理删除所有采集任务历史记录。"""
    return await scraper_service.clear_all_scraper_tasks(db)


@router.post("/tasks/retry-failed")
async def retry_failed_scraper_tasks(db: AsyncSession = Depends(get_db)) -> dict:
    """重试所有失败的采集任务，使用相同配置重新创建任务。"""
    return await scraper_service.retry_failed_scraper_tasks(db)


@router.post("/tasks/{task_id}/retry")
async def retry_single_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """重试单个失败任务，沿用断点续采（不重复采集已处理内容）。"""
    return await scraper_service.retry_single_task(db, task_id)


@router.get("/tasks/{task_id}/results")
async def task_results(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    return await scraper_service.get_task_results(db, task_id, page, size)


@router.post("/tasks/{task_id}/results/batch-delete")
async def task_results_batch_delete(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量将采集任务产出的指定素材移入垃圾桶（软删除，可恢复）。

    请求体: {"ids": ["id1", "id2", ...], "reason": "不喜欢"}
    reason 为空时按素材状态自动推断（质量审核被拒 → 质量差，其余 → 不喜欢）。
    """
    ids = payload.get("ids", [])
    reason = payload.get("reason")
    return await scraper_service.batch_delete_task_results(db, task_id, ids, reason)
