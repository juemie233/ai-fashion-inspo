"""f2 应用模块的 monkeypatch 辅助。

实现已迁到 ``app.services.f2.*``；``scripts/import_f2_downloads`` 只是旧路径转发壳。
monkeypatch 必须同时覆盖**实现模块**与旧转发壳，才等价于拆分前单模块里的全局替换。
"""

from app.services.f2 import (
    apply as f2_apply,
    collects as f2_collects,
    common as f2_common,
    fetch as f2_fetch,
    hash_cache as f2_hash_cache,
    plan as f2_plan,
    report as f2_report,
    rollback as f2_rollback,
)
from app.services import f2 as f2_app
from scripts import f2_cli
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
    f2_collects,
)


def patch_f2(monkeypatch, name: str, value) -> None:
    """把 f2 脚本里的全局名字 ``name`` 替换为 ``value``（转发壳 + 所有引用它的子模块）。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
        name: 目标全局名，如 ``"f2_available"``、``"DEFAULT_F2_DIR"``。
        value: 替换值。
    """
    monkeypatch.setattr(f2, name, value)
    monkeypatch.setattr(f2_app, name, value)
    for module in _SUBMODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
