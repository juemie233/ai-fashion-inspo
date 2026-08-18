"""人物服务：穿搭博主（Blogger）与职业模特（Model）的 CRUD 与关联管理。

原单表 persons 已拆为 bloggers / models 两张独立表；本包按领域拆分：
- ``base.py``：异常类 + ``PersonServiceBase``（CRUD / 列表 / 风格画像 / 素材关联）
- ``photo_sets.py``：``PhotoSetsMixin``（模特写真照片组，仅模特服务组合）
- ``csv_import.py``：博主 CSV 导入（按 xhs_id upsert）
- ``services.py``：``BloggerService`` / ``ModelService`` 实例

人物关联一律使用 ID（不按名称匹配），规避「同名多人」的歧义；
素材-博主 / 素材-模特关联分别写入独立关联表（inspiration_bloggers /
inspiration_models）。
"""

from app.services.person.base import (
    PersonConflictError,
    PersonHasInspirationsError,
    PersonNotFoundError,
    PersonServiceBase,
    STYLE_PROFILE_TOP_TAGS,
)
from app.services.person.services import blogger_service, model_service

__all__ = [
    "PersonServiceBase",
    "PersonNotFoundError",
    "PersonConflictError",
    "PersonHasInspirationsError",
    "STYLE_PROFILE_TOP_TAGS",
    "blogger_service",
    "model_service",
]
