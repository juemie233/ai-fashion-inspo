"""FastAPI 应用入口。"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, run_migrations
from app.db_migrations import compute_schema_version, ensure_schema
from app.routers import (
    inspirations,
    tags,
    files,
    search,
    ai,
    scraper,
    ws,
    admin,
    tasks,
    persons,
)
from app.utils.auth import is_destructive_route

logger = logging.getLogger(__name__)

# 垃圾桶自动清理周期（秒）：每 6 小时扫描一次超过保留期的软删除素材
_TRASH_SWEEP_INTERVAL = 6 * 3600

# 定时采集调度周期（秒）：每 30 秒检查一次到期计划
_SCHEDULE_TICK_SECONDS = 30


async def _sweep_expired_trash() -> None:
    """周期性地彻底删除超过保留期的垃圾桶素材（30 天自动清理）。

    独立 asyncio 任务运行于服务进程内，无需额外 cron；进程重启时由
    lifespan 立即触发一次清理，之后按固定间隔轮询。
    """
    from app.database import async_session
    from app.services import inspiration_service

    while True:
        try:
            await asyncio.sleep(_TRASH_SWEEP_INTERVAL)
            async with async_session() as db:
                result = await inspiration_service.purge_trash(db, only_expired=True)
                if result.get("deleted"):
                    logger.info(f"[垃圾桶] 自动清理 {result['deleted']} 个过期素材")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[垃圾桶] 自动清理失败: {e}")


async def _scraper_schedule_loop() -> None:
    """周期性检查定时采集计划：到期则创建采集任务并推进下次执行时间。

    独立 asyncio 任务运行于服务进程内；进程重启后由 lifespan 的
    僵尸任务清理逻辑兜底（未启动的计划保留 pending，等待下次到期重试）。
    """
    from app.database import async_session
    from app.services import scraper_service

    while True:
        try:
            await asyncio.sleep(_SCHEDULE_TICK_SECONDS)
            async with async_session() as db:
                triggered = await scraper_service.run_due_schedules(db)
                if triggered:
                    logger.info(f"[定时采集] 本次触发 {triggered} 个计划")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[定时采集] 检查失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭事件处理。"""
    # 启动：确保存储目录存在
    for dir_path in settings.storage_dirs.values():
        os.makedirs(dir_path, exist_ok=True)

    # 初始化数据库表（含新增 scraper_seen_urls 墓碑表）
    await init_db()

    # Alembic 正式迁移：空库建表 / 历史库 stamp 到 baseline / 已管理库升级增量
    await asyncio.to_thread(run_migrations)

    # 手写迁移兜底（Alembic 不可用或失败时仍能补齐缺失列）
    await ensure_schema()

    # 启动时清理遗留的僵尸任务
    from app.database import async_session
    from sqlalchemy import update
    from app.models.scraper import ScraperTask
    async with async_session() as db:
        result = await db.execute(
            update(ScraperTask)
            .where(ScraperTask.status.in_(["running", "pending"]))
            .values(status="failed", error="进程异常终止：后端服务重启导致采集中断")
        )
        if result.rowcount:
            print(f"已清理 {result.rowcount} 个僵尸采集任务")

    # 导入预设标签
    from app.database import async_session
    from app.services.tag_service import seed_tags
    async with async_session() as db:
        added = await seed_tags(db)
        if added:
            print(f"已导入 {added} 个预设标签")

    # 垃圾桶：启动时立即清理一次超过保留期的过期素材，并拉起周期性自动清理任务
    from app.services import inspiration_service
    async with async_session() as db:
        try:
            swept = await inspiration_service.purge_trash(db, only_expired=True)
            if swept.get("deleted"):
                logger.info(f"[垃圾桶] 启动清理 {swept['deleted']} 个过期素材")
        except Exception as e:
            logger.warning(f"[垃圾桶] 启动清理失败: {e}")

    sweep_task = asyncio.create_task(_sweep_expired_trash())
    schedule_task = asyncio.create_task(_scraper_schedule_loop())

    print(f"{settings.app_name} v{settings.app_version} 启动于端口 {settings.port}")
    yield
    # 关闭：取消周期性清理与定时采集调度任务
    for task in (sweep_task, schedule_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    print("正在关闭...")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS 中间件 — 本地开发允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def destructive_api_key_middleware(request: Request, call_next):
    """破坏性接口的 API Key 认证。

    命中 DESTRUCTIVE_ROUTES 清单的写操作（不可恢复删除/重置/批量破坏）需要
    有效的 X-API-Key；未配置 api_key（开发模式）时跳过；读接口与普通写接口
    完全不受影响。
    """
    if not is_destructive_route(request.method, request.url.path):
        return await call_next(request)
    if not settings.api_key:
        return await call_next(request)  # 开发模式：未配置密钥则跳过认证

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "缺少 API 密钥，请在请求头中提供 X-API-Key"},
        )
    if api_key != settings.api_key:
        return JSONResponse(
            status_code=403, content={"detail": "API 密钥无效"}
        )
    return await call_next(request)

# 注册路由
app.include_router(inspirations.router)
app.include_router(tags.router)
app.include_router(files.router)
app.include_router(search.router)
app.include_router(ai.router)
app.include_router(scraper.router)
app.include_router(admin.router)
app.include_router(ws.router)
app.include_router(tasks.router)
app.include_router(persons.router)


@app.get("/api/health")
async def health_check():
    """健康检查端点。

    返回 schema_version 供前端启动时比对，检测前后端契约是否一致。
    """
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "schema_version": compute_schema_version(),
    }
