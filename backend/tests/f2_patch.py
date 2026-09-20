"""f2 导入脚本的 monkeypatch 辅助（配合 scripts/import_f2_downloads.py 的模块拆分）。

``scripts/import_f2_downloads`` 现在只是**转发壳**：符号真正归属在 ``scripts.f2_*``
子模块里，子模块内部的调用走的是**自己命名空间**里的名字。所以 monkeypatch 必须
同时覆盖两处，才等价于拆分前单模块里的全局替换：

1. 转发壳（``scripts.import_f2_downloads``）——app 侧代码通过 ``f2.X`` 读取时生效；
2. 所有**引用了该名字**的子模块——子模块内部的调用点生效。

判定「引用了该名字」用 ``hasattr``：子模块只要读过这个名字（自己定义或从兄弟模块
导入），其命名空间里就有它。
"""

from scripts import (
    f2_apply,
    f2_cli,
    f2_common,
    f2_fetch,
    f2_hash_cache,
    f2_plan,
    f2_report,
    f2_rollback,
)
from scripts import import_f2_downloads as f2

_SUBMODULES = (
    f2_apply,
    f2_cli,
    f2_common,
    f2_fetch,
    f2_hash_cache,
    f2_plan,
    f2_report,
    f2_rollback,
)


def patch_f2(monkeypatch, name: str, value) -> None:
    """把 f2 脚本里的全局名字 ``name`` 替换为 ``value``（转发壳 + 所有引用它的子模块）。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
        name: 目标全局名，如 ``"f2_available"``、``"DEFAULT_F2_DIR"``。
        value: 替换值。
    """
    monkeypatch.setattr(f2, name, value)
    for module in _SUBMODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
