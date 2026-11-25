"""f2 包路径装配：确保进程内导入的是**项目实际在用的那份 f2**。

背景
----
PyPI 上的 f2 最新只到 `0.0.1.7`（2024-12-31 发布），其抖音签名已失效——用**旧版**
调作品清单接口稳定返回 **HTTP 403**，即使带完全有效的登录态 Cookie。项目实际用的是
f2 工作目录（git clone）里那一份，必须把它插到 `sys.path` 最前。

为什么单独成模块
----------------
本函数原在 ``backend/scripts/report_f2_id_alignment.py``。``49c8355``（把 f2 共享实现
迁进 ``app/services/f2``）漏搬它，而 ``collects.load_f2_runtime`` 写成了相对导入
``from .report_f2_id_alignment import ...``——同包里并没有这个模块，于是点「读取收藏夹」
直接 ``ModuleNotFoundError``（2026-11-25 修复）。

依赖方向定死：**app 侧是唯一真源**，``scripts/report_f2_id_alignment.py`` 从本模块导入
并重新导出（脚本依赖 app，不反向）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_clone_f2_on_path(f2_dir: Path) -> str:
    """把 f2 工作目录插到 `sys.path` 最前，确保导入的是**项目实际在用的那份 f2**。

    ⚠ 这一步不能省（2026-09 实测踩坑）：PyPI 上的 f2 最新只到 `0.0.1.7`
    （2024-12-31 发布），其抖音签名已失效——用**旧版**调作品清单接口稳定返回
    **HTTP 403**，即使带完全有效的登录态 Cookie。而 f2 工作目录是 git clone，
    项目通过「`cwd=f2_dir` 跑 `python -m f2`」用的正是克隆里那一份。

    在**进程内** import f2（不像项目那样起子进程）时，若不明式插路径，拿到的是
    site-packages 里的旧版 → 403。插路径后需清掉已导入的旧模块，否则不生效。

    Args:
        f2_dir: f2 工作目录（内含 `f2/` 包）。

    Returns:
        实际生效的 f2 包文件路径；该目录下没有 `f2/` 包时返回空串（回退 site-packages）。
    """
    root = str(f2_dir)
    if not (f2_dir / "f2" / "__init__.py").is_file():
        return ""

    loaded = sys.modules.get("f2")
    if loaded is not None and getattr(loaded, "__file__", "").startswith(root):
        return loaded.__file__  # 已经是这一份，无需重导

    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    if loaded is not None:
        for name in [m for m in list(sys.modules) if m == "f2" or m.startswith("f2.")]:
            del sys.modules[name]

    import f2  # noqa: PLC0415 —— 必须在插路径之后导入

    return getattr(f2, "__file__", "")
