"""`ai_analysis` 模块的请求/响应模型（从 routers/ 归位，见 CLAUDE.md：schemas/ 放 Pydantic 模型）。"""

from pydantic import BaseModel

class BatchAnalyzeRequest(BaseModel):
    """批量分析请求体（兼容旧版纯数组与多模型组合分析对象两种格式）。"""

    inspiration_ids: list[str]
    models: list[str] | None = None  # 视觉模型名列表（多选）；缺省用全局默认模型
    prompt_ids: list[int] | None = None  # 提示词版本 ID 列表（0 = 当前默认提示词）
    apply_tags: bool = True  # 是否把标签合并到素材（组合分析默认 False）
