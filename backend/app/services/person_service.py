"""人物服务（兼容薄壳）：实现按领域拆分至 ``services/person/`` 包。

- ``person/base.py``：异常类 + ``PersonServiceBase``（CRUD / 列表 / 风格画像 / 素材关联）
- ``person/photo_sets.py``：``PhotoSetsMixin``（模特写真照片组）
- ``person/csv_import.py``：博主 CSV 导入（按 xhs_id upsert）
- ``person/services.py``：``BloggerService`` / ``ModelService`` 实例

本文件保留 ``from app.services.person_service import ...`` 的既有引用路径，
仅做再导出，不承载业务逻辑。
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
