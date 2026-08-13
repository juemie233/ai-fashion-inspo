"""AI 相关路由聚合。

原 ai.py（1700+ 行）已按功能域拆分为多个子路由，此处负责聚合注册：
- ai_models.py     模型管理 + GPU 显存 + 模型统计
- ai_analysis.py   分析 + 队列 + 历史 + 结果对比
- ai_outfit.py     穿搭大标签建议
- ai_quality.py    质量审核
- ai_settings.py   Prompt 管理 + 参数调优
- ai_dashboard.py  分析质量仪表盘 + 单图测试
- ai_reset.py      数据重置

共享状态与后台任务见 ai_shared.py。
"""

from fastapi import APIRouter

from app.routers import (
    ai_analysis,
    ai_dashboard,
    ai_models,
    ai_outfit,
    ai_quality,
    ai_reset,
    ai_settings,
)

router = APIRouter(prefix="/api/ai", tags=["ai"])

router.include_router(ai_models.router)
router.include_router(ai_analysis.router)
router.include_router(ai_outfit.router)
router.include_router(ai_quality.router)
router.include_router(ai_settings.router)
router.include_router(ai_dashboard.router)
router.include_router(ai_reset.router)
