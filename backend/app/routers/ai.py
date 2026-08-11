"""AI 分析的 REST API 路由。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.inspiration import Inspiration

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status")
async def ai_status():
    """检查 AI 模型的可用性和状态。"""
    try:
        from app.services.ai_service import check_ollama_status
        status = await check_ollama_status()
        return status
    except ImportError:
        return {
            "status": "not_configured",
            "message": "AI 服务尚未配置。请安装 Ollama 并拉取视觉模型。",
            "ollama_url": "http://localhost:11434",
            "recommended_model": "qwen2:7b-vl",
        }


@router.post("/analyze/{inspiration_id}")
async def analyze_inspiration(
    inspiration_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """触发单个素材的 AI 分析（后台异步执行）。"""
    result = await db.execute(
        select(Inspiration).where(Inspiration.id == inspiration_id)
    )
    inspiration = result.scalar_one_or_none()
    if not inspiration:
        raise HTTPException(status_code=404, detail="灵感素材未找到")

    # 加入后台任务队列
    background_tasks.add_task(_run_analysis, inspiration_id, inspiration.file_path)
    return {
        "message": "分析任务已加入队列",
        "inspiration_id": inspiration_id,
        "status": "analyzing",
    }


@router.post("/batch-analyze")
async def batch_analyze(
    inspiration_ids: list[str],
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量触发多个素材的 AI 分析。"""
    result = await db.execute(
        select(Inspiration).where(Inspiration.id.in_(inspiration_ids))
    )
    inspirations = result.scalars().all()

    if not inspirations:
        raise HTTPException(status_code=404, detail="未找到任何素材")

    for insp in inspirations:
        background_tasks.add_task(_run_analysis, insp.id, insp.file_path)

    return {
        "message": f"已将 {len(inspirations)} 个素材加入分析队列",
        "count": len(inspirations),
    }


async def _run_analysis(inspiration_id: str, file_path: str):
    """后台任务：对图片执行 AI 分析并保存标签。"""
    try:
        from app.services.ai_service import analyze_image
        from app.database import async_session

        async with async_session() as db:
            await analyze_image(db, inspiration_id, file_path)
    except ImportError:
        # AI 服务尚未安装 — Phase 2 实现
        pass
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            f"分析失败 {inspiration_id}: {e}"
        )
