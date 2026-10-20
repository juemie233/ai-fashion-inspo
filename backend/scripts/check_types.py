"""门禁：mypy 类型检查（**棘轮模式**：只覆盖已清零的模块，列表只增不减）。

背景：全仓 `mypy app` 目前有 370 个错误 / 81 个文件（2026-10-20 实测），一次清零不现实；
而 `pyproject.toml` 里的 `disallow_untyped_defs = True` / `类型注解必须`（CLAUDE.md）
就成了纸面约定——从来没有一步真正跑过 mypy。

做法与 eslint 告警棘轮（`--max-warnings 73`）同一思路：**先把已经清零的模块锁住**，
不让新代码把债再加回去；以后每修好一个模块，就往 ``RATCHET`` 里加一行。列表只增不减。

``RATCHET`` 里目前基本是 f2 / crop 拆分产出的新模块：拆分时就顺手补齐了注解，属于
「新代码必须干净」的自然结果。

用法：
    cd backend && python scripts/check_types.py          # 棘轮（CI / scripts/test.sh 跑的就是这个）
    cd backend && python scripts/check_types.py --all    # 全仓诊断（预期有存量错误，仅排查用）

退出码：0=通过；1=有类型错误。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# 棘轮清单：只增不减。加新模块前先确保它自己 `mypy --follow-imports=silent` 是干净的。
RATCHET: tuple[str, ...] = (
    "app/services/task_runners/f2_library_ids.py",
    "app/services/task_runners/f2_progress.py",
    "app/services/task_runners/f2_personal.py",
    "app/services/task_runners/f2_fetch_stages.py",
    "app/services/task_runners/f2_plan_apply.py",
    "app/services/task_runners/f2_finalize.py",
    "app/services/task_runners/f2_import.py",
    "app/services/crop_common.py",
    "app/services/crop_scan.py",
)


def main() -> int:
    """跑 mypy：默认只查棘轮清单，``--all`` 查全仓。"""
    full = "--all" in sys.argv[1:]
    targets = ["app"] if full else list(RATCHET)
    # --follow-imports=silent：只报 targets 里的错误，不让被依赖模块的存量错误淹没信号
    args = [sys.executable, "-m", "mypy", "--follow-imports=silent", *targets]
    result = subprocess.run(args, cwd=str(BACKEND), text=True)
    if result.returncode == 0:
        scope = "全仓" if full else f"棘轮清单 {len(targets)} 个模块"
        print(f"[ok] mypy 通过（{scope}）")
        return 0
    print(
        "\n[fail] 类型检查未通过。棘轮清单里的模块必须保持干净；"
        "若报错来自 `--all`，那是存量债（见 docs/代码检查.md「类型检查」一节）。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
