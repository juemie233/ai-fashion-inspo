"""人脸库扫描 API 的 Pydantic 请求模型。"""

from pydantic import BaseModel, Field


class FaceConfirmItem(BaseModel):
    """单条审核项：detection_id + 目标人物（confirm 使用；reject/undo 忽略人物字段）。"""

    detection_id: int
    person_type: str | None = None  # blogger / model
    person_id: int | None = None


class FaceScanStartIn(BaseModel):
    """创建扫描任务请求。

    scope 三种模式：
    - incremental：仅扫描无任何检测记录的素材；
    - semi：半增量，跳过已有已确认（锁定）记录的素材；
    - all：全量，跳过含锁定记录的素材，其余清空重扫。
    """

    scope: str = Field("semi", pattern="^(incremental|semi|all)$")
    auto_match: bool = False


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


class FaceClusterRunIn(BaseModel):
    """创建人脸聚合聚类任务请求（阈值/最小组成员数可调）。

    threshold 为「建边阈值」（两两直连相似度门槛，默认 0.6）；平均链接
    合并门槛由它推导（建边阈值 - 0.15，下限 0.40），杜绝链式合并巨组。
    """

    threshold: float | None = Field(None, ge=0.0, le=1.0)
    min_group_size: int | None = Field(None, ge=2, le=100)
