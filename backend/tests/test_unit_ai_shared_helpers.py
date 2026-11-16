"""`routers/ai_shared.py` 剩余辅助函数测试（task-9 之后）。

单图分析的 API 进程内存队列与 `_run_analysis` 已删除（改为 worker 任务队列），
本文件只覆盖保留的两个工具函数：

- `_update_env_file`：新增键 / 覆盖已有键，且不动其它配置。用 monkeypatch 把
  `Path` 指到临时目录，**绝不写真实 backend/.env**；
- `_format_size`：0 / 字节 / KB / MB / GB / TB 的换算边界。
"""

import pytest

from app.routers import ai_shared


async def test_update_env_file_creates_and_replaces_keys(tmp_path, monkeypatch):
    """覆盖已有键 + 追加新键，且保留其它行。"""
    # _update_env_file 内部用 Path(__file__).parent.parent.parent / ".env"；
    # 替换 Path 返回 tmp/a/b/c，让三级 parent 恰好落到 tmp_path，从而只写临时文件。
    fake_base = tmp_path / "a" / "b" / "c"
    monkeypatch.setattr(ai_shared, "Path", lambda _file: fake_base)
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\nOTHER=keep\n", encoding="utf-8")

    await ai_shared._update_env_file({"EXISTING": "2", "NEW_KEY": "值"})

    content = env_path.read_text(encoding="utf-8")
    assert "EXISTING=2" in content
    assert content.count("EXISTING=") == 1
    assert "OTHER=keep" in content
    assert "NEW_KEY=值" in content


async def test_update_env_file_from_empty(tmp_path, monkeypatch):
    """文件不存在时从零创建。"""
    fake_base = tmp_path / "a" / "b" / "c"
    monkeypatch.setattr(ai_shared, "Path", lambda _file: fake_base)

    await ai_shared._update_env_file({"ONLY": "x"})

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "ONLY=x\n"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (512, "512.0 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**4, "1.0 TB"),
        (1024**5, "1024.0 TB"),  # 超出单位表后按最大单位继续放大（既有行为）
    ],
)
def test_format_size(size, expected):
    assert ai_shared._format_size(size) == expected
