"""采集服务：编排和管理采集任务的生命周期。Phase 4 完整实现。"""

import logging

logger = logging.getLogger(__name__)


async def run_scraper_task(task_id: int):
    """
    执行采集任务：根据任务配置调用对应平台的爬虫。

    流程：
    1. 从数据库加载任务配置
    2. 初始化对应平台的爬虫
    3. 执行搜索/发现
    4. 下载图片 → 入库 → 触发 AI 分析
    5. 更新任务状态

    Phase 4 完整实现。
    """
    logger.info(f"采集任务 {task_id} 执行 — Phase 4 实现")
