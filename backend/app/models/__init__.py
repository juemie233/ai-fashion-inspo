"""穿搭灵感库的 SQLAlchemy 数据模型。"""

from app.models.inspiration import (
    AIAnalysisLog,
    AIAnalysisTag,
    AIQualityReview,
    Inspiration,
)
from app.models.tag import Tag, InspirationTag, TagAlias
from app.models.person import Person, InspirationPerson
from app.models.scraper import ScraperTask
from app.models.task import TaskQueue

__all__ = [
    "Inspiration",
    "AIAnalysisLog",
    "AIAnalysisTag",
    "AIQualityReview",
    "Tag",
    "InspirationTag",
    "TagAlias",
    "Person",
    "InspirationPerson",
    "ScraperTask",
    "TaskQueue",
]
