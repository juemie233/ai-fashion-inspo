"""手机图剪裁 content 模式（抖音截图内容边界检测）候选资格诊断脚本。

用途：给定素材 ID，复现「扫描 → 检测 → 候选资格裁决」全过程，输出每张素材的
截图特征、内容边界、字形连通域明细（含被剔除的连通域及原因）与最终是否列出，
用于排查「不该列出的素材被列出」/「该列出的素材没出现」两类问题。

典型用法（在 backend/ 目录下执行，Python 解释器按项目约定指定）::

    # 1) 诊断指定素材（ID 可用完整 UUID 或前 8 位前缀，逗号/空格分隔）
    python scripts/diag_content_candidates.py --ids f3c6b983,ef28b320 --detail

    # 2) 校验用户标注的负样本是否已全部不列出（不符合预期时退出码 1）
    python scripts/diag_content_candidates.py --ids-file neg_ids.txt --expect unlisted

    # 3) 校验真实截图样本是否仍列出
    python scripts/diag_content_candidates.py --ids 695832a9,a4fbfda3 --expect listed --detail

    # 4) 全库扫描，只看被列出的候选（按 ratio 预筛，避免解码全部素材）
    python scripts/diag_content_candidates.py --scan --ratio-min 1.8 --only listed --detail

    # 5) 全库扫描，统计裁决分布
    python scripts/diag_content_candidates.py --scan --only all

ID 文件格式：每行一个 ID（完整或 8 位前缀），``#`` 开头为注释，空行忽略。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageOps

from app.config import settings
from app.services import crop_service as cs
from app.services import image_cropping as ic


def _load_db_map() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """读取真实库的 id → 相对路径映射（只读连接，不写任何数据）。

    返回:
        (完整 id 映射, 8 位前缀映射)
    """
    db_path = Path(settings.storage_root).parent / "fashion_inspo.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, file_path FROM inspirations").fetchall()
    finally:
        conn.close()
    full = {r[0]: r[1] for r in rows}
    prefix: dict[str, tuple[str, str]] = {}
    for r in rows:
        prefix.setdefault(r[0][:8], (r[0], r[1]))
    return full, prefix


def _resolve(key: str, full_map, prefix_map) -> tuple[str | None, str | None]:
    """按完整 id 或 8 位前缀解析素材，返回 (id, 相对路径)。"""
    key = key.strip()
    if not key:
        return None, None
    if key in full_map:
        return key, full_map[key]
    hit = prefix_map.get(key[:8])
    if hit:
        return hit
    return None, None


def _read_ids(args) -> list[str]:
    """汇总命令行与 ID 文件中的素材 ID。"""
    keys: list[str] = []
    if args.ids:
        keys.extend(k for k in args.ids.replace(",", " ").split() if k)
    if args.ids_file:
        text = Path(args.ids_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                keys.extend(line.replace(",", " ").split())
    return keys


def _verdict_reason(bounds: dict | None, confidence: str, ratio: float) -> str:
    """给出裁决原因（与 crop_service._content_candidate_qualified 同口径）。"""
    if bounds is None:
        return "检测失败（无内容边界且无字形证据）→ 排除"
    glyph_found = bounds.get("glyph_found", False)
    if bounds.get("glyph_strong"):
        return "强字形（状态栏时间签名）→ 列出"
    if (
        confidence == "low"
        and bounds["residual_top_frac"] <= 0
        and bounds.get("residual_bottom_frac", 0) <= 0
        and not glyph_found
    ):
        return "低置信且无任何建议 → 排除"
    if ratio >= ic._FULL_SCREENSHOT_RATIO:
        if glyph_found:
            return f"完整截图先验 + 弱字形（ratio {ratio:.2f} ≥ {ic._FULL_SCREENSHOT_RATIO}）→ 列出"
        if bounds.get("kind") in ("gray_band", "status_bar"):
            return f"完整截图先验 + UI 结构（kind={bounds['kind']}）→ 列出"
        return "完整截图比例但无字形/UI 结构证据 → 排除"
    if glyph_found:
        return "非完整截图且非强字形（弱字形/条带单独不构成候选）→ 排除"
    return "无 UI 证据 → 排除"


def _print_glyph_debug(glyph_debug: dict) -> None:
    """打印字形检测明细（保留与剔除的连通域）。"""
    print(
        f"    字形条带 {glyph_debug['strip_w']}x{glyph_debug['strip_h']} "
        f"饱和={glyph_debug['strip_sat']} 前景={glyph_debug['fg_ratio']} "
        f"带上沿前景={glyph_debug.get('fg_above')} "
        f"判定={glyph_debug['note'] or '-'}"
    )
    for g in glyph_debug["glyphs"]:
        print(
            f"      [保留] x=[{g['x0']},{g['x1']}) y=[{g['y0']},{g['y1']}) "
            f"宽={g['x1'] - g['x0']} 高={g['y1'] - g['y0']} 面积={g['area']} "
            f"背景std={g.get('bg_std')} "
            f"中心x={(g['x0'] + g['x1']) / 2 / glyph_debug['strip_w']:.2f}"
        )
    for g in glyph_debug["rejected"]:
        print(
            f"      [剔除] x=[{g['x0']},{g['x1']}) y=[{g['y0']},{g['y1']}) "
            f"宽={g['x1'] - g['x0']} 高={g['y1'] - g['y0']} 面积={g['area']} "
            f"背景std={g.get('bg_std')} 原因={g['reason']}"
        )


def _analyze(rel: str, detail: bool) -> dict | None:
    """检测单张素材并计算裁决，返回诊断字典（文件不存在/解码失败返回 None）。"""
    full = settings.storage_root / rel
    if not full.exists():
        return None
    with Image.open(full) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        width, height = img.size
        glyph = ic._glyph_evidence(img, debug=detail)
    try:
        features, bounds = ic.analyze_screenshot_combined(str(full))
    except Exception as exc:  # noqa: BLE001 - 诊断脚本需如实报告解码异常
        return {"error": str(exc), "width": width, "height": height}
    confidence = ic.screenshot_confidence(features)
    ratio = height / width
    listed = bounds is not None and cs._content_candidate_qualified(bounds, confidence, ratio)
    return {
        "width": width,
        "height": height,
        "ratio": ratio,
        "confidence": confidence,
        "features": features,
        "bounds": bounds,
        "glyph": glyph,
        "listed": listed,
        "reason": _verdict_reason(bounds, confidence, ratio),
    }


def _report_one(key: str, full_map, prefix_map, detail: bool) -> tuple[dict | None, bool]:
    """诊断单张素材并打印明细，返回 (诊断字典, 是否解析成功)。"""
    iid, rel = _resolve(key, full_map, prefix_map)
    if not iid:
        print(f"  !! {key} 库中未找到")
        return None, False
    info = _analyze(rel, detail)
    if info is None:
        print(f"  !! {key} 文件不存在：{rel}")
        return None, False
    if "error" in info:
        print(f"  !! {key} 解码失败：{info['error']}")
        return None, False
    bounds = info["bounds"]
    print(
        f"  {iid[:8]} {info['width']}x{info['height']} ratio={info['ratio']:.2f} "
        f"conf={info['confidence']} listed={info['listed']} | {info['reason']}"
    )
    print(
        f"    特征 top_bar={info['features'].get('top_bar')} bottom_bar={info['features'].get('bottom_bar')}"
    )
    if bounds is None:
        print("    边界：None（检测失败）")
    else:
        print(
            f"    边界 kind={bounds['kind']} top={bounds['top_frac']:.4f} "
            f"bot={bounds['bottom_frac']:.4f} "
            f"resid_top={bounds['residual_top_frac']:.4f} "
            f"resid_bot={bounds.get('residual_bottom_frac', 0):.4f} "
            f"already={bounds['already_cropped']}"
        )
    glyph = info["glyph"]
    print(f"    字形 found={glyph['found']} strong={glyph['strong']} top_frac={glyph['top_frac']:.4f}")
    if detail and "debug" in glyph:
        _print_glyph_debug(glyph["debug"])
    return info, True


def _run_ids(args) -> int:
    """按 --ids/--ids-file 诊断指定素材并校验预期。"""
    keys = _read_ids(args)
    if not keys:
        print("未指定素材：请用 --ids 或 --ids-file（或用 --scan 全库扫描）")
        return 2
    full_map, prefix_map = _load_db_map()
    expect_listed = {"listed": True, "unlisted": False}.get(args.expect)
    bad = 0
    listed_cnt = 0
    for key in keys:
        info, ok = _report_one(key, full_map, prefix_map, args.detail)
        if not ok:
            bad += 1
            continue
        if info.get("listed"):
            listed_cnt += 1
        if expect_listed is not None and info.get("listed") != expect_listed:
            bad += 1
    print(
        f"\n共 {len(keys)} 张：列出 {listed_cnt} 张、不列出 {len(keys) - listed_cnt} 张，"
        f"异常/不符预期 {bad} 张"
    )
    return 1 if bad else 0


def _run_scan(args) -> int:
    """全库扫描：按 ratio 预筛后逐张检测，输出裁决分布与明细。"""
    full_map, _prefix_map = _load_db_map()
    items = sorted(full_map.items(), key=lambda kv: kv[0])
    total = 0
    listed = 0
    decoded = 0
    for iid, rel in items:
        if args.limit and decoded >= args.limit:
            break
        full = settings.storage_root / rel
        if not full.exists():
            continue
        try:
            width, height = ic.probe_size(full)
        except Exception:  # noqa: BLE001 - 无法读头的文件直接跳过
            continue
        ratio = height / width
        if ratio < args.ratio_min:
            continue
        if args.ratio_max and ratio > args.ratio_max:
            continue
        decoded += 1
        total += 1
        info = _analyze(rel, args.detail)
        if info is None or "error" in info:
            print(f"  !! {iid[:8]} 检测失败：{(info or {}).get('error', '文件缺失')}")
            continue
        if info["listed"]:
            listed += 1
        if args.only == "listed" and not info["listed"]:
            continue
        if args.only == "unlisted" and info["listed"]:
            continue
        print(
            f"  {iid[:8]} {info['width']}x{info['height']} ratio={info['ratio']:.2f} "
            f"conf={info['confidence']} listed={info['listed']} | {info['reason']}"
        )
        bounds = info["bounds"]
        if bounds is not None:
            print(
                f"    kind={bounds['kind']} top={bounds['top_frac']:.4f} "
                f"bot={bounds['bottom_frac']:.4f} "
                f"resid_top={bounds['residual_top_frac']:.4f} "
                f"glyph_found={bounds.get('glyph_found')} "
                f"strong={bounds.get('glyph_strong')}"
            )
        if args.detail and "debug" in info["glyph"]:
            _print_glyph_debug(info["glyph"]["debug"])
    print(
        f"\n扫描完成：ratio ≥ {args.ratio_min} 的素材 {total} 张，"
        f"列出候选 {listed} 张，排除 {total - listed} 张"
    )
    return 0


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="content 模式（抖音截图内容边界检测）候选资格诊断",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ids", help="素材 ID 列表（完整 UUID 或 8 位前缀，逗号/空格分隔）")
    parser.add_argument("--ids-file", help="ID 文件（每行一个，# 开头为注释）")
    parser.add_argument(
        "--expect",
        choices=["listed", "unlisted", "any"],
        default="any",
        help="期望裁决结果：不符合时退出码 1（默认 any 只输出不校验）",
    )
    parser.add_argument("--detail", action="store_true", help="输出字形连通域明细")
    parser.add_argument("--scan", action="store_true", help="全库扫描（替代 --ids）")
    parser.add_argument(
        "--ratio-min",
        type=float,
        default=1.3,
        help="扫描时的高/宽下限预筛（默认 1.3，与 content 模式竖屏下限一致）",
    )
    parser.add_argument(
        "--ratio-max",
        type=float,
        default=0.0,
        help="扫描时的高/宽上限预筛（默认 0 表示不限）",
    )
    parser.add_argument("--limit", type=int, default=0, help="扫描最多检测多少张（0 不限）")
    parser.add_argument(
        "--only",
        choices=["all", "listed", "unlisted"],
        default="all",
        help="扫描输出过滤（默认 all）",
    )
    args = parser.parse_args()
    if args.scan:
        return _run_scan(args)
    return _run_ids(args)


if __name__ == "__main__":
    sys.exit(main())
