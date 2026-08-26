"""人物（穿搭博主 / 职业模特）的 Pydantic 请求/响应模型。

博主与模特已物理拆分为两张表，schema 同样拆分；共享字段通过
``_PersonFields`` / ``_PersonUpdateFields`` 基类收敛，避免两份定义漂移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.utils.time import format_utc

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


class PersonStyleProfile(BaseModel):
    """人物风格画像：聚合其素材标签的频次 / 类别分布 / 时间趋势"""

    top_tags: list[dict] = []
    by_category: dict[str, int] = {}
    trend: list[dict] = []


class _PersonFields(BaseModel):
    """博主/模特共享的创建字段。"""

    name: str = Field(min_length=1, max_length=128)
    platform: PersonPlatform = "other"
    platform_user_id: str | None = Field(None, max_length=128)
    xhs_id: str | None = Field(None, max_length=64)
    ip_location: str | None = Field(None, max_length=64)
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None

    _validate_name = field_validator("name")(_strip_name)


class _PersonUpdateFields(BaseModel):
    """博主/模特共享的更新字段（部分更新，显式传 null 清空）。"""

    name: str | None = Field(None, min_length=1, max_length=128)
    platform: PersonPlatform | None = None
    platform_user_id: str | None = Field(None, max_length=128)
    xhs_id: str | None = Field(None, max_length=64)
    ip_location: str | None = Field(None, max_length=64)
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None

    _validate_name = field_validator("name")(_strip_name)


class _PersonOutFields(BaseModel):
    """博主/模特共享的输出字段。"""

    id: int
    source: str = "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    inspiration_count: int = 0

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: datetime | None) -> str | None:
        return format_utc(dt)


# ── 穿搭博主 ──


class BloggerCreate(_PersonFields):
    """创建穿搭博主"""


class BloggerUpdate(_PersonUpdateFields):
    """更新穿搭博主"""


class BloggerOut(_PersonOutFields):
    """博主输出（含素材数统计）"""

    name: str
    platform: str = "other"
    platform_user_id: str | None = None
    xhs_id: str | None = None
    ip_location: str | None = None
    profile_url: str | None = None
    avatar_path: str | None = None
    # 人脸缩略图相对路径（从已匹配素材的人脸检测框裁剪缓存；无则 null）
    face_thumb_path: str | None = None
    bio: str | None = None
    # 人物组（方案 B）：null=独立账号；同组时列表折叠只返回主记录
    person_group_id: int | None = None
    # 折叠视图：组内其余账号（展开显示用）；独立账号/平铺视图为空数组
    group_members: list["BloggerOut"] = []
    # 组内平台去重列表（多平台徽标，如 ["douyin", "xiaohongshu"]）
    group_platforms: list[str] = []


class BloggerBriefOut(BaseModel):
    """博主简要输出（素材详情中关联博主展示用）"""

    id: int
    name: str
    platform: str = "other"
    avatar_path: str | None = None

    model_config = {"from_attributes": True}


class BloggerDetailOut(BloggerOut):
    """博主详情（含风格画像）"""

    style_profile: PersonStyleProfile = PersonStyleProfile()


class BloggerListOut(BaseModel):
    """博主分页列表"""

    items: list[BloggerOut]
    total: int
    page: int
    size: int


# ── 职业模特 ──


class ModelCreate(_PersonFields):
    """创建职业模特"""


class ModelUpdate(_PersonUpdateFields):
    """更新职业模特"""


class ModelOut(_PersonOutFields):
    """模特输出（含素材数统计）"""

    name: str
    platform: str = "other"
    platform_user_id: str | None = None
    xhs_id: str | None = None
    ip_location: str | None = None
    profile_url: str | None = None
    avatar_path: str | None = None
    bio: str | None = None


class ModelBriefOut(BaseModel):
    """模特简要输出（素材详情中关联模特展示用）"""

    id: int
    name: str
    platform: str = "other"
    avatar_path: str | None = None

    model_config = {"from_attributes": True}


class ModelDetailOut(ModelOut):
    """模特详情（含风格画像）"""

    style_profile: PersonStyleProfile = PersonStyleProfile()


class ModelListOut(BaseModel):
    """模特分页列表"""

    items: list[ModelOut]
    total: int
    page: int
    size: int


# ── 共用 ──


class PersonImportError(BaseModel):
    """CSV 导入单行失败明细"""

    row: int
    nickname: str | None = None
    reason: str


class PersonImportResult(BaseModel):
    """CSV 导入结果统计（博主专属：按小红书号 upsert）"""

    imported: int  # 新增入库
    updated: int  # 已存在（按 xhs_id）更新昵称/IP
    skipped: int  # 跳过（CSV 内重复 xhs_id 合并）
    failed: int  # 失败行数
    errors: list[PersonImportError] = []  # 失败明细（行号 + 原因）


class PersonLinkRequest(BaseModel):
    """批量关联素材-人物请求（博主/模特共用，字段一致）"""

    person_ids: list[int] = Field(min_length=1, max_length=50)


class BatchPersonLinkRequest(BaseModel):
    """批量关联素材-博主请求（素材库批量选择 → 批量关联穿搭博主）"""

    inspiration_ids: list[str] = Field(min_length=1, max_length=200)
    person_ids: list[int] = Field(min_length=1, max_length=50)


class ModelPhotoSetCreate(BaseModel):
    """创建模特照片组（组名缺省时后端回退为「未命名照片组」）"""

    name: str | None = Field(None, max_length=128)


class ModelPhotoSetUpdate(BaseModel):
    """更新模特照片组（仅名称）"""

    name: str = Field(min_length=1, max_length=128)


class ModelPhotoOut(BaseModel):
    """模特照片输出"""

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


class ModelPhotoSetOut(BaseModel):
    """模特照片组输出（含照片数与封面）"""

    id: int
    model_id: int
    name: str
    photo_count: int = 0
    cover_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_set_datetime(self, dt: datetime | None) -> str | None:
        return format_utc(dt)


class ModelPhotoSetListOut(BaseModel):
    """模特照片组分页列表"""

    items: list[ModelPhotoSetOut]
    total: int
    page: int
    size: int


class ModelPhotoSetDetailOut(ModelPhotoSetOut):
    """模特照片组详情（含分页照片列表）"""

    photos: list[ModelPhotoOut] = []
    total: int = 0
    page: int = 1
    size: int = 100
