"""灵感素材服务（兼容转发层）。

按领域职责拆分后的各子模块统一在此转发导入，保持旧调用方
（路由层、worker、脚本、测试）的导入路径与行为完全不变：

- ``inspiration_create``   素材创建与导入（上传 / URL 下载）
- ``inspiration_query``    列表查询、详情、颜色统计
- ``inspiration_update``   单条更新、批量元数据更新、收藏
- ``inspiration_tags``     标签关联管理（单条 / 批量）
- ``inspiration_trash``    垃圾桶软删除、恢复、清空、物理删除
- ``inspiration_state``    垃圾桶状态机、校验、不变量检查
- ``inspiration_dedupe``   内容哈希去重、平台 ID 查重、墓碑检查
"""

# ── 创建 / 导入 ──
from app.services.inspiration_create import (  # noqa: F401
    create_inspiration,
    create_inspiration_from_url,
)
# ── 查询 ──
from app.services.inspiration_query import (  # noqa: F401
    get_inspiration,
    list_dominant_colors,
    list_inspirations,
)
# ── 更新 / 收藏 ──
from app.services.inspiration_update import (  # noqa: F401
    batch_favorite_inspirations,
    batch_update_inspirations,
    update_inspiration,
)
# ── 标签关联 ──
from app.services.inspiration_tags import (  # noqa: F401
    add_inspiration_tags,
    batch_add_tags,
    remove_inspiration_tag,
)
# ── 垃圾桶 / 删除 ──
from app.services.inspiration_trash import (  # noqa: F401
    batch_trash_inspirations,
    delete_inspiration,
    delete_rejected_inspirations,
    list_trash,
    purge_trash,
    restore_inspiration,
    trash_inspiration,
)
# ── 状态机（含私有函数：scraper_service 仍从此导入 _mark_trashed / _resolve_trash_reason） ──
from app.services.inspiration_state import (  # noqa: F401
    _assert_trash_transition,
    _mark_restored,
    _mark_trashed,
    _resolve_trash_reason,
    verify_trash_invariants,
)
# ── 去重 ──
from app.services.inspiration_dedupe import find_duplicate_by_hash  # noqa: F401
