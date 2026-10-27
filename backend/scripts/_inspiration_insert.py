"""``inspirations`` 表**直写 INSERT 的单一来源**（采集路径 + f2 导入路径共用）。

为什么要有这个模块：同一张表曾有两份手写 INSERT——采集路径
（``scraper_download.INSERT_INSPIRATION_IMAGE_SQL``）与 f2 导入路径
（``f2_apply.INSERT_F2_SQL``）。两处的「列清单一致」只靠一句注释维系，而列清单一旦
漂移，症状是**运行期才炸且不一定炸得响**：历史事故任务 #46 是「14 列 13 值」——
sqlite 报 `13 values for 14 columns`，重试包装器把它当网络错误反复重下，整条抖音
采集链路静默颗粒无收。

现在三条 SQL 都由 ``InsertSpec`` 描述生成：

- ``columns``：列顺序（**调用方传参的顺序就是这个顺序**，只含非字面量列）；
- ``literals``：写死在 SQL 里的列（NULL / 0 / 'pending' 这类，调用方不传）；
- 生成的 SQL 由 ``test_unit_inspiration_insert.py`` 用 golden 字符串锁死，
  并校验「三条 spec 的列集合差异恰为已知的两列」。

⚠ 改列清单时只改这里，并同步 ``test_unit_inspiration_insert.py`` 的 golden 字符串。
"""

from __future__ import annotations

from dataclasses import dataclass, field

TABLE = "inspirations"

# 采集路径与 f2 路径共有的列（顺序 = 传参顺序；带字面量的列见各 spec 的 literals）
_BASE_COLUMNS = (
    "id",
    "source_type",
    "source_url",
    "file_path",
    "thumbnail_path",
    "media_type",
    "dominant_colors",
    "is_favorite",
    "quality_status",
    "rating",
    "is_ai_generated",
    "content_hash",
    "caption",
    "scraper_task_id",
    "created_at",
    "updated_at",
)

# f2 路径额外写的两列（采集路径拿不到：作者归一化名与作品 ID 都是 f2 侧才有）
F2_EXTRA_COLUMNS = ("source_author", "source_platform_id")


@dataclass(frozen=True)
class InsertSpec:
    """一条 INSERT 的列清单与字面量（列顺序即传参顺序）。"""

    columns: tuple[str, ...]
    literals: dict[str, str] = field(default_factory=dict)

    def sql(self, table: str = TABLE) -> str:
        """生成 SQL：``?`` 占位符给参数列，其余列写各自字面量。"""
        cols = ", ".join(self.columns)
        values = ", ".join(self.literals.get(c, "?") for c in self.columns)
        return f"INSERT INTO {table} ({cols}) VALUES ({values})"

    @property
    def param_columns(self) -> tuple[str, ...]:
        """需要调用方传参的列（顺序 = 元组顺序）。"""
        return tuple(c for c in self.columns if c not in self.literals)

    @property
    def param_count(self) -> int:
        return len(self.param_columns)


# ── 三条 spec ──

# 采集路径 · 图片：缩略图由下载环节生成，这里固定 NULL；content_hash 写 SHA-256
SCRAPER_IMAGE = InsertSpec(
    columns=_BASE_COLUMNS,
    literals={
        "thumbnail_path": "NULL",
        "dominant_colors": "NULL",
        "is_favorite": "0",
        "quality_status": "'pending'",
        "rating": "0",
        "is_ai_generated": "0",
    },
)

# 采集路径 · 视频：写 ffmpeg 首帧缩略图；content_hash 留空（URL 下载，历史实现不算哈希）
SCRAPER_VIDEO = InsertSpec(
    columns=_BASE_COLUMNS,
    literals={
        "dominant_colors": "NULL",
        "is_favorite": "0",
        "quality_status": "'pending'",
        "rating": "0",
        "is_ai_generated": "0",
        "content_hash": "NULL",
    },
)

# f2 导入路径：多两列（作者/作品 ID），scraper_task_id 固定 NULL（f2 不是采集任务），
# 缩略图与 content_hash 都写（本地文件，成本低）
F2_IMPORT = InsertSpec(
    columns=(
        "id",
        "source_type",
        "source_url",
        "source_author",
        "source_platform_id",
        "file_path",
        "thumbnail_path",
        "media_type",
        "dominant_colors",
        "is_favorite",
        "quality_status",
        "rating",
        "is_ai_generated",
        "content_hash",
        "caption",
        "scraper_task_id",
        "created_at",
        "updated_at",
    ),
    literals={
        "dominant_colors": "NULL",
        "is_favorite": "0",
        "quality_status": "'pending'",
        "rating": "0",
        "is_ai_generated": "0",
        "scraper_task_id": "NULL",
    },
)

# 与旧常量同名导出：调用方（含测试）的 import 路径不变
INSERT_INSPIRATION_IMAGE_SQL = SCRAPER_IMAGE.sql()
INSERT_INSPIRATION_VIDEO_SQL = SCRAPER_VIDEO.sql()
INSERT_F2_SQL = F2_IMPORT.sql()
