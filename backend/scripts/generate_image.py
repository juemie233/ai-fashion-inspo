#!/usr/bin/env python
"""命令行独立出图：只要 ComfyUI 在跑，不用启动本项目的前后端。

为什么单独做这个工具：素材库的「本地出图」页要把参数存库、走任务队列、按批确认入库——
那是素材生产的流程；而「我就想试个 prompt 看看效果」不该为此启动 FastAPI + worker +
Vue。本脚本复用**同一份**工作流模板（``app/services/comfyui/workflows/*.json``）与档位
定义（``MODEL_SPECS``），只做「拼工作流 → 提交 → 取图 → 存盘」，不碰数据库。

用法（任意目录均可运行）::

    python backend/scripts/generate_image.py "米色针织开衫配白色阔腿裤，极简工作室背景，柔和自然光"
    python backend/scripts/generate_image.py --count 4 --size 768x1344 "白色百褶短裙配黑色乐福鞋"
    python backend/scripts/generate_image.py --model qwen_2_1_i2i --ref 参考图.jpg "同一个人，光脚不穿鞋"
    python backend/scripts/generate_image.py --check          # 只体检：节点 / 模型 / 显存
    python backend/scripts/generate_image.py --list-models    # 列出可用档位

    # 想「在 ComfyUI 页面里改字出图」：先把工作流连参数一起导出来，再拖进画布
    python backend/scripts/generate_image.py --dump "backend/storage/generated/cli/z_image.json" "示例描述"
    echo "粉色针织连衣裙，街拍" | python backend/scripts/generate_image.py   # 从 stdin 读描述

输出默认落在 ``backend/storage/generated/cli/``（与素材库候选区同一棵树，已 gitignore）。
退出码：0 成功；1 参数或环境问题；2 ComfyUI 报错；130 手动中断。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:  # 支持从任意目录直接运行本脚本
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.comfyui import workflow as wf  # noqa: E402
from app.services.comfyui.client import ComfyUIClient, ComfyUIError  # noqa: E402
from app.services.comfyui.models import (  # noqa: E402
    MAX_COUNT,
    MAX_PIXELS,
    MAX_STEPS,
    MIN_STEPS,
    MODEL_SPECS,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8188"
DEFAULT_OUT_DIR = BACKEND_ROOT / "storage" / "generated" / "cli"
"""单张生成超时（秒）：Qwen 2.1 在 16 GB 卡上最慢约 45 秒，留足余量给排队。"""
DEFAULT_TIMEOUT = 900.0


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def parse_size(text: str) -> tuple[int, int]:
    """解析 ``WxH``（兼容 × 与 * 两种写法）。"""
    normalized = text.lower().replace("×", "x").replace("*", "x")
    left, _, right = normalized.partition("x")
    try:
        return int(left), int(right)
    except ValueError:
        raise SystemExit(f"尺寸格式不对：{text}（示例 1024x1024 或 768x1344）") from None


def pick_image(entry: dict[str, Any]) -> dict[str, Any] | None:
    """从 history 的 outputs 里挑出第一张生成图（与执行器同一口径）。"""
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


def build_workflow(
    spec: dict[str, Any],
    *,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    shift: float,
    refs: list[str],
    negative: str = "",
    prefix: str = "cli",
) -> dict[str, Any]:
    """按档位 + 参数拼出可直接提交的工作流（纯函数：不联网，便于单测）。

    与执行器 ``task_runners/image_gen.py`` 走同一套模板与注入方式：
    文生图注入 ``WIDTH``/``HEIGHT``，图生图把 ``RESOLUTION`` 与参考图交给
    :func:`workflow.build_edit_workflow`（后者负责追加第 2 张起的 LoadImage 节点）。
    """
    template, _digest = wf.load_template(str(spec["workflow"]))
    template = wf.apply_file_overrides(template, spec.get("files"))
    common: dict[str, Any] = {
        "PROMPT": prompt,
        "SEED": seed,
        "STEPS": steps,
        "CFG": cfg,
        "SAMPLER": sampler,
        "SCHEDULER": scheduler,
        "SHIFT": shift,
        "PREFIX": prefix,
    }
    if spec.get("kind") == "i2i":
        return wf.build_edit_workflow(
            template,
            reference_names=refs,
            NEGATIVE=negative,
            RESOLUTION=width,
            **common,
        )
    return wf.inject(template, WIDTH=width, HEIGHT=height, **common)


def resolve_params(args: argparse.Namespace, spec: dict[str, Any], prompt: str) -> dict[str, Any]:
    """校验并归一化参数（口径与后端 ``generation_service._validate_params`` 一致）。

    Raises:
        SystemExit: 参数越界，消息直接给人看。
    """
    defaults = spec["defaults"]
    width, height = parse_size(args.size) if args.size else (defaults["width"], defaults["height"])
    if width < 256 or height < 256:
        raise SystemExit("尺寸过小：宽高最小 256")
    # 上限按档位算：Z-Image 可到 1328²，Qwen-Image 2.1 只有 1024²（权重更大）
    limit = min(MAX_PIXELS, int(spec.get("max_pixels") or MAX_PIXELS))
    if width * height > limit:
        side = int(limit**0.5)
        raise SystemExit(f"尺寸过大：该档位宽×高不能超过 {limit}（约 {side}×{side}），否则会显存不足")
    steps = args.steps if args.steps is not None else int(defaults["steps"])
    if not MIN_STEPS <= steps <= MAX_STEPS:
        raise SystemExit(f"步数需在 {MIN_STEPS}~{MAX_STEPS} 之间")
    if not 1 <= args.count <= MAX_COUNT:
        raise SystemExit(f"张数需在 1~{MAX_COUNT} 之间")
    text = prompt.strip()
    if not text:
        raise SystemExit("prompt 不能为空")
    if len(text) > 2000:
        raise SystemExit("prompt 过长（上限 2000 字）")
    seed_base = args.seed if args.seed > 0 else random.randint(1, 2**31 - 1)
    return {
        "prompt": text,
        "negative": args.negative.strip(),
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": args.cfg if args.cfg is not None else float(defaults["cfg"]),
        "sampler": args.sampler or str(defaults["sampler"]),
        "scheduler": args.scheduler or str(defaults["scheduler"]),
        "shift": float(defaults["shift"]),
        "count": args.count,
        "seed_base": seed_base,
    }


def print_models() -> None:
    """列出档位（key / 类型 / 默认尺寸与步数 / 参考图要求）。

    不用对齐表格：档位名与中文说明混排时按字符数补空格必然错位，两行一组更稳。
    """
    print("可用档位：")
    for key, spec in MODEL_SPECS.items():
        defaults = spec["defaults"]
        refs = f"参考图 1~{spec['max_refs']} 张" if spec.get("max_refs") else "不需要参考图"
        print(f"  {key}  [{spec['kind']}] {spec['label']}")
        print(f"      默认 {defaults['width']}x{defaults['height']} · {defaults['steps']} 步 · {refs}")
    print("\n提示：图生图（i2i）必须给 --ref；文生图（t2i）不要给 --ref。")


def _vram_gib(stats: dict[str, Any]) -> tuple[float, float]:
    """从 /system_stats 取 (total, free)，单位 GiB。"""
    devices = stats.get("devices") or []
    dev = devices[0] if devices else {}
    total = float(dev.get("vram_total") or 0) / 2**30
    free = float(dev.get("vram_free") or 0) / 2**30
    return round(total, 2), round(free, 2)


async def run_check(client: ComfyUIClient) -> int:
    """体检：ComfyUI 是否在跑、模板依赖的节点与权重是否就位、显存够不够。"""
    stats = await client.system_stats()
    info = await client.object_info()
    total, free = _vram_gib(stats)
    system = stats.get("system") or {}
    print(f"ComfyUI {system.get('comfyui_version') or '?'} · 显存 {free:.1f} / {total:.1f} GiB 可用")
    print(f"节点表 {len(info)} 个\n")

    ready = 0
    for key, spec in MODEL_SPECS.items():
        try:
            template, _digest = wf.load_template(str(spec["workflow"]))
            template = wf.apply_file_overrides(template, spec.get("files"))
        except FileNotFoundError:
            print(f"✗ {key:<18} 模板文件缺失：{spec['workflow']}.json")
            continue
        except KeyError as exc:
            print(f"✗ {key:<18} 档位配置与模板不符：{exc}")
            continue
        missing_nodes = wf.missing_nodes(template, info)
        missing_models = wf.check_models(template, info)
        if missing_nodes or missing_models:
            print(f"✗ {key:<18} 缺节点 {missing_nodes or '无'} · 缺模型 {missing_models or '无'}")
            continue
        ready += 1
        need = float(spec.get("vram_gib") or 0)
        state = (
            "节点与模型就绪"
            if free >= need
            else f"节点与模型就绪，但可用显存 {free:.1f} < 建议 {need:.1f} GiB（可能 OOM）"
        )
        print(f"✓ {key:<18} {state}")
    return 0 if ready else 2


async def _generate(
    client: ComfyUIClient, args: argparse.Namespace, spec: dict[str, Any], params: dict[str, Any]
) -> int:
    """逐张生成并落盘（参考图先上传，再按 seed 递增提交）。"""
    refs: list[str] = []
    if spec.get("kind") == "i2i":
        if not args.ref:
            raise SystemExit("该档位是图生图，必须用 --ref 给 1~10 张参考图")
        max_refs = int(spec.get("max_refs") or 10)
        if len(args.ref) > max_refs:
            raise SystemExit(f"参考图最多 {max_refs} 张，实际 {len(args.ref)} 张")
        for path_text in args.ref:
            path = Path(path_text).expanduser()
            if not path.is_file():
                raise SystemExit(f"参考图不存在：{path}")
            # 必须上传到 ComfyUI 的 input 目录：LoadImage 只读它自己的目录（路径白名单）
            refs.append(await client.upload_image(path.read_bytes(), f"cli_ref_{path.name}"))
        print(f"参考图已上传 {len(refs)} 张")
    elif args.ref:
        raise SystemExit("当前档位是文生图，不支持 --ref；改款请加 --model qwen_2_1_i2i")

    stats = await client.system_stats()
    _total, free = _vram_gib(stats)
    need = float(spec.get("vram_gib") or 0)
    print(f"ComfyUI 可用显存 {free:.1f} GiB（该档位建议 ≥ {need:.1f} GiB）")
    if free < need:
        _err("⚠ 显存可能不足：先把 Ollama 停掉，或把 --size 调小；仍会继续尝试")

    out_dir = Path(args.out).expanduser() if args.out else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    count = int(params["count"])
    written: list[Path] = []

    for idx in range(1, count + 1):
        seed = int(params["seed_base"]) + idx - 1
        workflow = build_workflow(
            spec,
            prompt=params["prompt"],
            seed=seed,
            width=params["width"],
            height=params["height"],
            steps=params["steps"],
            cfg=params["cfg"],
            sampler=params["sampler"],
            scheduler=params["scheduler"],
            shift=params["shift"],
            refs=refs,
            negative=params["negative"],
            prefix=f"cli_{stamp}_{idx:02d}",
        )
        label = f"[{idx}/{count}] seed {seed}"
        print(f"{label} 提交中…", flush=True)
        started = time.monotonic()
        prompt_id = await client.submit(workflow, client_id="fashion-inspo-cli")

        async def _tick() -> bool:
            print(".", end="", flush=True)  # 每 2 秒一个点，让人知道还在跑
            return False

        entry = await client.wait_for_prompt(prompt_id, timeout=args.timeout, on_tick=_tick)
        print()
        image = pick_image(entry)
        if image is None:
            _err(f"{label} ComfyUI 未返回图片（outputs 为空）")
            continue
        data = await client.fetch_image(str(image.get("filename") or ""), str(image.get("subfolder") or ""))
        target = out_dir / f"{stamp}_{idx:02d}_seed{seed}.png"
        target.write_bytes(data)
        written.append(target)
        elapsed = time.monotonic() - started
        print(f"{label} ✓ {target}（{len(data) / 1024:.0f} KB，{elapsed:.1f}s）")

    if not written:
        _err("✗ 一张都没成功")
        return 2
    print(f"\n完成 {len(written)}/{count} 张，目录：{out_dir}")
    return 0


def prompt_node_ids(workflow: dict[str, Any], prompt: str) -> list[tuple[str, str]]:
    """找出工作流里承载描述文字的节点（页面里要改的就是它）。

    两种档位的落点不同：文生图是 ``CLIPTextEncode.text``，图生图是
    ``TextEncodeQwenImage21.prompt``，所以按「值等于描述」来反查，而不是写死节点 id。
    """
    hits: list[tuple[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        if any(value == prompt for value in inputs.values()):
            hits.append((str(node_id), str(node.get("class_type") or "?")))
    return hits


async def _dump(
    client: ComfyUIClient, args: argparse.Namespace, spec: dict[str, Any], params: dict[str, Any]
) -> int:
    """导出「占位符已填实值」的工作流，供直接拖进 ComfyUI 页面改字出图。

    为什么需要它：页面（画布）能吃 API 格式 JSON（前端 ``isApiJson`` / ``loadApiJson``
    会自动作图），但仓库里的模板是给后端注入用的，数字字段写的是 ``__SEED__`` 这类
    占位符 —— 页面会把它们当字符串塞进数字框，点 Queue 报参数非法。这里把参数全部
    落成真实值，拖进去就能直接跑，只剩描述文字要改。
    """
    refs: list[str] = []
    if spec.get("kind") == "i2i":
        if not args.ref:
            raise SystemExit("图生图导出需要 --ref（页面里的 LoadImage 必须指向一张真实存在的图）")
        for path_text in args.ref:
            path = Path(path_text).expanduser()
            if not path.is_file():
                raise SystemExit(f"参考图不存在：{path}")
            # 上传后把「ComfyUI 认得的文件名」写进工作流，页面里那张图才会直接可跑
            refs.append(await client.upload_image(path.read_bytes(), f"cli_ref_{path.name}"))

    workflow = build_workflow(
        spec,
        prompt=params["prompt"],
        seed=int(params["seed_base"]),
        width=params["width"],
        height=params["height"],
        steps=params["steps"],
        cfg=params["cfg"],
        sampler=params["sampler"],
        scheduler=params["scheduler"],
        shift=params["shift"],
        refs=refs,
        negative=params["negative"],
        prefix=f"ui_{spec['workflow']}",
    )
    target = Path(args.dump).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes = prompt_node_ids(workflow, params["prompt"])
    node_text = "、".join(f"节点 {nid}（{ctype}）" for nid, ctype in nodes) or "（未找到，请手动确认）"
    print(f"已导出工作流：{target}\n")
    print("在 ComfyUI 页面里出图（不用启动本项目前后端）：")
    print(f"  1. 打开 {args.base_url}")
    print("  2. 把上面这个 .json 拖进画布（或 Ctrl+O 打开）—— 页面会自动把 API 工作流画成节点图")
    print(f"  3. 双击 {node_text}，把文字改成你要的描述")
    print("  4. 点「Queue / 运行」，出图在 ComfyUI 的 output 目录（页面里也能直接看到）")
    print("\n注意：页面里出的图不会进素材库；要入库请用网页的「本地出图」或直接跑本脚本。")
    return 0


async def _run(args: argparse.Namespace, spec: dict[str, Any]) -> int:
    client = ComfyUIClient(args.base_url, timeout=15.0)
    try:
        if args.check:
            return await run_check(client)
        prompt = args.prompt if args.prompt is not None else sys.stdin.readline()
        params = resolve_params(args, spec, prompt)
        if args.dump:
            return await _dump(client, args, spec, params)
        return await _generate(client, args, spec, params)
    except ComfyUIError as exc:
        _err(f"✗ {_friendly(exc)}")
        if exc.kind in ("invalid", "oom") and exc.detail:
            _err(f"  详情：{exc.detail}")
        return 2
    finally:
        await client.aclose()


def _friendly(exc: ComfyUIError) -> str:
    """把客户端错误分类翻译成一句能照着做的话。"""
    mapping = {
        "not_running": "ComfyUI 没在跑：双击 D:\\AI\\ComfyUI\\run_nvidia_gpu.bat，等 8188 能打开再试",
        "oom": "显存不足：先停掉 Ollama，或把 --size / --steps 调小后重试",
        "timeout": "生成超时：可调大 --timeout，或降 --size / --steps",
        "cancelled": "已取消",
        "invalid": "ComfyUI 拒绝了工作流：多半是节点或权重与当前 ComfyUI 版本不匹配（先跑 --check）",
    }
    return mapping.get(exc.kind, str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="命令行独立出图（只需 ComfyUI 在跑，不用启动本项目前后端）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", default=None, help="描述；省略则从 stdin 读一行")
    parser.add_argument("--model", default="z_image_int8", help="档位 key（见 --list-models）")
    parser.add_argument("--count", type=int, default=1, help=f"张数 1~{MAX_COUNT}（默认 1）")
    parser.add_argument("--size", default=None, help="尺寸 WxH，默认取档位默认（如 1024x1024）")
    parser.add_argument("--steps", type=int, default=None, help="采样步数，默认取档位默认")
    parser.add_argument("--cfg", type=float, default=None, help="CFG，默认取档位默认")
    parser.add_argument("--sampler", default=None, help="采样器，默认取档位默认")
    parser.add_argument("--scheduler", default=None, help="调度器，默认取档位默认")
    parser.add_argument("--seed", type=int, default=0, help="种子；0 = 随机，多张时逐张加 1")
    parser.add_argument("--negative", default="", help="负向描述（仅图生图有效）")
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        metavar="PATH",
        help="参考图路径（图生图必填，可重复 1~10 张）",
    )
    parser.add_argument("--out", default=None, help="输出目录（默认 backend/storage/generated/cli）")
    parser.add_argument(
        "--dump",
        default=None,
        metavar="PATH",
        help="不跑图，把「占位符已填实值」的工作流导出到 PATH，供拖进 ComfyUI 页面改字出图",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="ComfyUI 地址")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单张超时秒数")
    parser.add_argument("--check", action="store_true", help="只体检（节点 / 模型 / 显存），不出图")
    parser.add_argument("--list-models", action="store_true", help="列出档位后退出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_models:
        print_models()
        return 0
    spec = MODEL_SPECS.get(args.model)
    if spec is None:
        _err(f"未知档位：{args.model}（可用：{', '.join(MODEL_SPECS)}）")
        return 1
    try:
        return asyncio.run(_run(args, spec))
    except KeyboardInterrupt:
        _err("\n已中断；ComfyUI 队列里最后一条可能仍在跑（可在 ComfyUI 界面清队列）")
        return 130


if __name__ == "__main__":
    sys.exit(main())
