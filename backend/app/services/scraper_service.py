"""采集服务（兼容薄壳）：实现按领域拆分至 ``services/scraper/`` 包。

- ``scraper/process.py``：Chrome/CDP 检测、采集子进程启动/自动续采/取消信号
- ``scraper/tasks.py``：采集任务 CRUD / 取消 / 重试 / 日志 / 采集源状态
- ``scraper/schedules.py``：定时采集计划 CRUD 与到期执行
- ``scraper/cookies.py``：平台 Cookie 导入 / 状态 / 删除
- ``scraper/extension.py``：浏览器插件会话任务记录
- ``scraper/results.py``：统计看板 / 任务结果列表 / 结果批量移入垃圾桶

本文件保留 ``from app.services.scraper_service import ...`` 的既有引用路径
（含 ``_check_cdp`` / ``_scraper_pids`` 等内部符号），仅做再导出。
"""

from app.services.scraper import *  # noqa: F401,F403
from app.services.scraper import __all__  # noqa: F401
