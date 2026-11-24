"""存储完整性 Rust sidecar 调用（可选）。

`storage-audit` 是独立只读 CLI：输入数据库 expected 路径清单，扫描
images/thumbnails/videos，返回缺失/孤立/大小不一致。未配置二进制、超时或
失败时返回 None，由 `admin_stats_service` 回退原 Python 扫描实现。
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def find_storage_audit_binary() -> Path | None:
    """从 MEDIA_WORKER_BIN 同目录查找 storage audit 二进制。

    约定：media-worker.exe 与 storage_audit.exe 一起构建/部署；不单独新增配置项。
    支持 Windows 的 storage_audit.exe 和 Rust 默认生成的 storage_audit。
    """
    configured = (settings.media_worker_bin or "").strip()
    if not configured:
        return None
    worker = Path(configured).expanduser()
    for name in ("storage_audit.exe", "storage_audit", "storage-audit.exe", "storage-audit"):
        candidate = worker.with_name(name)
        if candidate.is_file():
            return candidate
    return None


async def run_storage_audit(expected_paths: list[str]) -> dict | None:
    """调用 Rust 扫描；未配置/失败返回 None。

    Returns:
        Rust 报告的 dict（missing / orphans / ...），调用方负责映射业务字段。
    """
    binary = find_storage_audit_binary()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory(
        prefix="storage-audit-", dir=settings.storage_root
    ) as temp_dir:
        temp_path = Path(temp_dir)
        manifest_path = temp_path / "manifest.json"
        report_path = temp_path / "report.json"
        manifest_path.write_text(
            json.dumps(
                {"expected": [{"path": path} for path in expected_paths]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            str(binary),
            str(settings.storage_root),
            str(manifest_path),
            "--dirs",
            "images,thumbnails,videos",
            "--output",
            str(report_path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.media_worker_audit_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("Rust storage-audit 超时，回退 Python 完整性检查")
            return None
        if process.returncode != 0:
            logger.warning(
                "Rust storage-audit 失败（exit %s），回退 Python：%s",
                process.returncode,
                stderr.decode("utf-8", errors="replace")[:500],
            )
            return None
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Rust storage-audit 报告无法解析，回退 Python：%s", exc)
            return None
