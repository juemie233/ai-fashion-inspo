"""人物的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.utils.time import format_utc

# 内容类型：职业模特写真 / 博主穿搭（UI 区分呈现的核心维度）
PersonType = Literal["model", "blogger"]

# 平台标识
PersonPlatform = Literal["xiaohongshu", "douyin", "other"]


def _strip_name(v: str | None) -> str | None:
    """去除首尾空白；纯空白字符串视为无效名称。"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("人物名称不能为空")
    return v


class PersonCreate(BaseModel):
    """创建人物"""

    name: str = Field(min_length=1, max_length=128)
    person_type: PersonType = "blogger"
    platform: PersonPlatform = "other"
    platform_user_id: str | None = Field(None, max_length=128)
    xhs_id: str | None = Field(None, max_length=64)
    ip_location: str | None = Field(None, max_length=64)
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None

    _validate_name = field_validator("name")(_strip_name)


class PersonUpdate(BaseModel):
    """更新人物（部分更新）"""

    name: str | None = Field(None, min_length=1, max_length=128)
    person_type: PersonType | None = None
    platform: PersonPlatform | None = None
    platform_user_id: str | None = Field(None, max_length=128)
    xhs_id: str | None = Field(None, max_length=64)
    ip_location: str | None = Field(None, max_length=64)
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None

    _validate_name = field_validator("name")(_strip_name)


class PersonOut(BaseModel):
    """人物输出（含素材数统计）"""

    id: int
    name: str
    person_type: PersonType = "blogger"
    platform: str = "other"
    platform_user_id: str | None = None
    xhs_id: str | None = None
    ip_location: str | None = None
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None
    source: str = "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    inspiration_count: int = 0

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        return format_utc(dt)


class PersonBriefOut(BaseModel):
    """人物简要输出（素材详情中关联人物展示用）"""

    id: int
    name: str
    person_type: PersonType = "blogger"
    platform: str = "other"
    avatar_path: str | None = None

    model_config = {"from_attributes": True}


class PersonStyleProfile(BaseModel):
    """人物风格画像：聚合其素材标签的频次 / 类别分布 / 时间趋势"""

    top_tags: list[dict] = []
    by_category: dict[str, int] = {}
    trend: list[dict] = []


class PersonDetailOut(PersonOut):
    """人物详情（含风格画像）"""

    style_profile: PersonStyleProfile = PersonStyleProfile()


class PersonListOut(BaseModel):
    """人物分页列表"""

    items: list[PersonOut]
    total: int
    page: int
    size: int


class PersonImportError(BaseModel):
    """CSV 导入单行失败明细"""

    row: int
    nickname: str | None = None
    reason: str


class PersonImportResult(BaseModel):
    """CSV 导入结果统计"""

    imported: int  # 新增入库
    updated: int  # 已存在（按 xhs_id）更新昵称/IP
    skipped: int  # 跳过（CSV 内重复 xhs_id 合并）
    failed: int  # 失败行数
    errors: list[PersonImportError] = []  # 失败明细（行号 + 原因）


class PersonLinkRequest(BaseModel):
    """批量关联素材-人物请求"""

    person_ids: list[int] = Field(min_length=1, max_length=50)


class PersonPhotoSetCreate(BaseModel):
    """创建人物照片组（组名缺省时后端回退为「未命名照片组」）"""

    name: str | None = Field(None, max_length=128)


class PersonPhotoSetUpdate(BaseModel):
    """更新人物照片组（仅名称）"""

    name: str = Field(min_length=1, max_length=128)


class PersonPhotoOut(BaseModel):
    """人物照片输出"""

    id: int
    set_id: int
    file_path: str
    thumbnail_path: str | None = None
    sort_order: int = 0
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def serialize_photo_created_at(self, dt: datetime | None) -> str | None:
        return format_utc(dt)


class PersonPhotoSetOut(BaseModel):
    """人物照片组输出（含照片数与封面）"""

    id: int
    person_id: int
    name: str
    photo_count: int = 0
    cover_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_set_datetime(self, dt: datetime | None) -> str | None:
        return format_utc(dt)


class PersonPhotoSetListOut(BaseModel):
    """人物照片组分页列表"""

    items: list[PersonPhotoSetOut]
    total: int
    page: int
    size: int


class PersonPhotoSetDetailOut(PersonPhotoSetOut):
    """人物照片组详情（含分页照片列表）"""

    photos: list[PersonPhotoOut] = []
    total: int = 0
    page: int = 1
    size: int = 100
