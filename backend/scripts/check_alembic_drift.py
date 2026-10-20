"""门禁：ORM 模型与数据库结构必须一致（`alembic check`）。

背景：历史上多次「模型先改、迁移没跟上」，累积出类型 / NOT NULL / 索引 / 外键漂移。
每做一次 `alembic revision --autogenerate` 都会把这些无关漂移一起带进来，必须手工裁剪。
对齐一次后，这个脚本把「再出现漂移」变成显式失败，避免债重新长出来。

用法：
    cd backend && python scripts/check_alembic_drift.py

退出码：0=一致；1=有漂移（打印明细）；2=数据库不存在（跳过，不算失败，便于在无库环境跑）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DB = BACKEND / "fashion_inspo.db"


def main() -> int:
    """运行 alembic check 并返回退出码。"""
    if not DB.exists():
        print(f"[skip] 未找到数据库 {DB.name}，跳过漂移检查（无库环境不算失败）")
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if "No new upgrade operations detected" in output:
        print("[ok] ORM 与数据库结构一致")
        return 0

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
