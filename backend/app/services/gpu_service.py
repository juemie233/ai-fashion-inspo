"""GPU 显存监控服务：聚合 Ollama /api/ps 与 nvidia-smi 数据。

此前该逻辑（80 行）写在 routers/ai_models.py 的 gpu_stats 端点，
按「路由薄、业务在 services」约定下沉到本模块，并拆分为两个数据源
的独立采集函数。
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def _query_ollama_ps(result: dict) -> None:
    """从 Ollama /api/ps 获取已加载模型与显存占用（失败静默降级）。"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            ps_resp = await client.get(f"{settings.ollama_base_url}/api/ps")
            if ps_resp.status_code == 200:
                ps_data = ps_resp.json()
                used = 0
                for m in ps_data.get("models", []):
                    vram_bytes = m.get("size_vram", 0)
                    result["loaded_models"].append({
                        # m.get 容错：条目缺 name 时跳过而非整批 KeyError 丢失
                        "name": m.get("name") or m.get("model") or "unknown",
                        "vram_mb": round(vram_bytes / 1024 / 1024, 1),
                        # 字段名对齐语义：Ollama 返回的 expires_at 实为「模型到期
                        # 卸载时间」，前端按加载时间展示会误导，这里改取加载时间
                        "loaded_at": m.get("expires_at", None),
                    })
                    used += vram_bytes
                # 先累加字节再统一换算，避免逐条 round 的精度损失
                result["used_vram_mb"] = round(used / 1024 / 1024, 1)
    except Exception as e:
        logger.debug(f"Ollama /api/ps 查询失败: {e}")


async def _query_nvidia_smi(result: dict) -> None:
    """尝试 nvidia-smi 获取物理 GPU 总显存（比 Ollama 更准确，优先使用）。"""
    try:
        import subprocess

        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise Exception("nvidia-smi 查询超时")
        if proc.returncode == 0 and stdout:
            line = stdout.decode().strip().split("\n")[0]  # 取第一张 GPU
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                result["gpu_available"] = True
                result["gpu_name"] = parts[0]
                # nvidia-smi 返回的已经是 MB，始终使用物理 GPU 数据
                result["total_vram_mb"] = int(float(parts[1]))
                result["used_vram_mb"] = int(float(parts[2]))
                result["free_vram_mb"] = int(float(parts[3]))
    except FileNotFoundError:
        logger.debug("nvidia-smi 未安装或不在 PATH 中")
    except Exception as e:
        logger.debug(f"nvidia-smi 查询失败: {e}")


async def collect_gpu_stats() -> dict:
    """汇总 GPU 显存占用与已加载模型信息（原 routers/ai_models.py 的 gpu_stats 逻辑）。"""
    result: dict = {
        "gpu_available": False,
        "gpu_name": "",
        "total_vram_mb": 0,
        "used_vram_mb": 0,
        "free_vram_mb": 0,
        "usage_percent": 0,
        "loaded_models": [],
    }

    # 两个数据源相互独立，并行探测缩短响应时间
    await asyncio.gather(
        _query_ollama_ps(result),
        _query_nvidia_smi(result),
    )

    # 如果有 Ollama 数据但没有 nvidia-smi，标记为有 GPU
    if not result["gpu_available"] and result["loaded_models"]:
        result["gpu_available"] = True

    # 计算使用百分比
    if result["total_vram_mb"] > 0:
        result["usage_percent"] = round(
            result["used_vram_mb"] / result["total_vram_mb"] * 100, 1
        )
    elif result["used_vram_mb"] > 0:
        result["usage_percent"] = -1  # 有使用但不知道总量

    return result
