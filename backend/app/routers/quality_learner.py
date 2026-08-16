"""AI 子路由：负样本初筛器（方案阶段 0/2）的状态、训练与回滚接口。

初筛器用垃圾桶「质量差」负样本 + 已拒绝素材 + 已通过正样本的 CLIP 图像向量，
训练 sklearn 逻辑回归做质量审核前置初筛；训练/回滚均通过本路由暴露，
供 AI 模型管理页或脚本调用。模型未训练时前置初筛静默跳过，不影响审核主流程。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import quality_learner

router = APIRouter(prefix="/quality-learner", tags=["quality-learner"])


@router.get("/status")
async def learner_status(db: AsyncSession = Depends(get_db)):
    """返回初筛器状态（是否已训练、指标、阈值）与当前正负样本统计。"""
    from app.services.vector import store as vector_store

    status = quality_learner.get_status()
    if vector_store.is_lancedb_available():
        _, _, stats = await quality_learner.collect_samples(db)
        status["dataset"] = stats
    else:
        status["dataset"] = {"error": "lancedb 未安装，请先执行：pip install lancedb"}
    return status


@router.post("/train")
async def learner_train(db: AsyncSession = Depends(get_db)):
    """用当前正负样本训练/重训初筛器，返回训练指标与样本统计。"""
    return await quality_learner.train(db)


@router.post("/reset")
async def learner_reset():
    """删除已训练模型，回滚到纯 VLM 审核（指标变差时使用）。"""
    return quality_learner.reset()
