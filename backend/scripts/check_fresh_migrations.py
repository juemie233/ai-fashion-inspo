"""门禁：迁移链必须能从**空库**建出与 ORM 一致的库结构（灾备恢复路径自检）。

为什么单独一个门禁：本地与线上建库走的是 `create_all` + `stamp head`（见 app/database.py），
迁移链平时根本不执行，所以「空库 upgrade head 会炸」这类问题平时发现不了——而它断的
正是「换机器 / 恢复服务器后按迁移建库」这条路。2026-10-20 实测发现两处：

1. 链尾的对齐迁移假定「从漂移老库升级」，在空库上撞 `index ... already exists`，
   整条链断在最后一条（已改为先 inspect 再改，幂等）；
2. 修好之后又暴露：空库建出来的库比真实库**松散**——缺 12 处 NOT NULL 与
   `bloggers.xhs_id` / `models.xhs_id` 的唯一索引（已由 d1b2c3d4e5f6 补齐）。

做法：临时目录建空库 → `ALEMBIC_DB_URL` 指向它 → `alembic upgrade head` → `alembic check`
→ 删库。**全程不碰真实库**；真实库的漂移检查是 `check_alembic_drift.py`（两者互补：
一个查「链能不能从零建」，一个查「现有库与模型是否一致」）。

用法：
    cd backend && python scripts/check_fresh_migrations.py

退出码：0=通过；1=迁移链跑不通或结构与 ORM 不一致。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _run(args: list[str], db_url: str) -> subprocess.CompletedProcess[str]:
    """在 backend 目录下执行 alembic 子命令，指向指定的临时库。"""
    env = {**os.environ, "ALEMBIC_DB_URL": db_url}
    return subprocess.run(
        args, cwd=str(BACKEND), capture_output=True, text=True, env=env
    )


def main() -> int:
    """建临时空库、跑完整迁移链，并校验结构与 ORM 一致。"""
    tmp = Path(tempfile.mkdtemp(prefix="fresh_migrations_"))
    db = tmp / "fresh.db"
    # 同步驱动：alembic/env.py 把这个 URL 直接交给同步引擎（见该文件注释）
    url = f"sqlite:///{db.as_posix()}"
    try:
        upgrade = _run([sys.executable, "-m", "alembic", "upgrade", "head"], url)
        if upgrade.returncode != 0:
            print("[fail] 空库 `alembic upgrade head` 失败——迁移链无法从零建库：")
            print((upgrade.stdout or "") + (upgrade.stderr or ""))
            return 1

        check = _run([sys.executable, "-m", "alembic", "check"], url)
        output = (check.stdout or "") + (check.stderr or "")
        if "No new upgrade operations detected" in output:
            print("[ok] 迁移链可从空库建出与 ORM 一致的结构")
            return 0

        print("[fail] 空库建出来的结构与 ORM 不一致（历史迁移漏了约束，补一条对齐迁移）：")
        for line in output.splitlines():
            if "Detected" in line:
                print("  -", line.split("] ", 1)[-1])
        print("\n提示：对齐迁移必须幂等（先 sa.inspect 再改），否则空库会撞「已存在」。")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
