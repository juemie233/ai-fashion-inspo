"""手机图剪裁增强分析：最近 N 张手动上传素材的行剖面诊断（只读，不修改任何数据）。

用途
    现有裁剪功能（crop_service）的黑边检测面向「深色背景截图」，固定比例模式
    靠拍脑袋参数。本脚本读取最近上传的手动素材，逐张分析行级特征，验证两类
    假设并统计参数分布，为增强检测算法提供数据依据：

    类型一（灰地带包夹）：图片上下有明显分界线，界限外侧是灰色/低饱和地带，
        内容照片在中间，裁剪应从分界处进行；
    类型二（状态栏 + 进度条）：顶部是手机状态栏（界限不明显），底部有进度条/
        导航栏（界限较明显），需要多次实验获得参数。

    每张图输出行级特征（亮度/饱和度/颜色多样度）与候选分界位置，最后汇总
    各类型计数与分界位置分布，供人工核对后落地为检测算法。

用法
    python scripts/analyze_crop_candidates.py [--limit 100] [--db backend/fashion_inspo.db]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

# 分析宽度（统一缩放，降低计算量；行剖面按相对高度输出，与绝对分辨率无关）
ANALYZE_W = 96
# 状态栏/底部条特征检测的扫描窗口（相对全图高度，仅用于类型二标注）
STATUS_BAR_SCAN_FRACTION = 0.16
BOTTOM_BAR_SCAN_FRACTION = 0.14
# 多样度阈值（沿用 crop_service 校准值）
ROW_UNIFORM = 0.1  # 近纯色行
ROW_STATUS_BAR = 0.3  # 状态栏行（含图标）上限
ROW_CONTENT = 0.25  # 内容区下限
# 饱和度阈值（0~1）：低于此值视为「灰/无彩色」
SAT_GRAY = 0.2
# 灰带亮度方差阈值（0~1 尺度，纹理少 = 平坦地带）
GRAY_BAND_STD_MAX = 0.06
# 内容区占比合理范围（灰带包夹的照片主体）
CONTENT_FRACTION_MIN = 0.25
CONTENT_FRACTION_MAX = 0.95


def _profile(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算图片的行级剖面：亮度均值、饱和度均值、颜色多样度（0~1，逐行）。

    参数:
        path: 图片绝对路径

    返回:
        (brightness, saturation, diversity) 三个长度 = 缩放后高度的数组
    """
    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
        small = img.resize(
            (ANALYZE_W, max(16, img.height * ANALYZE_W // img.width)),
            Image.Resampling.LANCZOS,
        )
    arr = np.asarray(small).astype(np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    brightness = (r * 0.299 + g * 0.587 + b * 0.114).mean(axis=1)
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    saturation = saturation.mean(axis=1)
    # 颜色多样度：16 级量化后每行唯一色占比
    q = (arr * 16).astype(np.int32)
    diversity = np.array(
        [
            len({(int(v[0]), int(v[1]), int(v[2])) for v in q[y]}) / ANALYZE_W
            for y in range(arr.shape[0])
        ]
    )
    return brightness, saturation, diversity


def _band_stats(brightness: np.ndarray, saturation: np.ndarray, lo: int, hi: int) -> dict:
    """统计行区间的灰色特征（饱和度均值、亮度均值、亮度方差）。

    参数:
        brightness: 行亮度剖面
        saturation: 行饱和度剖面
        lo: 起始行（含）
        hi: 结束行（不含）

    返回:
        {"sat_mean", "bright_mean", "bright_std"}
    """
    if hi <= lo:
        return {"sat_mean": 0.0, "bright_mean": 0.0, "bright_std": 0.0}
    return {
        "sat_mean": float(saturation[lo:hi].mean()),
        "bright_mean": float(brightness[lo:hi].mean()),
        "bright_std": float(brightness[lo:hi].std()),
    }


def _content_bounds(
    diversity: np.ndarray,
    min_d: float = 0.15,
    min_run: int = 3,
    max_gap: int = 5,
) -> tuple[int | None, int | None]:
    """找内容区的上下边界（类型一/类型二统一的「非内容地带包夹」检测）。

    思路：内容行 = 多样度 ≥ min_d 的行。从顶向下找首个「连续 ≥min_run 行
    内容」的区段起点作为 top_edge；从底向上对称找 bottom_edge。扫描时允许
    内容区内部有 ≤max_gap 行的缺口（照片中的纯色块/暗部），避免提前截断。

    参数:
        diversity: 行多样度剖面
        min_d: 内容行多样度下限
        min_run: 内容区起始的最小连续行数（防单行噪声）
        max_gap: 内容区内部允许的最大缺口行数

    返回:
        (top_edge, bottom_edge) 内容区上下边界行；找不到返回 None
    """
    n = len(diversity)
    content = diversity >= min_d

    # 上界：向下扫描，累计缺口；连续内容达到 min_run 即定为起点
    top_edge: int | None = None
    run = 0
    gap = 0
    last_content: int | None = None
    for y in range(n):
        if content[y]:
            if last_content is None or (y - last_content - 1) <= max_gap:
                run += 1
            else:
                run = 1
            last_content = y
            if run >= min_run:
                top_edge = last_content - run + 1
                break
            gap = 0
        else:
            gap += 1

    # 下界：向上扫描对称处理
    bottom_edge: int | None = None
    run = 0
    last_content = None
    for y in range(n - 1, -1, -1):
        if content[y]:
            if last_content is None or (last_content - y - 1) <= max_gap:
                run += 1
            else:
                run = 1
            last_content = y
            if run >= min_run:
                bottom_edge = last_content + run - 1
                break
    return top_edge, bottom_edge


def _status_bar_correction(
    diversity: np.ndarray, top_edge: int
) -> int:
    """状态栏修正：顶部内容簇多样度显著低于后续内容区时，视为状态栏并后移边界。

    顶部状态栏的结构：纯色背景（多样度 < 0.1）→ 图标行簇（多样度 0.15~0.24，
    极薄）→ 内容区（多样度更高）。判定规则（100 张样本校准）：
    首区段（顶部 8% 窗口内）多样度中位 < 0.25、且区段前一行属低多样度地带、
    且区段结束后存在多样度 ≥ max(0.24, 首区段中位+0.05) 的行 → 状态栏，
    内容边界后移到该行。

    参数:
        diversity: 行多样度剖面
        top_edge: 未修正的内容区上边界

    返回:
        修正后的内容区上边界（无法确认状态栏时原样返回）
    """
    n = len(diversity)
    window = int(n * 0.08)
    if top_edge >= window:
        return top_edge
    seg_end = top_edge
    while seg_end + 1 < window and diversity[seg_end + 1] >= 0.15:
        seg_end += 1
    first_med = float(np.median(diversity[top_edge : seg_end + 1]))
    if first_med >= 0.25:
        return top_edge
    if top_edge == 0 or diversity[top_edge - 1] >= 0.1:
        return top_edge
    threshold = max(0.24, first_med + 0.05)
    search_end = min(n, window + int(n * 0.2))
    for y in range(seg_end + 1, search_end):
        if diversity[y] >= threshold:
            return y
    return top_edge


def analyze_one(path: Path) -> dict:
    """分析单张图片：输出内容区边界 + 类型一/类型二候选判定。

    参数:
        path: 图片绝对路径

    返回:
        特征字典（详见输出字段）
    """
    brightness, saturation, diversity = _profile(path)
    n = len(brightness)

    # ── 内容区边界（统一检测：非内容地带包夹 = 灰带/状态栏/播放器条）──
    top_edge_raw, bot_edge = _content_bounds(diversity)
    top_edge = _status_bar_correction(diversity, top_edge_raw) if top_edge_raw is not None else None
    if top_edge is not None and top_edge_raw is not None and top_edge != top_edge_raw:
        pass  # 状态栏修正生效（分类时标记）

    # 边界外侧区域特征（灰带判定：低饱和 + 平坦 + 低多样度）
    top_gray = _band_stats(brightness, saturation, 0, top_edge) if top_edge else None
    bot_gray = (
        _band_stats(brightness, saturation, bot_edge + 1, n) if bot_edge is not None else None
    )
    top_gray_ok = bool(
        top_edge is not None
        and top_gray is not None
        and top_gray["sat_mean"] < SAT_GRAY
        and top_gray["bright_std"] < GRAY_BAND_STD_MAX
    )
    bot_gray_ok = bool(
        bot_edge is not None
        and bot_gray is not None
        and bot_gray["sat_mean"] < SAT_GRAY
        and bot_gray["bright_std"] < GRAY_BAND_STD_MAX
    )
    # 内容区占比合理（25%~95%）
    content_ok = False
    if top_edge is not None and bot_edge is not None and bot_edge > top_edge:
        frac = (bot_edge - top_edge + 1) / n
        content_ok = CONTENT_FRACTION_MIN <= frac <= CONTENT_FRACTION_MAX

    # ── 类型二特征（原有状态栏/底部条检测，用于标注）──
    top_limit = int(n * STATUS_BAR_SCAN_FRACTION)
    bot_start = int(n * (1 - BOTTOM_BAR_SCAN_FRACTION))
    status_bar_y: int | None = None
    for y in range(min(2, top_limit), top_limit):
        if ROW_UNIFORM < diversity[y] <= ROW_STATUS_BAR:
            if any(diversity[j] > ROW_CONTENT for j in range(y, min(n, y + int(n * 0.4)))):
                status_bar_y = y
                break
    bottom_bar_y: int | None = None
    for y in range(bot_start, n - 1):
        if diversity[y] < ROW_UNIFORM and diversity[y + 1] < ROW_UNIFORM:
            if any(diversity[j] > ROW_CONTENT for j in range(max(0, y - int(n * 0.6)), y)):
                bottom_bar_y = y
                break

    # ── 分类 ──
    # 类型一：上下灰带包夹（外侧低饱和平坦）
    type1 = bool(top_gray_ok and bot_gray_ok and content_ok)
    # 类型二：状态栏修正生效 或 原有状态栏+底部条特征
    type2 = bool(
        (top_edge is not None and top_edge_raw is not None and top_edge != top_edge_raw)
        or (status_bar_y is not None and bottom_bar_y is not None)
    )
    # 有内容边界且上下都有可裁区域（无论灰带/状态栏/播放器条）→ 可裁剪候选
    croppable = bool(
        top_edge is not None
        and bot_edge is not None
        and bot_edge > top_edge
        and (top_edge > 0 or bot_edge < n - 1)
        and content_ok
    )

    return {
        "n": n,
        "status_bar_y": status_bar_y,
        "bottom_bar_y": bottom_bar_y,
        "top_edge": top_edge,
        "bot_edge": bot_edge,
        "top_edge_raw": top_edge_raw,
        "top_gray": top_gray or {"sat_mean": 0.0, "bright_mean": 0.0, "bright_std": 0.0},
        "bot_gray": bot_gray or {"sat_mean": 0.0, "bright_mean": 0.0, "bright_std": 0.0},
        "type1": type1,
        "type2": type2,
        "croppable": croppable,
    }


def main() -> None:
    """主入口：读取最近上传素材并输出分析报告。"""
    parser = argparse.ArgumentParser(description="手机图剪裁候选行剖面分析（只读）")
    parser.add_argument("--limit", type=int, default=100, help="分析的素材数（按上传时间倒序）")
    parser.add_argument("--db", default="fashion_inspo.db", help="SQLite 数据库路径")
    parser.add_argument("--storage", default="storage", help="存储根目录（file_path 相对此目录）")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        sys.exit(1)
    storage_root = Path(args.storage)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT id, file_path, created_at FROM inspirations
        WHERE source_type = 'manual_upload'
          AND deleted_at IS NULL
          AND media_type = 'image'
          AND file_path IS NOT NULL
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    conn.close()
    print(f"命中最近 {len(rows)} 张手动上传图片素材（按上传时间倒序）\n")

    results: list[dict] = []
    missing = 0
    for insp_id, rel_path, created_at in rows:
        full = (storage_root / rel_path).resolve()
        if not full.exists():
            missing += 1
            print(f"[{insp_id}] 文件缺失: {rel_path}")
            continue
        try:
            r = analyze_one(full)
        except Exception as e:  # noqa: BLE001 单张失败不影响整体分析
            print(f"[{insp_id}] 分析失败: {e}")
            continue
        r["id"] = insp_id
        r["created_at"] = (created_at or "")[:10]
        results.append(r)
        if r["top_edge"] is not None:
            top_frac = r["top_edge"] / r["n"]
            bot_frac = 1 - r["bot_edge"] / r["n"] if r["bot_edge"] is not None else 0.0
            crop_info = f"建议裁 顶{top_frac:.1%} 底{bot_frac:.1%}"
        else:
            crop_info = "无内容边界"
        flag = " | ".join(
            [
                "类型一(灰带包夹)" if r["type1"] else "",
                "类型二(状态栏+底部条)" if r["type2"] else "",
            ]
        ).strip(" |")
        corr = " [状态栏修正]" if r["top_edge_raw"] != r["top_edge"] else ""
        print(
            f"[{insp_id}] {r['created_at']} {rel_path.split('/')[-1][:30]:<32} "
            f"上界={r['top_edge']} 下界={r['bot_edge']}{corr} "
            f"上灰(sat={r['top_gray']['sat_mean']:.2f},σ={r['top_gray']['bright_std']:.3f}) "
            f"下灰(sat={r['bot_gray']['sat_mean']:.2f},σ={r['bot_gray']['bright_std']:.3f}) "
            f"{crop_info} → {flag or ('可裁剪' if r['croppable'] else '其他')}"
        )

    if not results:
        print("没有可分析的素材")
        return

    n1 = sum(1 for r in results if r["type1"])
    n2 = sum(1 for r in results if r["type2"])
    n_crop = sum(1 for r in results if r["croppable"])
    n_both = sum(1 for r in results if r["type1"] and r["type2"])
    print(
        f"\n===== 汇总（{len(results)} 张，缺失 {missing}）=====\n"
        f"类型一（上下灰带包夹）: {n1} 张（{n1 / len(results):.0%}）\n"
        f"类型二（状态栏+底部条）: {n2} 张（{n2 / len(results):.0%}）\n"
        f"同时命中两类: {n_both} 张\n"
        f"有内容边界可裁剪: {n_crop} 张（{n_crop / len(results):.0%}）"
    )

    # 可裁剪样本的裁剪比例分布
    cropable_list = [r for r in results if r["croppable"] and r["top_edge"] is not None and r["bot_edge"] is not None]
    if cropable_list:
        top_fracs = [r["top_edge"] / r["n"] for r in cropable_list]
        bot_fracs = [1 - r["bot_edge"] / r["n"] for r in cropable_list]
        fracs = np.array([top_fracs, bot_fracs])

        def stat_line(name: str, arr: np.ndarray) -> str:
            return (
                f"  {name}: min={arr.min():.3f} p25={np.percentile(arr, 25):.3f} "
                f"中位={np.median(arr):.3f} p75={np.percentile(arr, 75):.3f} "
                f"max={arr.max():.3f} n={len(arr)}"
            )

        print(
            f"\n建议裁剪比例分布（相对全图高度，n={len(cropable_list)}）:\n"
            + stat_line("顶部裁剪(上界/高)", fracs[0])
            + "\n"
            + stat_line("底部裁剪(1-下界/高)", fracs[1])
        )
        # 上界位置的众数区间（直方图粗分桶，找集中区间）
        for name, arr in [("顶部裁剪", fracs[0]), ("底部裁剪", fracs[1])]:
            hist, edges = np.histogram(arr, bins=10, range=(0.0, 1.0))
            peak = int(np.argmax(hist))
            print(
                f"  {name}最集中区间: [{edges[peak]:.3f}, {edges[peak + 1]:.3f}) "
                f"占比 {hist[peak]}/{len(arr)}"
            )

    # 其他样本：列出 id 供抽查
    others = [r for r in results if not r["croppable"]]
    print(f"\n不可裁剪样本 {len(others)} 张（列出最多 16 个 id 供抽查）:")
    if others:
        sample_ids = (others[:8] + others[-8:]) if len(others) > 16 else others
        print("  " + ", ".join(str(r["id"]) for r in sample_ids))


if __name__ == "__main__":
    main()
