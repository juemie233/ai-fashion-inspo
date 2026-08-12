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
    from sqlalchemy import func, select, update
    from app.models.scraper import ScraperTask, ScraperSeenURL
    from app.models.inspiration import Inspiration
    async with async_session() as db:
        result = await db.execute(
            update(ScraperTask)
            .where(ScraperTask.status.in_(["running", "pending"]))
            .values(status="failed", error="进程异常终止：后端服务重启导致采集中断")
        )
        if result.rowcount:
            print(f"已清理 {result.rowcount} 个僵尸采集任务")

    # 回填已有素材 URL 到墓碑表（首次运行）
    async with async_session() as db:
        existing_count = (await db.execute(
            select(func.count(ScraperSeenURL.source_url))
        )).scalar() or 0
        if existing_count == 0:
            # 批量回填
            backfill_result = await db.execute(
                select(Inspiration.source_url).where(
                    Inspiration.source_url.isnot(None),
                    Inspiration.source_url != "",
                )
            )
            urls = [r[0] for r in backfill_result.all() if r[0]]
            if urls:
                import asyncio as _asyncio
                BATCH = 500
                total_inserted = 0
                for i in range(0, len(urls), BATCH):
                    batch = urls[i:i + BATCH]
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                    stmt = sqlite_insert(ScraperSeenURL).values([
                        {"source_url": u} for u in batch
                    ]).prefix_with("OR IGNORE")
                    await db.execute(stmt)
                    total_inserted += len(batch)
                    if i + BATCH < len(urls):
                        await _asyncio.sleep(0)  # 让出事件循环
                await db.commit()
                print(f"已回填 {total_inserted} 个已有 URL 到墓碑表")
        else:
            print(f"墓碑表已有 {existing_count} 条记录，跳过回填")

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
