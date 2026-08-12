"""FastAPI 应用入口。"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import inspirations, tags, files, search, ai, scraper, ws, admin


async def _auto_migrate():
    """自动添加模型中已定义但物理表中缺失的列。"""
    import aiosqlite
    db_path = settings.storage_root.parent / "fashion_inspo.db"
    async with aiosqlite.connect(str(db_path)) as conn:
        # inspirations 表
        cursor = await conn.execute("PRAGMA table_info(inspirations)")
        rows = await cursor.fetchall()
        insp_cols = {r[1] for r in rows}

        insp_missing = [
            ("scraper_task_id", "INTEGER REFERENCES scraper_tasks(id) ON DELETE SET NULL"),
        ]
        for col_name, col_def in insp_missing:
            if col_name not in insp_cols:
                await conn.execute(
                    f"ALTER TABLE inspirations ADD COLUMN {col_name} {col_def}"
                )
                print(f"[迁移] inspirations 添加列: {col_name}")

        # scraper_tasks 表
        cursor = await conn.execute("PRAGMA table_info(scraper_tasks)")
        rows = await cursor.fetchall()
        st_cols = {r[1] for r in rows}

        st_missing = [
            ("diagnostics", "TEXT"),
        ]
        for col_name, col_def in st_missing:
            if col_name not in st_cols:
                await conn.execute(
                    f"ALTER TABLE scraper_tasks ADD COLUMN {col_name} {col_def}"
                )
                print(f"[迁移] scraper_tasks 添加列: {col_name}")

        await conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭事件处理。"""
    # 启动：确保存储目录存在
    for dir_path in settings.storage_dirs.values():
        os.makedirs(dir_path, exist_ok=True)

    # 初始化数据库表（含新增 scraper_seen_urls 墓碑表）
    await init_db()

    # 自动迁移缺失的列（开发期模型变更频繁，避免手动 ALTER TABLE）
    await _auto_migrate()

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

    print(f"{settings.app_name} v{settings.app_version} 启动于端口 {settings.port}")
    yield
    # 关闭
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


@app.get("/api/health")
async def health_check():
    """健康检查端点。"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
