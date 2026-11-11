"""门禁：ORM 模型与数据库结构必须一致（`alembic check`）。

背景：历史上多次「模型先改、迁移没跟上」，累积出类型 / NOT NULL / 索引 / 外键漂移。
每做一次 `alembic revision --autogenerate` 都会把这些无关漂移一起带进来，必须手工裁剪。
对齐一次后，这个脚本把「再出现漂移」变成显式失败，避免债重新长出来。

用法：
    cd backend && python scripts/check_alembic_drift.py

    # CI / 无真实库的环境：先用临时库建表，再检查（两处必须指向同一个库）
    ALEMBIC_DB_URL=sqlite:////tmp/ci.db python -m alembic upgrade head
    ALEMBIC_DB_URL=sqlite:////tmp/ci.db python scripts/check_alembic_drift.py

退出码：0=一致或跳过（无库环境）；1=有漂移（打印明细）。
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def _resolve_db() -> Path | None:
    """本次要检查的 SQLite 文件；不是文件库（内存库/非 SQLite）时返回 None。

    定位口径与 ``alembic/env.py`` 保持一致，两处必须指向同一个库，否则会出现
    「迁移建的是 A 库、检查的是 B 库」这种假绿：

    1. ``ALEMBIC_DB_URL``（CI / 临时库 / 排查时显式指定）；
    2. 否则取 app 配置的 ``database_url``（本地真实库）。
    """
    from sqlalchemy.engine import make_url

    raw = os.environ.get("ALEMBIC_DB_URL", "").strip()
    if not raw:
        sys.path.insert(0, str(BACKEND))
        from app.config import settings

        raw = settings.database_url
    try:
        url = make_url(raw)
    except Exception:  # noqa: BLE001 —— URL 都解析不了就没法检查，按跳过处理
        return None
    if not url.get_backend_name().startswith("sqlite"):
        return None
    if not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path if path.is_absolute() else (BACKEND / path).resolve()


def _check_partial_indexes(db: Path) -> list[str]:
    """对账 ORM 部分索引的 WHERE 谓词（alembic check 不比较 sqlite_where）。

    2026-11-06 数据库审查 P1-1：历史库版本是 head，但
    ix_inspirations_source_platform_id 仍是全量唯一；`alembic check` 认为一致，
    只有直接读 sqlite_master 才能发现。返回问题列表（空=通过）。
    """
    sys.path.insert(0, str(BACKEND))
    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.database import Base

    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    problems: list[str] = []
    try:
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                where = index.dialect_options["sqlite"].get("where")
                if where is None:
                    continue
                row = con.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (index.name,),
                ).fetchone()
                actual = " ".join((row[0] or "").split()).upper() if row else ""
                expected = " ".join(str(where).split()).upper()
                if not actual:
                    problems.append(f"缺少部分索引 {index.name}（表 {table.name}）")
                elif "WHERE" not in actual or expected not in actual:
                    problems.append(
                        f"部分索引 {index.name} 谓词不一致：库中 `{actual}`，"
                        f"ORM 要求 `{expected}`"
                    )
    finally:
        con.close()
    return problems


def main() -> int:
    """运行 alembic check 并返回退出码。"""
    db = _resolve_db()
    if db is None:
        print("[skip] 目标不是 SQLite 文件库，跳过漂移检查")
        return 0
    if not db.exists():
        print(
            f"[skip] 未找到数据库 {db.name}，跳过漂移检查（无库环境不算失败；"
            "CI 请先 `alembic upgrade head` 建库）"
        )
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if "No new upgrade operations detected" in output:
        partial_problems = _check_partial_indexes(db)
        if partial_problems:
            print("[fail] ORM 与数据库的部分索引谓词不一致：")
            for problem in partial_problems:
                print(f"  - {problem}")
            print(
                "\n修复：执行 `python -m alembic upgrade head` 跑 w3x4y5z6a7b8 迁移，"
                "或重启后端让 ensure_schema 的索引对账执行。"
            )
            return 1
        print(f"[ok] ORM 与数据库结构一致（{db.name}，含部分索引谓词）")
        return 0

    if "not up to date" in output:
        # 库落后于迁移链：这时 alembic 根本不做比对，报「漂移」会误导人
        print(
            "[fail] 库结构落后于迁移链（alembic check 未做比对）："
            "先 `python -m alembic upgrade head`，或重启后端让启动迁移跑完"
        )
        return 1

    print("[fail] ORM 与数据库结构存在漂移，先生成并裁剪迁移再提交：")
    for line in output.splitlines():
        if "Detected" in line:
            print("  -", line.split("] ", 1)[-1])
    print(
        "\n提示：`alembic revision --autogenerate` 会把所有漂移一起写进迁移，"
        "请只保留本次相关改动（历史上就是这样累积出债的）。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
