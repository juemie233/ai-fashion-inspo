"""出图相关的请求/响应模型。

只定义"需要校验的入参"用 Pydantic；出参大量是动态拼装的 dict（与项目其它模块一致），
不额外套一层 schema，减少无谓的样板。
"""

from pydantic import BaseModel, Field


class GenerationRunCreate(BaseModel):
    """提交一次出图批次。"""

    kind: str = Field(default="t2i", description="t2i=文生图；i2i=参考图（一期未开放）")
    prompt: str = Field(min_length=1, max_length=2000, description="正向描述")
    negative: str | None = Field(default=None, max_length=2000, description="负向描述")
    width: int = Field(default=1024, description="宽（像素）")
    height: int = Field(default=1024, description="高（像素）")
    steps: int = Field(default=8, description="采样步数")
    cfg: float = Field(default=1.0, description="提示词引导强度")
    seed: int = Field(default=0, description="0=随机；>0 则固定（可复现）")
    count: int = Field(default=1, description="本次生成张数（1~4）")
    # 不传 = 按 kind 取默认档位（t2i → z_image_int8 / i2i → qwen_2_1_i2i）。
# 这里**不能**写成 str + 默认 "z_image_int8"：model_dump() 会把它当成用户显式选择，
# 于是 kind=i2i 的请求会被「档位与生成方式不符」直接拒掉（2026-10 实测的图生图报错）。
    model_key: str | None = Field(default=None, description="模型档位（不传则按 kind 取默认）")
    reference_ids: list[str] = Field(default_factory=list, description="参考素材 id（i2i）")


class GenerationConfirm(BaseModel):
    """确认把候选图入库。"""

    filenames: list[str] = Field(min_length=1, description="候选文件名列表")
    tag_ids: list[int] = Field(default_factory=list, description="入库时直接关联的标签")
    caption_from_prompt: bool = Field(
        default=True, description="是否把 prompt 写进素材正文（便于日后检索）"
    )


class PromptPreviewRequest(BaseModel):
    """从素材反推 prompt。"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=10)

