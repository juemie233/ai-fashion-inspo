"""采集任务的 Pydantic 请求/响应模型。"""

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.utils.time import format_utc
import logging

logger = logging.getLogger(__name__)


class ScraperTaskCreate(BaseModel):
    """创建采集任务"""
    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(default=[], description="搜索关键词列表")
    max_count: int = Field(default=100, ge=1, le=500)
    headless: bool = Field(default=True, description="是否无头模式（默认无头，与 scraper_browser_headless 配置一致）")
    cdp_port: int | None = Field(default=None, description="CDP 端口，连接真实 Chrome 实现零检测采集（小红书固定使用；抖音提供此端口时走完整通道）")
    cookie_file: str | None = Field(default=None, description="Cookie 文件路径")
    sort_mode: str | None = Field(default=None, description="搜索排序: general | latest | popular（仅小红书搜索模式生效）")
    collect_mode: str | None = Field(default=None, description="采集模式: search | user（user=按博主采集，小红书/抖音均支持）")
    blogger_id: int | None = Field(default=None, description="按博主采集（collect_mode=user）时的博主 ID")
    profile_url: str | None = Field(default=None, description="博主主页 URL（按博主采集；缺省从博主记录自动补齐）")
    platform_user_id: str | None = Field(default=None, description="博主平台用户 ID（按博主采集；缺省从博主记录自动补齐）")
    max_notes: int | None = Field(default=None, ge=1, le=200, description="按博主采集的笔记数上限")
    download_video: bool | None = Field(default=None, description="是否下载视频入库（默认开启；关闭可节省磁盘）")


class ScraperTaskOut(BaseModel):
    """采集任务输出"""
    id: int
    platform: str
    status: str
    config: str | None = None
    items_found: int
    items_added: int
    diagnostics: str | None = None  # 漏斗日志 JSON 字符串
    resume_token: str | None = None  # 断点续采进度 JSON 字符串
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer('started_at', 'finished_at', 'created_at')
    def serialize_datetime(self, dt: datetime | None, _info: Any) -> str | None:
        return format_utc(dt)


class ScraperScheduleCreate(BaseModel):
    """创建定时采集计划"""

    platform: str = Field(..., description="xiaohongshu | douyin")
    keywords: list[str] = Field(..., description="搜索关键词列表（至少 1 个）")
    max_count: int = Field(default=20, ge=1, le=500)
    sort_mode: str | None = Field(default=None, description="general | latest | popular（仅小红书生效）")
    interval_minutes: int = Field(default=1440, ge=30, le=10080, description="执行间隔（分钟）")
    enabled: bool = Field(default=True, description="创建后是否启用")


class ScraperScheduleUpdate(BaseModel):
    """更新定时采集计划（仅更新传入的字段）"""

    keywords: list[str] | None = Field(default=None, description="搜索关键词列表")
    max_count: int | None = Field(default=None, ge=1, le=500)
    sort_mode: str | None = Field(default=None, description="general | latest | popular")
    interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    enabled: bool | None = Field(default=None)


class ScraperScheduleOut(BaseModel):
    """定时采集计划输出"""

    id: int
    platform: str
    keywords: list[str]
    max_count: int
    sort_mode: str | None = None
    enabled: bool
    interval_minutes: int
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_task_id: int | None = None
    run_count: int
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("keywords", mode="before")
    @classmethod
    def parse_keywords(cls, v: Any) -> list[str]:
        """ORM 中 keywords 为 JSON 字符串，反序列化为列表。"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError as exc:
                logger.debug("parse_keywords: 忽略异常（预期降级）：%s", exc)
                return []
        return v or []

    @field_serializer('next_run_at', 'last_run_at', 'created_at')
    def serialize_datetime(self, dt: datetime | None, _info: Any) -> str | None:
        return format_utc(dt)


class F2ImportRequest(BaseModel):
    """``POST /api/scraper/f2-import`` 的可选 JSON 请求体（2026-11-27）。

    与同名 query 参数一一对应，解析优先级为「请求体字段非 None 时覆盖 query」：

    - 字段为 ``None`` 表示「请求体未提供」，回落到同名 query；显式 ``false`` /
      空数组同样会覆盖 query（所以布尔字段不能用 ``False`` 默认值）；
    - ``profiles`` / ``collect_ids`` 按**原生数组**使用、不做逗号拆分，便于前端
      去掉手工 ``join(',')``；旧前端不传 body 时仍走原有 query 解析，行为不变。

    校验口径与 query 一致（mode 枚举、分页范围），另外对数组元素做显式校验：
    profiles 元素不能含逗号、collect_ids 元素必须是纯数字字符串。
    """

    fetch: bool | None = Field(
        None, description="是否先调 f2 增量下载（否则只入库已下载文件）"
    )
    authors: str | None = Field(
        None, description="只处理这些作者（逗号分隔，归一化名）"
    )
    limit: int | None = Field(None, ge=1, description="最多导入多少个作品")
    skip_live: bool | None = Field(None, description="跳过 live 实况的分段视频")
    fetch_limit: int | None = Field(
        None, ge=1, description="下载阶段最多处理多少个作者"
    )
    make_thumbnails: bool | None = Field(None, description="是否生成缩略图")
    prune_downloads: bool | None = Field(
        None, description="入库成功后是否删除 f2 下载目录里的源文件"
    )
    since_days: int | None = Field(
        None, ge=0, le=3650, description="f2 日期窗口天数（0=全历史）"
    )
    include_unknown_authors: bool | None = Field(
        None, description="是否连未登记到博主库的 f2 账号一起处理"
    )
    mode: str | None = Field(
        None,
        pattern="^(post|like|collection)$",
        description="post=博主主页作品；like=我的喜欢；collection=我的收藏",
    )
    like_user: str | None = Field(
        None, description="mode=like/collection 时用的我的主页链接 / sec_user_id"
    )
    register_bloggers: bool | None = Field(
        None, description="mode=like 时是否把未登记的来源作者补建成抖音博主"
    )
    like_max_counts: int | None = Field(
        None, ge=0, le=100000, description="「我的列表」最多翻多少条（0=全量）"
    )
    profiles: list[str] | None = Field(
        None,
        description="按博主全量下载：主页链接 / sec_user_id 原生数组（不拆逗号）",
    )
    collect_ids: list[str] | None = Field(
        None, description="只下这些收藏夹：夹 ID 原生数组（不拆逗号）"
    )
    allow_all_collect: bool | None = Field(
        None, description="没勾收藏夹时是否允许平铺导入全部收藏"
    )

    @field_validator("profiles")
    @classmethod
    def validate_profiles(cls, value: list[str] | None) -> list[str] | None:
        """profiles 数组元素不能含逗号（显式数组后不再拆分，含逗号说明调用方仍按旧格式发）。"""
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if "," in text or "，" in text:
                raise ValueError(
                    "profiles 数组元素不能包含逗号；多个博主请用数组传多个元素"
                )
            cleaned.append(text)
        return cleaned

    @field_validator("collect_ids")
    @classmethod
    def validate_collect_ids(cls, value: list[str] | None) -> list[str] | None:
        """collect_ids 数组元素必须是纯数字字符串（收藏夹 ID），不做逗号拆分。"""
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            if not (text.isascii() and text.isdigit()):
                raise ValueError(
                    "collect_ids 数组元素必须是纯数字字符串（收藏夹 ID）"
                )
            cleaned.append(text)
        return cleaned
