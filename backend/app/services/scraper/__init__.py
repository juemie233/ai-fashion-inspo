"""采集服务：编排采集任务、下载图片、入库、触发 AI 分析，以及采集引擎管理。

本包按领域拆分（原 scraper_service.py 1164 行）：
- ``process.py``：Chrome/CDP 检测、采集子进程启动/自动续采/取消信号
- ``tasks.py``：采集任务 CRUD / 取消 / 重试 / 日志 / 采集源状态
- ``schedules.py``：定时采集计划 CRUD 与到期执行
- ``cookies.py``：平台 Cookie 导入 / 状态 / 删除
- ``extension.py``：浏览器插件会话任务记录
- ``results.py``：统计看板 / 任务结果列表 / 结果批量移入垃圾桶

``scraper_service.py`` 保留为兼容薄壳（re-export 全部符号），既有引用不变。
"""

# 薄壳：保持 from app.services.scraper_service import ... 的引用路径不变
from app.services.scraper.process import (
    CHROME_DEBUG_CMD,
    _check_cdp,
    _launch_scraper_process,
    _maybe_auto_retry,
    _safe_launch,
    _scraper_pids,
    _scraper_retry_count,
    check_cdp,
    has_active_scraper_tasks,
)
from app.services.scraper.cookies import (
    _COOKIE_PLATFORMS,
    _validate_cookie_platform,
    delete_cookies,
    get_cookie_status,
    import_cookies,
)
from app.services.scraper.tasks import (
    cancel_scraper_task,
    clear_all_scraper_tasks,
    create_scraper_task,
    delete_single_scraper_task,
    get_scraper_sources,
    get_task_log,
    list_scraper_tasks,
    retry_failed_scraper_tasks,
    retry_single_task,
)
from app.services.scraper.schedules import (
    _SCHEDULE_PLATFORMS,
    _SCHEDULE_SORT_MODES,
    _advance_next_run,
    _build_schedule_task_config,
    _validate_schedule_platform,
    create_schedule,
    delete_schedule,
    list_schedules,
    run_due_schedules,
    run_schedule_now,
    update_schedule,
)
from app.services.scraper.extension import complete_extension_task, create_extension_task
from app.services.scraper.results import (
    batch_delete_task_results,
    get_scraper_stats,
    get_task_results,
)

# 兼容别名：_utcnow 与 app.utils.time.utcnow 实现一致（收敛历史遗留）
from app.utils.time import utcnow as _utcnow

__all__ = [
    # process
    "CHROME_DEBUG_CMD",
    "_check_cdp",
    "_launch_scraper_process",
    "_maybe_auto_retry",
    "_safe_launch",
    "_scraper_pids",
    "_scraper_retry_count",
    "check_cdp",
    "has_active_scraper_tasks",
    # cookies
    "_COOKIE_PLATFORMS",
    "_validate_cookie_platform",
    "delete_cookies",
    "get_cookie_status",
    "import_cookies",
    # tasks
    "cancel_scraper_task",
    "clear_all_scraper_tasks",
    "create_scraper_task",
    "delete_single_scraper_task",
    "get_scraper_sources",
    "get_task_log",
    "list_scraper_tasks",
    "retry_failed_scraper_tasks",
    "retry_single_task",
    # schedules
    "_SCHEDULE_PLATFORMS",
    "_SCHEDULE_SORT_MODES",
    "_advance_next_run",
    "_build_schedule_task_config",
    "_validate_schedule_platform",
    "create_schedule",
    "delete_schedule",
    "list_schedules",
    "run_due_schedules",
    "run_schedule_now",
    "update_schedule",
    # extension
    "complete_extension_task",
    "create_extension_task",
    # results
    "batch_delete_task_results",
    "get_scraper_stats",
    "get_task_results",
    # 兼容
    "_utcnow",
]
