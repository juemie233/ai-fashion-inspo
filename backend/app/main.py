"""FastAPI 应用入口。"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import inspirations, tags, files, search, ai, scraper, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭事件处理。"""
    # 启动：确保存储目录存在
    for dir_path in settings.storage_dirs.values():
        os.makedirs(dir_path, exist_ok=True)

    # 初始化数据库表
    await init_db()

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
app.include_router(ws.router)


@app.get("/api/health")
async def health_check():
    """健康检查端点。"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
