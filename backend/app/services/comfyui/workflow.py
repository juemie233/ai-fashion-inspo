"""工作流模板：加载、占位符注入、版本指纹、节点自检。

为什么要模板化而不是让前端拼 JSON：

- 前端拼 JSON = 把 ComfyUI 的节点参数契约泄漏到 UI，改一个节点名要发前端版本；
- 模板随代码走，**改模板 = 改后端**，能进 code review、能跑单测、能记 hash；
- 模板里用占位符（``__SEED__`` 等），注入时做类型检查（该是 int 的不能是字符串），
  避免「CFG 传成 "1.0" 字符串」这类只有跑起来才发现的错误。

ComfyUI 升级导致节点改名是本方案最现实的风险来源，所以 :func:`missing_nodes`
配合 ``/object_info`` 在**启动时**做一次自检（而不是等用户点出图才炸）。
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

WORKFLOW_DIR = Path(__file__).parent / "workflows"

"""占位符形式：``__NAME__``（大写字母+下划线）。"""
_PLACEHOLDER_PREFIX = "__"
_PLACEHOLDER_SUFFIX = "__"


def template_path(name: str) -> Path:
    """模板文件路径（``name`` 不含扩展名）。"""
    return WORKFLOW_DIR / f"{name}.json"


def load_template(name: str) -> tuple[dict, str]:
    """加载模板，返回 ``(工作流 dict, 指纹)``。

    指纹是文件内容 sha256 的前 16 位：写进 ``generation_runs.workflow_hash``，
    将来用户反馈「同一 prompt 出图变了」时，能先确认是不是模板改过。

    Raises:
        FileNotFoundError: 模板不存在（部署漏文件）。
    """
    path = template_path(name)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return json.loads(raw.decode("utf-8")), digest


def _is_placeholder(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_PLACEHOLDER_PREFIX)
        and value.endswith(_PLACEHOLDER_SUFFIX)
        and len(value) > 4
    )


def _substitute(
    value: Any, params: dict[str, Any], used: set[str], depth: int = 0
) -> Any:
    """递归替换占位符。

    整串就是占位符时**保留原类型**（int/float 直接放进去）；
    嵌在字符串里时做文本替换（如 filename_prefix ``gen___SEED__``）。

    参数值本身还可以再引用占位符（``PREFIX="gen___SEED__"``）：这类值会再展开一次，
    否则 ``__PREFIX__`` 会被原样塞进工作流，ComfyUI 只会报「400 参数非法」，
    排查起来毫无线索。``depth`` 防自引用死循环。
    """
    if depth > 5:
        raise ValueError("占位符展开层数过深（疑似参数互相引用）")
    if isinstance(value, dict):
        return {k: _substitute(v, params, used, depth) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params, used, depth) for v in value]
    if _is_placeholder(value):
        key = value[2:-2]
        if key not in params:
            raise KeyError(key)
        used.add(key)
        resolved = params[key]
        if isinstance(resolved, str) and _PLACEHOLDER_PREFIX in resolved:
            return _substitute(resolved, params, used, depth + 1)
        return resolved
    if isinstance(value, str) and _PLACEHOLDER_PREFIX in value:
        out = value
        for key, val in params.items():
            token = f"{_PLACEHOLDER_PREFIX}{key}{_PLACEHOLDER_SUFFIX}"
            if token in out:
                out = out.replace(token, str(val))
                used.add(key)
        return out
    return value


def inject(template: dict, /, **params: Any) -> dict:
    """把参数注入模板，返回**新的**工作流（不修改传入的 dict）。

    Raises:
        KeyError: 模板里有占位符但没给对应参数 —— 这类错误必须在提交前暴露，
            否则 ComfyUI 会把 ``__SEED__`` 当字符串 seed 直接拒绝（400），
            错误信息对用户毫无意义。
    """
    used: set[str] = set()
    workflow = _substitute(copy.deepcopy(template), params, used)
    missing = _placeholders_in(template) - used
    if missing:
        raise KeyError(f"模板占位符未提供参数: {', '.join(sorted(missing))}")
    return workflow


def _placeholders_in(node: Any) -> set[str]:
    """收集模板里出现的全部占位符名。"""
    found: set[str] = set()
    if isinstance(node, dict):
        for value in node.values():
            found |= _placeholders_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= _placeholders_in(value)
    elif _is_placeholder(node):
        found.add(node[2:-2])
    elif isinstance(node, str) and _PLACEHOLDER_PREFIX in node:
        import re

        found |= set(re.findall(r"__([A-Z0-9_]+)__", node))
    return found


def required_nodes(workflow: dict) -> set[str]:
    """模板里用到的全部节点类型（启动自检用）。"""
    return {
        node["class_type"]
        for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type")
    }


def missing_nodes(workflow: dict, object_info: dict) -> list[str]:
    """对照 ``/object_info`` 返回缺失的节点类型（升序，便于日志比对）。"""
    available = set(object_info.keys())
    return sorted(required_nodes(workflow) - available)


def _widget_options(object_info: dict, node: str, field: str) -> list[str]:
    """取某个节点某个下拉字段的可选值（ComfyUI 的 object_info 结构）。"""
    spec = (object_info.get(node) or {}).get("input") or {}
    for group in ("required", "optional"):
        entry = (spec.get(group) or {}).get(field)
        if not entry:
            continue
        first = entry[0] if isinstance(entry, list) and entry else None
        if isinstance(first, list):
            return [str(x) for x in first]
    return []


def model_files_in(workflow: dict) -> dict[str, list[str]]:
    """模板引用的模型文件，按节点类型归组（诊断「文件放错目录」用）。

    典型用途：文件下了但放进 ``checkpoints`` 而不是 ``diffusion_models``，
    ComfyUI 的下拉里就没有它 —— 前端只看到「模型缺失」，这里能指出是哪个文件。
    """
    out: dict[str, list[str]] = {}
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ctype = str(node.get("class_type") or "")
        inputs = node.get("inputs") or {}
        for key in ("unet_name", "clip_name", "vae_name", "ckpt_name"):
            value = inputs.get(key)
            if isinstance(value, str):
                out.setdefault(ctype, []).append(value)
    return out


def check_models(workflow: dict, object_info: dict) -> list[str]:
    """检查模板引用的模型文件是否出现在对应节点的下拉选项里。

    Returns:
        缺失项描述列表（空 = 全部就位）。用下拉选项而不是查磁盘：
        ComfyUI 只认它**自己扫到**的文件，磁盘上有但目录不对一样是不可用。
    """
    missing: list[str] = []
    field_of = {
        "UNETLoader": ("unet_name",),
        "CLIPLoader": ("clip_name",),
        "VAELoader": ("vae_name",),
        "CheckpointLoaderSimple": ("ckpt_name",),
    }
    for ctype, files in model_files_in(workflow).items():
        fields = field_of.get(ctype) or ()
        for field in fields:
            options = _widget_options(object_info, ctype, field)
            for name in files:
                if options and name not in options:
                    missing.append(f"{ctype}.{field}={name}（当前可选：{', '.join(options[:5])}）")
    return missing


def build_edit_workflow(
    template: dict,
    *,
    reference_names: list[str],
    **params: Any,
) -> dict:
    """构造「参考图编辑」工作流：把 N 张参考图接进 Qwen 2.1 的条件编码节点。

    Qwen-Image 2.1 的参考图通过 ``TextEncodeQwenImage21`` 的 AUTOGROW 输入
    ``images`` 传进去，API 序列化形如 ``{"image_1": ["4", 0], "image_2": ["5", 0]}``
    —— **嵌套 dict，不是列表**（实测：传列表会被 400 拒）。

    模板里已经连好第 1 张（``__REF_1__`` + ``images.image_1``）；第 2 张起在本函数里
    动态追加 ``LoadImage`` 节点（id 从 20 起）并挂到 ``images.image_{i}``，上限由调用方
    （``MODEL_SPECS[...]["max_refs"]``）保证 ≤10 —— 这也是 Qwen 2.1 的官方上限。

    Args:
        template: :func:`load_template` 读出的 ``qwen_edit`` 模板。
        reference_names: 已上传到 ComfyUI ``input`` 目录的文件名（1~10 个）。
        **params: 注入的标量参数（PROMPT/SEED/... 以及 ``REF_1``）。

    Raises:
        ValueError: 参考图数量不在 1~10。
    """
    if not 1 <= len(reference_names) <= 10:
        raise ValueError(f"参考图数量需在 1~10 之间，实际 {len(reference_names)}")

    workflow = inject(template, REF_1=reference_names[0], **params)

    # 第 2 张起：追加 LoadImage 节点并扩展条件节点的 images 映射
    images = workflow["6"]["inputs"]["images"]
    for index, name in enumerate(reference_names[1:], start=2):
        node_id = str(19 + index)  # 21, 22, ...
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        images[f"image_{index}"] = [node_id, 0]
    return workflow
