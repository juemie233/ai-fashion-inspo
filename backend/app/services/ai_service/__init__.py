"""AI 服务编排层：通过 Ollama 调用视觉模型进行穿搭分析、质量审核与大标签总结。

按职责拆分为子模块后，本包仅做 re-export，保持对调用方的导入路径不变：
- ``from app.services.ai_service import analyze_image``
- ``from app.services.ai_service import analyze_video``
- ``from app.services.ai_service import check_image_quality``
- ``from app.services.ai_service import summarize_outfit_tags``

子模块职责：
- analyze：穿搭分析编排
- quality：质量审核 + AI 生成检测
- outfit_summary：大标签总结
- common：共用辅助（图片读取与 base64 转换、共享 logger）
"""

from app.services.ai_service.analyze import analyze_image, analyze_video
from app.services.ai_service.outfit_summary import summarize_outfit_tags
from app.services.ai_service.quality import check_image_quality

__all__ = ["analyze_image", "analyze_video", "check_image_quality", "summarize_outfit_tags"]
