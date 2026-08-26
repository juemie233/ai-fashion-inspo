"""穿搭灵感库的 SQLAlchemy 数据模型。"""

from app.models.inspiration import (
    AIAnalysisLog,
    AIAnalysisTag,
    AIQualityReview,
    Inspiration,
)
from app.models.tag import Tag, InspirationTag, TagAlias
from app.models.person import (
    Blogger,
    InspirationBlogger,
    InspirationModel,
    Model,
    ModelPhoto,
    ModelPhotoSet,
    PersonGroup,
)
from app.models.face import BloggerFaceEmbedding, InspirationFaceDetection
from app.models.scraper import ScraperSchedule, ScraperTask
from app.models.task import PendingVectorBackfill, TaskQueue
from app.models.audit import AuditLog
from app.models.tag_history import TagHistory
from app.models.service_heartbeat import ServiceHeartbeat

__all__ = [
    "Inspiration",
    "AIAnalysisLog",
    "AIAnalysisTag",
    "AIQualityReview",
    "Tag",
    "InspirationTag",
    "TagAlias",
    "Blogger",
    "Model",
    "InspirationBlogger",
    "InspirationModel",
    "ModelPhoto",
    "ModelPhotoSet",
    "PersonGroup",
    "BloggerFaceEmbedding",
    "InspirationFaceDetection",
    "ScraperTask",
    "ScraperSchedule",
    "TaskQueue",
    "PendingVectorBackfill",
    "AuditLog",
    "TagHistory",
    "ServiceHeartbeat",
]
