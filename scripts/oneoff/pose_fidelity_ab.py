"""零成本验证：R1（Qwen-Image 2.1 多参考图编辑）能不能靠「提示词 / 参考顺序 / 参考数量」把姿势改准。

背景（见 docs/R2（SDXL+ControlNet lineart）PoC成本与代价评估.md §9）：
正式下载 SDXL 之前，先用现有 R1 栈做一组不花新模型钱的对照实验，回答：
1. 现有提示词（无姿势名） vs 明确写出姿势名/角度，姿势跟随有没有肉眼可见差别；
2. 把参考图画成「两张图各是什么」的解释性提示词，能不能让模型真的按线稿摆姿势；
3. 参考顺序调换（线稿在前 / 人物在前）是否影响构图；
4. 同一张线稿重复放两遍（加权）是否有帮助；
5. 完全不给线稿、只用文字描述姿势（对照组）——用来确认线稿到底有没有起作用。

用法（默认 dry-run，只打印计划与工作流节点映射，不连 ComfyUI）：
    python scripts/oneoff/pose_fidelity_ab.py --dry-run
    python scripts/oneoff/pose_fidelity_ab.py --execute      # 需要 ComfyUI 在 8188 运行

产物：``<storage_root>/poc/r1_pose_fidelity_<日期>/`` 下的 PNG + summary.json（gitignore 覆盖，不入库）。
**注意**：脚本只读数据库（``mode=ro``），不改任何数据；生成图只落在 storage 的 poc 目录。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services import pose_gallery
from app.services.comfyui.client import ComfyUIClient
from app.services.comfyui.workflow import (
    build_edit_workflow,
    load_template,
)

DEFAULT_SUBJECT_ID = "9be0a4e5-f4cb-44b5-a120-781adfc85dbf"
DEFAULT_POSES = ("full_sexy_P001_side", "full_sexy_P006_diag")

# 生成参数与线上一致（qwen 档位默认：res_multistep / simple / shift 3 / cfg 1.0）
GEN = {
    "RESOLUTION": 1024,
    "STEPS": 25,
    "CFG": 1.0,
    "SAMPLER": "res_multistep",
    "SCHEDULER": "simple",
    "SHIFT": 3.0,
    "NEGATIVE": "",
}
SEED = 1001


@dataclass(frozen=True)
class Variant:
    """一个实验组：参考图排列 + 提示词构造方式。"""

    key: str
    label: str
    refs: tuple[str, ...]
    prompt_kind: str


VARIANTS = (
    Variant(
        "v0_baseline",
        "现状基线（人物在前、线稿最后；通用提示词，无姿势名）",
        ("subject", "pose"),
        "baseline",
    ),
    Variant(
        "v1_named",
        "提示词带姿势名·角度（当前一键出图的实际写法）",
        ("subject", "pose"),
        "named",
    ),
    Variant(
        "v2_explain",
        "解释性提示词：说明两张图各是什么 + 线稿只作姿势、不要关节圆圈",
        ("subject", "pose"),
        "explain",
    ),
    Variant(
        "v3_swapped",
        "参考顺序调换：线稿在前、人物在后",
        ("pose", "subject"),
        "swapped",
    ),
    Variant(
        "v4_dup_pose",
        "线稿重复两遍（人物 + 线稿 + 线稿，变相加权）",
        ("subject", "pose", "pose"),
        "named",
    ),
    Variant(
        "v5_no_pose",
        "对照组：不给线稿，只用文字描述姿势",
        ("subject",),
        "text_only",
    ),
)


def _subject_from_db(subject_id: str, db_path: Path) -> tuple[Path, str]:
    """只读数据库查素材主文件路径；返回 (绝对路径, 原始后缀)。"""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path from inspirations where id=? and deleted_at is null",
            (subject_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"素材不存在或已删除：{subject_id}")
    rel = str(row[0])
    path = settings.storage_root / rel
    if not path.exists():
        raise SystemExit(f"素材文件不存在：{path}")
    return path, Path(rel).suffix or ".png"


def _db_path() -> Path:
    """从 database_url 里取 SQLite 文件路径（只支持 sqlite:// 配置）。"""
    raw = settings.database_url
    if ":///" not in raw:
        raise SystemExit(f"不认识 DATABASE_URL：{raw}")
    return Path(raw.split(":///", 1)[1])


async def _pose_paths(pose_ids: list[str]) -> dict[str, Path]:
    """确保姿势原图在本地缓存（已缓存则零网络）。"""
    out: dict[str, Path] = {}
    for pose_id in pose_ids:
        out[pose_id] = await pose_gallery.ensure_pose_image(pose_id)
    return out


def _pose_info(pose_id: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    item = next((x for x in catalog if x["id"] == pose_id), None)
    if item is None:
        raise SystemExit(f"姿势不在目录里：{pose_id}")
    return item


def build_prompt(kind: str, pose: dict[str, Any]) -> str:
    """按实验组构造提示词（中文；与线上模板同一风格）。"""
    name, angle = pose["name"], pose["angle"]
    if kind == "baseline":
        return (
            "保持第 1 张参考图的人物长相、发型与服装不变，"
            "按最后一张参考图的姿势重新生成，全身照。"
        )
    if kind == "named":
        return (
            "保持第 1 张参考图的人物长相、发型与服装不变，"
            f"按最后一张参考图的姿势（{name}·{angle}）重新生成，全身照。"
        )
    if kind == "explain":
        return (
            "画面有两张参考图：第 1 张是人物照片（主体），第 2 张是灰色素体线稿（姿势参考）。"
            "保持第 1 张人物的长相、发型与服装，把人物姿势改成第 2 张线稿的姿势"
            f"（{name}·{angle}），全身照、真实照片质感；线稿只作姿势参考，"
            "不要保留灰色模特、关节圆圈或线条风格。"
        )
    if kind == "swapped":
        return (
            "画面有两张参考图：第 1 张是灰色素体线稿（姿势参考），第 2 张是人物照片（主体）。"
            "保持第 2 张人物的长相、发型与服装，按第 1 张线稿的姿势"
            f"（{name}·{angle}）重新生成，全身照。"
        )
    if kind == "text_only":
        return (
            "保持人物长相、发型与服装不变，把姿势改成"
            f"「{name}」（{angle}视角、全身照）。"
        )
    raise ValueError(f"未知提示词类型：{kind}")


def _build_workflow(
    template: dict,
    variant: Variant,
    pose: dict[str, Any],
    names: dict[str, str],
) -> dict:
    """按实验组拼参考图顺序并构建工作流。"""
    ref_names = [names[ref] for ref in variant.refs]
    return build_edit_workflow(
        template,
        reference_names=ref_names,
        PROMPT=build_prompt(variant.prompt_kind, pose),
        PREFIX=f"poc_{variant.key}_{pose['id']}",
        SEED=SEED,
        **GEN,
    )


def _dump_mapping(workflow: dict, variant: Variant) -> None:
    """打印工作流里的参考图挂载，人工核对顺序是否真按实验设定。"""
    encode = workflow["6"]["inputs"]
    images = {k: v for k, v in encode.items() if k.startswith("images")}
    load_names = {
        node_id: node["inputs"].get("image")
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "LoadImage"
    }
    print(f"   参考顺序 {list(variant.refs)} -> encode: {images}")
    for key, (node_id, _slot) in images.items():
        print(f"     {key} = 节点 {node_id} = {load_names.get(node_id)}")


def _pick_image(entry: dict) -> dict | None:
    for output in (entry.get("outputs") or {}).values():
        images = output.get("images") or []
        if images:
            return images[0]
    return None


async def run(args: argparse.Namespace) -> int:
    db_path = _db_path()
    subject_path, subject_suffix = _subject_from_db(args.subject_id, db_path)
    pose_ids = [p.strip() for p in args.poses.split(",") if p.strip()]
    pose_paths = await _pose_paths(pose_ids)
    template, digest = load_template("qwen_edit")
    catalog = await pose_gallery.load_catalog()
    poses = {pid: _pose_info(pid, catalog) for pid in pose_ids}

    out_dir = Path(args.out_dir) if args.out_dir else (
        settings.storage_root / "poc" / f"r1_pose_fidelity_{time.strftime('%Y%m%d')}"
    )
    print(f"主体素材：{subject_path}")
    print(f"姿势：{pose_ids}")
    print(f"输出目录：{out_dir}")
    print(f"模板：qwen_edit sha={digest}；组数={len(VARIANTS)}，姿势数={len(pose_ids)}")
    print()

    names = {"subject": f"poc_subject{subject_suffix}"}
    for pid in pose_ids:
        names[f"pose_{pid}"] = f"poc_pose_{pid}.png"

    if args.dry_run:
        print("=== dry-run：只构建工作流并打印参考挂载（不连 ComfyUI）===")
        first = poses[pose_ids[0]]
        for variant in VARIANTS:
            wf = _build_workflow(template, variant, first, _ref_names(names, variant, first))
            print(f"[{variant.key}] {variant.label}")
            _dump_mapping(wf, variant)
            print()
        return 0

    # ── 实跑：连 ComfyUI ──
    client = ComfyUIClient(settings.comfyui_base_url, timeout=settings.comfyui_timeout)
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    try:
        subject_data = await asyncio.to_thread(subject_path.read_bytes)
        uploaded = {
            "subject": await client.upload_image(subject_data, names["subject"]),
        }
        for pid in pose_ids:
            data = await asyncio.to_thread(pose_paths[pid].read_bytes)
            uploaded[f"pose_{pid}"] = await client.upload_image(
                data, names[f"pose_{pid}"]
            )
        print(f"已上传参考图：{uploaded}")
        print()

        for pose_id in pose_ids:
            pose = poses[pose_id]
            for variant in VARIANTS:
                ref_names = _ref_names(uploaded, variant, pose)
                wf = _build_workflow(template, variant, pose, ref_names)
                started = time.monotonic()
                prompt_id = await client.submit(wf)
                entry = await client.wait_for_prompt(prompt_id, timeout=args.timeout)
                image = _pick_image(entry)
                if image is None:
                    print(f"[失败] {variant.key} × {pose_id}：ComfyUI 未返回图片")
                    continue
                data = await client.fetch_image(
                    image.get("filename") or "", image.get("subfolder") or ""
                )
                target = out_dir / f"{pose_id}__{variant.key}.png"
                target.write_bytes(data)
                elapsed = round(time.monotonic() - started, 1)
                records.append(
                    {
                        "pose_id": pose_id,
                        "pose_name": pose["name"],
                        "pose_angle": pose["angle"],
                        "variant": variant.key,
                        "variant_label": variant.label,
                        "image": target.name,
                        "seed": SEED,
                        "steps": GEN["STEPS"],
                        "elapsed_s": elapsed,
                    }
                )
                print(f"[OK] {variant.key} × {pose_id} -> {target.name}（{elapsed}s）")
    finally:
        await client.aclose()

    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"完成 {len(records)} 张；summary: {summary}")
    print("下一步：人工按 姿势保真 / 身份 / 画面质感 三项打分，把结论写回评估文档。")
    return 0


def _ref_names(
    uploaded: dict[str, str], variant: Variant, pose: dict[str, Any]
) -> dict[str, str]:
    """把「subject / pose」占位映射成已上传的文件名。"""
    return {
        "subject": uploaded["subject"],
        "pose": uploaded[f"pose_{pose['id']}"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="R1 姿势保真度零成本对照实验（提示词/参考顺序/参考数量）"
    )
    parser.add_argument("--subject-id", default=DEFAULT_SUBJECT_ID, help="主体素材 id")
    parser.add_argument(
        "--poses", default=",".join(DEFAULT_POSES), help="姿势 id，逗号分隔"
    )
    parser.add_argument("--out-dir", default="", help="输出目录（默认 storage/poc/...）")
    parser.add_argument("--timeout", type=float, default=900.0, help="单张生成超时（秒）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true", default=True, help="只构建工作流（默认）"
    )
    group.add_argument(
        "--execute", dest="dry_run", action="store_false", help="真实提交 ComfyUI"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
