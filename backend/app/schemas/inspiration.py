"""灵感素材的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal, get_args
from pydantic import BaseModel, Field, field_serializer

from app.schemas.person import PersonBriefOut

if TYPE_CHECKING:
    from app.models.inspiration import Inspiration

# 垃圾桶删除原因枚举（负样本学习只用「质量差」子集保证语义纯净）
TrashReason = Literal["质量差", "重复", "不喜欢", "隐私", "其他"]

# 垃圾桶删除原因的运行时全部取值（由 TrashReason 派生，单一来源，勿再手工维护副本）
TRASH_REASONS: tuple[str, ...] = get_args(TrashReason)

# 负样本学习使用的删除原因（语义纯净子集）
LEARN_NEGATIVE_TRASH_REASON: str = "质量差"


class TagOut(BaseModel):
    """标签输出"""
    id: int
    name: str
    category: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class InspirationTagOut(BaseModel):
    """灵感-标签关联输出（含置信度）"""
    tag: TagOut
    confidence: float

    model_config = {"from_attributes": True}


class InspirationCreate(BaseModel):
    """创建灵感的请求体"""
    source_type: str = "manual_upload"
    source_url: str | None = None
    source_author: str | None = None
    source_platform_id: str | None = None
    media_type: str = "image"


class InspirationUpdate(BaseModel):
    """更新灵感的请求体（部分更新）"""
    is_favorite: bool | None = None
    source_author: str | None = None
    quality_status: Literal["pending", "approved", "rejected"] | None = None  # 人工复核翻案
    quality_reason: str | None = None  # 翻案为 rejected 时的自定义原因
    is_ai_generated: bool | None = None  # 人工复核翻案：标记/取消「疑似 AI」


class BatchAddTagsRequest(BaseModel):
    """批量给多个素材关联标签的请求体"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=200)
    names: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=50
    )
    category: str = "free"
    source: str = "manual"


class MoveToTrashRequest(BaseModel):
    """移入垃圾桶的请求体（reason 为空时按素材状态自动推断）。"""

    reason: TrashReason | None = None


class BatchFavoriteRequest(BaseModel):
    """批量收藏/取消收藏的请求体。"""

    ids: list[str] = Field(min_length=1, max_length=500)
    is_favorite: bool


class BatchTrashRequest(BaseModel):
    """批量移入垃圾桶的请求体（reason 为空时按各素材状态自动推断）。"""

    ids: list[str] = Field(min_length=1, max_length=500)
    reason: TrashReason | None = None


class BatchUpdateRequest(BaseModel):
    """批量编辑素材元数据的请求体（仅更新显式提供的字段）。"""

    ids: list[str] = Field(min_length=1, max_length=500)
    source_type: str | None = None
    is_favorite: bool | None = None
    quality_status: Literal["pending", "approved", "rejected"] | None = None
    is_ai_generated: bool | None = None


class InspirationOut(BaseModel):
    """灵感列表项输出"""
    id: str
    source_type: str
    source_url: str | None = None
    source_author: str | None = None
    source_platform_id: str | None = None
    file_path: str
    thumbnail_path: str | None = None
    media_type: str
    dominant_colors: str | None = None
    is_favorite: bool = False
    quality_status: str | None = "pending"
    quality_reason: str | None = None
    is_ai_generated: bool = False
    deleted_at: datetime | None = None
    trash_reason: TrashReason | None = None
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[InspirationTagOut] = []
    persons: list[PersonBriefOut] = []
    analysis_status: str | None = "none"

    model_config = {"from_attributes": True}

    @field_serializer('created_at', 'updated_at', 'deleted_at')
    def serialize_datetime(self, dt: datetime | None, _info) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


class InspirationListOut(BaseModel):
    """灵感分页列表"""

    items: list[InspirationOut]
    total: int
    page: int
    size: int
    # 垃圾桶保留天数（仅垃圾桶列表返回，前端据此展示剩余天数，避免硬编码 30 天）
    trash_retention_days: int | None = None


class InspirationDetailOut(InspirationOut):
    """灵感详情（含 AI 分析日志）"""
    analysis_logs: list["AnalysisLogOut"] = []

    model_config = {"from_attributes": True}


class AnalysisLogOut(BaseModel):
    """AI 分析日志"""
    id: int
    model_name: str
    processing_time_ms: int | None = None
    error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer('created_at')
    def serialize_created_at(self, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def analysis_status_from_logs(logs: list) -> str:
    """从分析日志推断素材的分析状态（仅看标签分析，取最新一条）。

    质量审核（quality_check）日志不产出标签、不参与判定；旧失败日志不影响
    后续成功的判定——只要最新一次标签分析成功即视为「已完成」，避免「重试
    成功后仍因残留失败日志显示为错误」。
    """
    analysis_logs = [log for log in logs if (log.log_type or "analysis") == "analysis"]
    if not analysis_logs:
        return "none"
    latest = max(analysis_logs, key=lambda log: log.id)
    return "error" if latest.error else "done"


def inspiration_to_out(inspiration: "Inspiration") -> InspirationOut:
    """将 ORM 素材对象转换为列表/详情响应模型（各路由共用）。

    此前该转换逻辑在 routers/inspirations.py 与 routers/search.py 重复
    实现（后者甚至反向 import 前者），现统一收敛到 schema 层。
    """
    tags_out = [
        InspirationTagOut(
            tag=TagOut.model_validate(t.tag),
            confidence=t.confidence,
        )
        for t in inspiration.tags
    ]

    # 推断分析状态：仅看最新一次标签分析日志（旧失败日志不覆盖后续成功）
    status = analysis_status_from_logs(inspiration.analysis_logs)

    return InspirationOut(
        id=inspiration.id,
        source_type=inspiration.source_type,
        source_url=inspiration.source_url,
        source_author=inspiration.source_author,
        source_platform_id=inspiration.source_platform_id,
        file_path=inspiration.file_path,
        thumbnail_path=inspiration.thumbnail_path,
        media_type=inspiration.media_type,
        dominant_colors=inspiration.dominant_colors,
        is_favorite=inspiration.is_favorite,
        quality_status=inspiration.quality_status,
        quality_reason=inspiration.quality_reason,
        is_ai_generated=inspiration.is_ai_generated,
        deleted_at=inspiration.deleted_at,
        trash_reason=inspiration.trash_reason,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
        tags=tags_out,
        persons=[
            PersonBriefOut.model_validate(t.person) for t in inspiration.persons
        ],
        analysis_status=status,
    )
