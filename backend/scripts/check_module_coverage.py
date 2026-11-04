"""关键模块覆盖率地板（2026-11-04 测试体系评测 P1-2）。

全局覆盖率 82% 会掩盖「关键模块低于均值也能过」：worker / 出图 / 搜索 / AI 设置
这些最近频繁改动、又直接触达用户的模块必须单独设地板。脚本读取当前目录的
``.coverage``（test.sh 第 1 步 pytest 生成），逐模块检查，低于地板直接退出 1。

地板值取 2026-11-04 全量实测值（只降不升；以后补测试再往上调）：
- worker.py 77.5%
- generation_service.py 77.6%
- seedream_client.py 76.5%
- search.py 43.4%
- ai_settings.py 45.5%
- ai_quality.py 52.5%
"""

from __future__ import annotations

from pathlib import Path

import coverage

# 模块相对 backend/ 的路径 → 覆盖率地板（百分数，保留一位小数）
MODULE_FLOORS: dict[str, float] = {
    "app/worker.py": 77.5,
    "app/services/generation_service.py": 77.6,
    "app/services/seedream_client.py": 76.5,
    "app/routers/search.py": 43.4,
    "app/routers/ai_settings.py": 45.5,
    "app/routers/ai_quality.py": 52.5,
}


def module_percent(cov: coverage.Coverage, path: Path) -> float:
    """返回单个文件的语句覆盖率；文件无语句时按 100 处理。"""
    _filename, statements, _excluded, missing, _missing_text = cov.analysis2(str(path))
    total = len(statements)
    if total == 0:
        return 100.0
    return (total - len(missing)) / total * 100


def main() -> int:
    cov = coverage.Coverage()
    try:
        cov.load()
    except coverage.CoverageException as exc:
        print(f"[FAIL] 读取 .coverage 失败：{exc}（请先跑 pytest --cov）")
        return 2

    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    print("关键模块覆盖率地板：")
    for relative, floor in MODULE_FLOORS.items():
        path = root / relative
        percent = module_percent(cov, path)
        status = "OK" if percent + 1e-9 >= floor else "FAIL"
        print(f"  [{status}] {relative:48s} {percent:6.2f}%  地板 {floor:5.1f}%")
        if status == "FAIL":
            failures.append(f"{relative}: {percent:.2f}% < {floor:.1f}%")

    if failures:
        print("\n[FAIL] 以下关键模块低于覆盖率地板：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n[ok] 关键模块覆盖率地板全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
