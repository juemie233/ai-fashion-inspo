"""标签服务（兼容转发层）。

按领域职责拆分后的各子模块统一在此转发导入，保持旧调用方
（路由层、AI 打标、素材标签模块、脚本、测试）的导入路径与行为完全不变：

- ``tag_crud``          标签 CRUD、合并、批量操作、预设导入（含异常类与 SEED_TAGS）
- ``tag_alias``         标签别名管理（同义词归一化）
- ``tag_inspirations``  标签-素材关联（解除关联 / 按标签查素材）
- ``tag_query``         分组列表、统计、重复对、排行、共现网络与趋势
"""

# ── 异常类与常量 ──
from app.services.tag_crud import SEED_TAGS, TagConflictError, TagNotFoundError  # noqa: F401
# ── 标签 CRUD / 合并 / 批量 ──
from app.services.tag_crud import (  # noqa: F401
    batch_change_category,
    batch_delete_tags,
    batch_rename_tags,
    create_tag,
    delete_unused_tags,
    find_similar_tags,
    get_or_create_tag,
    import_tags,
    merge_tag_pair,
    merge_tags,
    reorder_tags,
    seed_tags,
    update_tag,
)
# ── 别名管理 ──
from app.services.tag_alias import create_alias, delete_alias, list_aliases  # noqa: F401
# ── 标签-素材关联 ──
from app.services.tag_inspirations import (  # noqa: F401
    batch_remove_tag_inspirations,
    list_tag_inspirations,
)
# ── 查询与统计 ──
from app.services.tag_query import (  # noqa: F401
    export_tags,
    find_duplicate_tag_pairs,
    get_all_tags_grouped,
    get_cooccurrence_network,
    get_tag_stats,
    get_tag_trend,
    get_top_tags,
)
