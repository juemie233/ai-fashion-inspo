"""日志可见性回归：迁移不能禁用已有 logger（否则 500 的 traceback 永远不会落盘）。

背景（2026-09-27 排查「素材 4ee7f266 没有相似推荐」时踩到）：
`alembic/env.py` 调 `fileConfig(config.config_file_name)` 时没传
`disable_existing_loggers=False`，而 `fileConfig()` 默认值是 **True**——它会禁用所有
未在 `alembic.ini` 里声明的 logger（该文件只声明了 root / sqlalchemy.engine /
alembic）。应用是在服务进程启动时跑迁移的（`database.run_migrations`），于是迁移一跑完：

- `uvicorn.access` 被禁用 → **请求日志一行都没有**；
- `uvicorn.error` 被禁用 → `Application startup complete.` 与
  `Exception in ASGI application`（含 traceback）全部消失；
- `app.*` 被禁用 → 业务日志静默。

结果就是：接口稳定 500，而 `logs/backend.log` 里连请求行都查不到，只能靠在进程内
复现才定位到根因。「能不能看见错误」本身也是需要被用例守住的能力。
"""

import logging

from app.database import run_migrations


def test_run_migrations_keeps_existing_loggers_enabled():
    """跑一遍迁移后，已有 logger 必须仍是启用状态（disabled=False）。"""
    probes = [
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("app.database"),
        logging.getLogger("sqlalchemy.engine"),
    ]
    for probe in probes:
        probe.disabled = False

    run_migrations()

    disabled = [p.name for p in probes if p.disabled]
    assert not disabled, (
        f"迁移禁用了这些 logger: {disabled}——"
        f"请求日志与 500 的 traceback 会从此消失（检查 alembic/env.py 的 fileConfig "
        f"是否传了 disable_existing_loggers=False）"
    )
