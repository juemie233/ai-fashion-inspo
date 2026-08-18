"""人脸识别微服务：独立 Python 3.10 环境，供主后端（Python 3.12）通过 HTTP 调用。

启动方式：
    .venv\\Scripts\\python -m uvicorn app.main:app --host 0.0.0.0 --port 18889

能力：
    - 人脸检测 + 512 维特征提取（insightface buffalo_l 模型，onnxruntime-gpu 推理）
    - 人脸注册（持久化到本地 SQLite，特征向量 BLOB 存储）
    - 人脸匹配（余弦相似度，返回 top-k）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from .face import face_engine
from .router import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="人脸识别微服务",
    description="insightface 人脸检测/特征提取/注册/匹配（独立 Python 3.10 环境）",
    version="0.1.0",
)

app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    """启动时预加载模型（加载失败不阻断启动，接口调用时再尝试）。"""
    try:
        await face_engine.ensure_loaded()
        logger.info("人脸模型加载完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("启动预加载人脸模型失败（接口调用时重试）: %s", e)
