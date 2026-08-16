"""FastAPI 应用入口。"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
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

# 垃圾桶自动清理周期（秒）：每 6 小时扫描一次超过保留期的软删除素材
_TRASH_SWEEP_INTERVAL = 6 * 3600


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
                    print(f"[垃圾桶] 自动清理 {result['deleted']} 个过期素材")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[垃圾桶] 自动清理失败: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭事件处理。"""
    # 启动：确保存储目录存在
    for dir_path in settings.storage_dirs.values():
        os.makedirs(dir_path, exist_ok=True)

    # 初始化数据库表（含新增 scraper_seen_urls 墓碑表）
    await init_db()

    # 自动迁移缺失的列（开发期模型变更频繁，避免手动 ALTER TABLE）
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
                print(f"[垃圾桶] 启动清理 {swept['deleted']} 个过期素材")
        except Exception as e:
            print(f"[垃圾桶] 启动清理失败: {e}")

    sweep_task = asyncio.create_task(_sweep_expired_trash())

    print(f"{settings.app_name} v{settings.app_version} 启动于端口 {settings.port}")
    yield
    # 关闭：取消周期性清理任务
    sweep_task.cancel()
    try:
        await sweep_task
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
