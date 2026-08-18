"""人物服务实例：穿搭博主 / 职业模特（组合基座、照片组 Mixin 与 CSV 导入）。"""

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import (
    Blogger,
    InspirationBlogger,
    InspirationModel,
    Model,
    ModelPhoto,
    ModelPhotoSet,
)
from app.services.person.base import PersonServiceBase
from app.services.person.csv_import import import_bloggers_csv
from app.services.person.photo_sets import PhotoSetsMixin


class BloggerService(PersonServiceBase):
    """穿搭博主服务。"""

    model = Blogger
    link_model = InspirationBlogger
    link_id_field = "blogger_id"
    link_entity_attr = "blogger"
    label = "博主"

    async def import_from_csv(self, db: AsyncSession, file: UploadFile) -> dict:
        """从 CSV 批量导入博主（按 xhs_id upsert），实现见 services/person/csv_import.py。"""
        return await import_bloggers_csv(db, file)


class ModelService(PersonServiceBase, PhotoSetsMixin):
    """职业模特服务（含写真照片组）。"""

    model = Model
    link_model = InspirationModel
    link_id_field = "model_id"
    link_entity_attr = "model"
    photo_set_model = ModelPhotoSet
    photo_model = ModelPhoto
    has_photo_sets = True
    label = "模特"


blogger_service = BloggerService()
model_service = ModelService()
