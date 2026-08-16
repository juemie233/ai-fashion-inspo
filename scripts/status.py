"""查看各服务健康状态（后端 / 前端 / worker + 资源占用）。

用法:
    python scripts/status.py

数据来源（优先级）：
1. 后端 ``GET /api/health/services``（权威：后端能探测前端与 worker 心跳）；
2. 后端不可用时回退读 ``logs/service_status.json``（supervisor 每轮落盘）。
"""

import io
import json
import sys
import urllib.request
from pathlib import Path

# UTF-8 输出：保证中文在 Windows 控制台（配合 chcp 65001）正确显示
try:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = PROJECT_ROOT / "logs" / "service_status.json"
API_URL = "http://127.0.0.1:18888/api/health/services"

# 状态 → 中文展示
_STATUS_LABEL = {
    "ok": "✅ 正常",
    "down": "❌ 停止",
    "unhealthy": "⚠️ 异常",
    "starting": "⏳ 启动中",
}
_SERVICE_LABEL = {"backend": "后端", "frontend": "前端", "worker": "worker"}


def _fetch_api() -> dict | None:
    try:
        req = urllib.request.Request(API_URL)
        with urllib.request.urlopen(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_file() -> dict | None:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def render(data: dict, source: str) -> None:
    services = data.get("services", {})
    resources = data.get("resources", {})
    alerts = data.get("alerts", [])

    print("==============================================")
    print("  服务健康状态")
    print(f"  数据来源: {'后端 API' if source == 'api' else 'supervisor 状态文件'}")
    if data.get("updated_at") or data.get("checked_at"):
        print(f"  更新时间: {data.get('checked_at') or data.get('updated_at')}")
    print("==============================================")

    print("\n── 服务 ──")
    for key, label in _SERVICE_LABEL.items():
        svc = services.get(key, {})
        status = svc.get("status", "down")
        line = f"  {label:<8} {_STATUS_LABEL.get(status, status)}"
        if key == "frontend" and svc.get("latency_ms") is not None:
            line += f"  ({svc['latency_ms']}ms)"
        if key == "worker" and svc.get("count"):
            line += f"  (存活 {svc['count']} 个)"
        if svc.get("pid"):
            line += f"  [PID {svc['pid']}]"
        print(line)

    print("\n── 资源 ──")
    disk = resources.get("disk", {})
    if disk:
        print(
            f"  磁盘: 已用 {disk.get('used_percent', 0):.1f}%  "
            f"({_fmt_size(disk.get('used_bytes'))} / {_fmt_size(disk.get('total_bytes'))})"
        )
    mem = resources.get("memory")
    if mem:
        print(
            f"  内存: 已用 {mem.get('used_percent', 0):.1f}%  "
            f"({_fmt_size(mem.get('total_bytes'))} 总量)"
        )
    logs = resources.get("logs", {})
    if logs:
        print(f"  日志目录: {_fmt_size(logs.get('total_bytes'))}（{logs.get('dir', '-')}）")

    if alerts:
        print("\n── 告警 ──")
        for a in alerts:
            print(f"  ⚠️  {a}")
    else:
        print("\n── 告警 ──\n  无")

    print("\n==============================================")


def main() -> int:
    data = _fetch_api()
    source = "api"
    if data is None:
        data = _fetch_file()
        source = "file"
    if data is None:
        print("无法获取健康状态：后端未运行且无 supervisor 状态文件。")
        print("请先启动服务：bash scripts/ensure-services.sh")
        return 1
    render(data, source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
