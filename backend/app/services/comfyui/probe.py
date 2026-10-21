"""就绪探测：ComfyUI 是否可用、模板依赖的模型是否就位、Ollama 占着多少显存。

前端的「运行」按钮该不该亮，取决于这里；用户点下去才失败是最差的体验。
探测必须**快**（2 秒超时）且**可降级**：ComfyUI 没启动时接口要正常返回
``ready=false``，而不是把整个页面拖死或抛 500。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.services.comfyui import workflow as wf
from app.services.comfyui.client import ComfyUIClient, ComfyUIError

logger = logging.getLogger(__name__)

"""探测用的短超时：UI 每次打开都会调，不能等 10 秒。"""
PROBE_TIMEOUT = 2.0

"""模型档位定义。

- ``workflow``：模板名（对应 ``workflows/<name>.json``）
- ``vram_gib``：出图所需显存（**取实测/保守峰值**，不动态算 —— 算不准还不如早判）
- ``defaults``：前端填默认值用
"""

MODEL_SPECS: dict[str, dict] = {
    "z_image_int8": {
        "label": "Z-Image-Turbo int8（快，1024² 约 6 秒）",
        "workflow": "z_image_t2i",
        "kind": "t2i",
        "vram_gib": 15.0,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 8,
            "cfg": 1.0,
            "sampler": "res_multistep",
            "scheduler": "simple",
            "shift": 3.0,
        },
        "max_pixels": 1328 * 1328,
    },
    "qwen_2_1_i2i": {
        "label": "Qwen-Image 2.1（参考图改款，1024² 约 20~45 秒）",
        "workflow": "qwen_edit",
        "kind": "i2i",
        "vram_gib": 14.5,
        "max_refs": 10,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 25,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "shift": 3.0,
        },
        # Qwen-Image 2.1 在 16 GB 上 1024² 已是上限（权重 13.6 GiB + 激活）
        "max_pixels": 1024 * 1024,
    },
}


@dataclass
class ProbedStatus:
    """探测结果（序列化后直接给前端）。"""

    ready: bool = False
    reason: str | None = None
    comfyui: dict = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    ollama_resident: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "comfyui": self.comfyui,
            "models": self.models,
            "ollama_resident": self.ollama_resident,
        }


async def ollama_resident() -> list[dict]:
    """当前驻留的 Ollama 模型（失败返回空列表：它只是诊断信息，不能影响出图判断）。"""
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/ps")
            if resp.status_code != 200:
                return []
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 —— 诊断信息，失败静默
        logger.debug(f"读取 Ollama 驻留模型失败（忽略）: {exc}")
        return []
    out: list[dict] = []
    for item in data.get("models") or []:
        size = int(item.get("size_vram") or item.get("size") or 0)
        out.append({"name": item.get("name") or item.get("model") or "?", "size_gib": round(size / 2**30, 2)})
    return out


def _vram_gib(stats: dict) -> tuple[float, float]:
    """从 /system_stats 取 (total, free)，单位 GiB。"""
    devices = stats.get("devices") or []
    dev = devices[0] if devices else {}
    total = float(dev.get("vram_total") or 0) / 2**30
    free = float(dev.get("vram_free") or 0) / 2**30
    return round(total, 2), round(free, 2)


async def probe(client: ComfyUIClient | None = None) -> ProbedStatus:
    """探测 ComfyUI 可用性与模型就位情况。

    Args:
        client: 可注入（测试用）；缺省按配置新建。
    """
    client = client or ComfyUIClient(settings.comfyui_base_url, timeout=PROBE_TIMEOUT)
    status = ProbedStatus()
    try:
        stats = await client.system_stats()
    except ComfyUIError as exc:
        status.reason = (
            "ComfyUI 未启动：双击 D:\\AI\\ComfyUI\\run_nvidia_gpu.bat 后重试"
            if exc.kind == "not_running"
            else f"ComfyUI 不可用：{exc}"
        )
        status.models = {key: "unknown" for key in MODEL_SPECS}
        status.ollama_resident = await ollama_resident()
        return status

    total, free = _vram_gib(stats)
    system = stats.get("system") or {}
    status.comfyui = {
        "version": system.get("comfyui_version"),
        "url": settings.comfyui_base_url,
        "vram_total_gib": total,
        "vram_free_gib": free,
    }

    # 模型自检：只看模板依赖的那几个文件在不在 ComfyUI 的下拉选项里
    try:
        info = await client.object_info()
    except ComfyUIError as exc:
        status.reason = f"读取 ComfyUI 节点信息失败：{exc}"
        status.models = {key: "unknown" for key in MODEL_SPECS}
        return status

    missing_all: list[str] = []
    for key, spec in MODEL_SPECS.items():
        try:
            template, _digest = wf.load_template(spec["workflow"])
        except FileNotFoundError:
            status.models[key] = "missing_workflow"
            missing_all.append(f"{key}: 模板文件缺失")
            continue
        missing_nodes = wf.missing_nodes(template, info)
        if missing_nodes:
            status.models[key] = "missing_nodes"
            missing_all.append(f"{key}: 缺少节点 {', '.join(missing_nodes)}")
            continue
        missing_models = wf.check_models(template, info)
        if missing_models:
            status.models[key] = "missing_model"
            missing_all.append(f"{key}: 模型文件未就位（{'; '.join(missing_models)}）")
            continue
        status.models[key] = "ok"

    status.ready = any(v == "ok" for v in status.models.values())
    if not status.ready:
        status.reason = "；".join(missing_all) or "没有可用的出图模型"
    status.ollama_resident = await ollama_resident()
    return status
