"""scripts 包标记：采集脚本族（run_scraper + scraper_common/download/xhs/douyin）。

拆分为包内模块后使用相对导入，必须以模块方式启动：
``cd backend && python -m scripts.run_scraper <task_id>``
直接以 ``python scripts/run_scraper.py`` 执行会缺失包上下文导致 ImportError。
"""
