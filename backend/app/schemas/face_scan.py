"""人脸库扫描 API 的 Pydantic 请求模型。"""

from pydantic import BaseModel, Field


class FaceConfirmItem(BaseModel):
    """单条审核项：detection_id + 目标人物（confirm 使用；reject/undo 忽略人物字段）。"""

    detection_id: int
    person_type: str | None = None  # blogger / model
    person_id: int | None = None


class FaceScanStartIn(BaseModel):
    """创建扫描任务请求。"""

    scope: str = Field("incremental", pattern="^(incremental|all)$")
    auto_match: bool = True


class FaceMatchRunIn(BaseModel):
    """创建全库候选匹配任务请求（可限定人物范围与阈值）。"""

    scope: str = Field("all", pattern="^(all|bloggers|models)$")
    person_type: str | None = Field(None, pattern="^(blogger|model)$")
    person_id: int | None = None
    threshold: float | None = Field(None, ge=0.0, le=1.0)


class FaceConfirmIn(BaseModel):
    """审核确认/驳回/撤销请求。"""

    action: str = Field(..., pattern="^(confirm|reject|undo)$")
    items: list[FaceConfirmItem] = Field(..., min_length=1, max_length=5000)
