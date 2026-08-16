"""人物的 Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

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
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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


class PersonLinkRequest(BaseModel):
    """批量关联素材-人物请求"""

    person_ids: list[int] = Field(min_length=1, max_length=50)
