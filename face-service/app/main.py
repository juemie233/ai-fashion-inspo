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
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI

from .face import face_engine
from .router import router

"""日志目录（face-service/app/main.py → 仓库根/logs）。"""
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

"""服务日志文件：由本进程按大小轮转（10 MB × 3），不再依赖启动脚本的 `>` 重定向。"""
_LOG_FILE = _LOG_DIR / "face-service.log"


def _setup_logging() -> None:
    """控制台 + 轮转文件双输出。

    历史问题（2026-11-02 日志扫描 P1-4）：face-service 由 `restart.sh` 用
    `> logs/face-service.log` 重定向，**没有轮转**，进程不在 supervisor 守护内；
    跑久了单文件会无限增长。这里自带 `RotatingFileHandler`，启动脚本的重定向
    改为写 ``face-service-bootstrap.log``（只含 uvicorn 启动/访问行，体积很小）。
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stream_handler]
    try:
        file_handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.insert(0, file_handler)
    except OSError as exc:
        # Windows 下若启动脚本/其它进程已以不共享方式占用日志文件，文件日志会
        # PermissionError；服务本身不应因此启动失败，降级为仅控制台并记录原因。
        logging.getLogger(__name__).warning(
            "人脸服务文件日志不可用，降级为控制台输出: %s", exc
        )
    logging.basicConfig(level=logging.INFO, handlers=handlers)


_setup_logging()
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
