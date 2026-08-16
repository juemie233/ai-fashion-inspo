"""系统资源统计工具：磁盘 / 内存占用查询（供健康检查与告警使用）。

不引入 psutil 等额外依赖：
- 磁盘：标准库 shutil.disk_usage
- 内存：Windows 用 ctypes 调 GlobalMemoryStatusEx，非 Windows 返回 None（跳过）
"""

import ctypes
import shutil
import sys
from pathlib import Path


def disk_usage(path: Path | str) -> dict:
    """返回指定路径所在磁盘分区的使用情况。

    Args:
        path: 目标路径（只需存在，用于定位磁盘分区）。

    Returns:
        含 total/used/free 字节数与 used_percent 的字典。
    """
    usage = shutil.disk_usage(str(path))
    total = usage.total
    used = usage.used
    return {
        "path": str(path),
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": usage.free,
        "used_percent": round(used / total * 100, 1) if total else 0.0,
    }


class _MEMORYSTATUSEX(ctypes.Structure):
    """Windows GlobalMemoryStatusEx 结构体（物理内存 + 分页文件）。"""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_usage() -> dict | None:
    """返回物理内存使用情况；非 Windows 平台返回 None（跳过内存统计）。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None
        return {
            "used_percent": round(float(stat.dwMemoryLoad), 1),
            "total_bytes": stat.ullTotalPhys,
            "available_bytes": stat.ullAvailPhys,
        }
    except Exception:
        return None
