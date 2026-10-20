"""手机图裁剪的**候选扫描**（切片 ⑤）：一屏全扫描 + 单张候选判定 + VLM 主判。

从 `services/crop_service.py` 拆出。这里只读不写：按两段（完整截图 / 已裁过的）扫描
竖屏候选、逐张解码判定，最后用 VLM 复核候选里是否还残留状态栏；真正的裁剪、备份、
写库、向量入队都在 `crop_service.py` 的 apply 链路里。

为什么和 apply 分家：扫描是「纯读 + 高 CPU」，apply 是「写文件 + 写库 + 事务」，
两者失败处理与测试关注点完全不同，混在一个 1000 行文件里时扫描占了三分之一。

公共名字（比例常数、``_resolve_storage_path``）在 `crop_common.py`——先抽它，
扫描模块才不会反向 import 原模块（那才是循环导入）。
"""

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inspiration import NOT_DELETED, Inspiration
from app.services.crop_candidates import (
    _STAGE_CROPPED,
    _STAGE_FULL,
    _content_candidate_qualified,
    _resolve_scan_cursor,
)
from app.services.crop_common import (
    CONTENT_MIN_RATIO,
    DEFAULT_CROP_BOTTOM,
    DEFAULT_CROP_TOP,
    MIN_RATIO,
    _resolve_storage_path,
)
from app.services.crop_vlm import _vlm_residue_review
from app.services.image_cropping import (
    _FULL_SCREENSHOT_RATIO,
    analyze_screenshot_combined,
    detect_photo_band,
    detect_screenshot_features,
    probe_size as _probe_size,
    screenshot_confidence,
)

logger = logging.getLogger(__name__)


async def scan_candidates(
    db: AsyncSession,
    mode: str = "auto",
    crop_top: float = DEFAULT_CROP_TOP,
    crop_bottom: float = DEFAULT_CROP_BOTTOM,
    limit: int = 200,
    cursor: str | None = None,
    time_budget: float = 60.0,
    vlm_review: bool = False,
) -> dict:
    """扫描手动上传素材中的手机全屏截图候选（只读，不执行任何裁剪）。

    参数:
        db: 数据库会话
        mode: auto（黑边自动检测——小红书截图：上下黑边包夹图片主体，双侧
            黑边才默认勾选）/ ratio（统一按比例裁剪）/
            content（内容边界检测——抖音截图：状态栏/播放器条残留，按字形证据
            勾选。
            自动化口径——只列手机截图候选：须检出系统 UI 特征（状态栏/
            导航栏）或状态栏残留信号；无 UI 证据的普通竖屏照片静默排除，
            检测失败（无内容区边界）的同样排除。
            **分段扫描**：先扫「完整截图比例」（ratio ≥ 2.0，抖音截图正常形态，
            合格候选几乎都在这一段），这一段扫完再扫「已裁过 / 长竖图」
            （1.3~2.0，只认字形强证据）——检测每张要完整解码 ~85ms，混在一起扫会
            把 60 秒预算全花在几乎不可能入选的素材上，连扫多轮都零候选）
        crop_top: 顶部裁剪比例（仅 ratio 模式生效）
        crop_bottom: 底部裁剪比例（仅 ratio 模式生效）
        limit: 单次最多返回的候选数（0 表示不限制）
        cursor: 分页游标（上一批返回的 next_cursor，格式 ``<阶段>:<素材 id>``）。
            素材按 id 全序分批扫描（id 为 UUID，顺序稳定但与上传时间无关——只保证
            分批不重不漏，不代表批次新旧）；content 模式的阶段游标先 ``full:`` 后
            ``cropped:``，前端原样回传即可
        time_budget: 单次扫描的时间预算（秒）。素材量大（5000+ 竖屏）时
            全量检测远超前端请求超时，预算耗尽即返回已找到的候选并置
            truncated=True，由前端提示用户继续扫描。

    返回:
        {
            "total": 本次扫描列入候选的数量（受 limit 封顶，等于 len(items））,
            "items": [候选列表，同旧结构],
            "scanned": 本次实际扫描的素材数,
            "next_cursor": 截断时下一次扫描的起点（``<阶段>:<素材 id>``）；扫完返回 None,
            "truncated": 是否因时间预算/候选上限提前结束（还有未扫描素材）,
        }
    """
    if mode not in ("auto", "ratio", "content"):
        raise ValueError(f"不支持的裁剪模式: {mode}（允许 auto / ratio / content）")
    if mode == "ratio" and crop_top + crop_bottom >= 1:
        raise ValueError(f"裁剪比例合计必须 < 1：顶部 {crop_top} + 底部 {crop_bottom}")
    # content 模式放宽竖屏下限（被裁剪过的截图比例可低至 1.3）
    min_ratio = MIN_RATIO if mode != "content" else CONTENT_MIN_RATIO

    stage, cursor_id = _resolve_scan_cursor(cursor)

    query = select(Inspiration).where(
        Inspiration.source_type == "manual_upload",
        NOT_DELETED,
        Inspiration.file_path.isnot(None),
        Inspiration.media_type == "image",
    )
    if cursor_id is not None:
        query = query.where(Inspiration.id < cursor_id)
    query = query.order_by(Inspiration.id.desc())
    result = await db.execute(query)

    candidates: list[dict] = []
    scanned = 0
    deferred = 0  # content 模式：属于另一段的素材数（决定是否还要切到下一段）
    truncated = False
    last_insp_id: str | None = None
    deadline = time.monotonic() + max(1.0, time_budget)
    for insp in result.scalars():
        # 时间预算检查（检测是耗时大头，循环级检查粒度足够）
        if time.monotonic() >= deadline:
            truncated = True
            break
        scanned += 1
        last_insp_id = insp.id
        item, postponed = await _scan_one_candidate(
            insp, mode, crop_top, crop_bottom, min_ratio, stage
        )
        if postponed:
            deferred += 1
        if item is None:
            continue
        candidates.append(item)
        # 候选上限检查必须放在 append 之后：此时当前候选已入选，断点游标
        # （last_insp_id）指向它，续扫从它之后接续，不重不漏。若放在 append
        # 之前，边界候选会被计入 total 却不入选，续扫游标又跳过它 → 永久丢失
        if limit > 0 and len(candidates) >= limit:
            truncated = True
            break
    # ── VLM 主判（方案 A）：对入围候选逐张裁决（口径见 _apply_vlm_review）──
    vlm_hit = 0
    vlm_removed = 0
    if vlm_review and candidates:
        candidates, vlm_hit, vlm_removed = await _apply_vlm_review(candidates, deadline)
    # 排序：VLM 复核阳性 > 其余候选；同组内按上传时间倒序
    candidates.sort(
        key=lambda c: (bool(c.get("vlm_residue")), c.get("created_at") or ""),
        reverse=True,
    )
    # 分段：完整截图段扫完而库里还有「已裁过」素材时，把游标切到第二段（从头开始扫）
    next_cursor: str | None = None
    if mode == "content" and stage == _STAGE_FULL and not truncated and deferred:
        truncated = True
        next_cursor = f"{_STAGE_CROPPED}:"
    else:
        next_cursor = (
            f"{stage}:{last_insp_id}" if truncated and last_insp_id is not None else None
        )
    return {
        "total": len(candidates),
        "items": candidates,
        "scanned": scanned,
        "next_cursor": next_cursor,
        "truncated": truncated,
        "vlm_reviewed": bool(vlm_review and candidates),
        "vlm_hits": vlm_hit,
        "vlm_removed": vlm_removed,
    }




async def _scan_one_candidate(
    insp: Inspiration,
    mode: str,
    crop_top: float,
    crop_bottom: float,
    min_ratio: float,
    stage: str = _STAGE_FULL,
) -> tuple[dict | None, bool]:
    """检测单个素材是否为手机截图候选。

    只读、不落库：写库与候选上限/时间预算由 scan_candidates 的循环负责。

    返回:
        ``(候选条目或 None, 是否因「不属于本轮阶段」被推迟到下一段)``。content 模式
        分「完整截图 / 已裁过」两段扫描（见 :data:`_STAGE_FULL`），不属于本轮阶段的
        素材**不做解码**（贵在解码），只回报被推迟，供调用方决定是否切段。
    """

    full = _resolve_storage_path(insp.file_path)
    if full is None or not full.exists():
        return None, False
    try:
        # PIL 文件头读取是阻塞 I/O，放线程池执行（与下方 detect_* 一致）
        width, height = await asyncio.to_thread(_probe_size, full)
    except Exception:
        return None, False  # 无法解码的图片不做候选
    ratio = height / width
    if ratio < min_ratio:
        return None, False
    if mode == "content":
        is_full_screenshot = ratio >= _FULL_SCREENSHOT_RATIO
        if (stage == _STAGE_FULL) is not is_full_screenshot:
            return None, True  # 交给另一段（先完整截图，再已裁过）

    # content 模式：单次解码合并「截图特征 + 内容边界」（性能关键——
    # 全量扫描 5000+ 张时每张只解码一次，旧实现要解码三次）
    bounds_result = None
    if mode == "content":
        try:
            features, bounds_result = await asyncio.to_thread(
                analyze_screenshot_combined, full
            )
            confidence = screenshot_confidence(features)
        except Exception:
            confidence = "low"
            bounds_result = None
        if bounds_result is None:
            # 检测失败（未检出内容区边界/布局不规则）且无字形证据
            # （combined 已在字形存在时返回字形建议而非 None）：静默排除
            return None, False
        # ── 候选资格（FP/FN 裁决层，口径见 _content_candidate_qualified）──
        if not _content_candidate_qualified(bounds_result, confidence, ratio):
            return None, False
        # 资格通过后即使已裁剪干净、无残留建议也继续列入候选：它是真实
        # 截图（有字形/UI 结构证据），顶部状态栏残留肉眼可见，交给人工
        # 目检勾选（此前把这类素材静默过滤，导致「大部分素材找不到、
        # 无法选中」），item 构造中如实标注
    else:
        # 非 content 模式：截图特征检测（状态栏/底部栏 → 置信度分级）
        try:
            features = await asyncio.to_thread(detect_screenshot_features, full)
            confidence = screenshot_confidence(features)
        except Exception:
            confidence = "low"

    item: dict = {
        "id": insp.id,
        "file_path": str(insp.file_path),
        "width": width,
        "height": height,
        "ratio": round(height / width, 3),
        "crop_top": crop_top,
        "crop_bottom": crop_bottom,
        "auto_ok": True,
        "note": None,
        "auto_checked": None,
        "confidence": confidence,
        "boundary_kind": None,
        "created_at": insp.created_at.isoformat(sep=" ") if insp.created_at else None,
    }
    if mode == "auto":
        try:
            top_px, bottom_px = await asyncio.to_thread(detect_photo_band, full)
            item["crop_top"] = round(top_px / height, 6)
            item["crop_bottom"] = round((height - 1 - bottom_px) / height, 6)
            # 本模式为小红书截图设计：其形态是「上下黑边包夹图片主体」。
            # 双侧黑边才默认勾选；单侧「黑边」多为抖音截图的播放器条或
            # 照片暗部（应走 content 模式处理），保留候选但不自动勾选，
            # 交人工判断
            item["auto_checked"] = item["crop_top"] > 0 and item["crop_bottom"] > 0
        except ValueError as e:
            item["auto_ok"] = False
            item["auto_checked"] = False
            item["note"] = f"自动检测失败：{e}"
    elif mode == "content" and bounds_result is not None:
        # 使用上方缓存的检测结果（content 模式必然拿到边界结果，否则上面已 return）
        item["crop_top"] = bounds_result["top_frac"]
        item["crop_bottom"] = bounds_result["bottom_frac"]
        item["boundary_kind"] = bounds_result["kind"]
        glyph_top_frac = bounds_result.get("glyph_top_frac", 0)
        residual_bottom = bounds_result.get("residual_bottom_frac", 0)
        suggestion = max(bounds_result["residual_top_frac"], glyph_top_frac)
        if (
            bounds_result["top_frac"] == 0
            and suggestion == 0
            and residual_bottom > 0
        ):
            # 仅底部残留（均匀暗带建议）：灰底渐变与播放器条人眼都难区分
            # （20 张误报中 4 张属此类），候选保留但交人工确认，不自动勾选
            item["auto_ok"] = False
            item["crop_bottom"] = residual_bottom
            item["auto_checked"] = False
            item["note"] = (
                f"疑似底部导航条/进度条残留（建议裁剪 {residual_bottom:.1%}），"
                "请预览确认"
            )
        elif bounds_result["top_frac"] == 0 and suggestion > 0:
            # 疑似顶部状态栏残留（透明图标叠加照片——抖音全屏浏览态的
            # 典型特征，或实底状态栏残留）：不自动判定可裁剪（防误裁
            # 普通照片），标注建议比例。
            # 默认勾选条件（四轮真实样本校准的最终口径）：仅系统 UI 证据
            # 齐全（top_bar+bottom_bar=high）。字形时间签名在两类样本上
            # 分布重叠（20 张误报中 5 张签名通过、13 张真残留仅 2 张通过），
            # 不再作为勾选依据；VLM 单问实验同样不达标（7/13、9/20）。
            # 残留建议保留在候选中供人工勾选——误勾代价（裁坏图）远大于
            # 漏勾（手动勾选）。
            item["auto_ok"] = False
            item["crop_top"] = suggestion
            item["auto_checked"] = confidence == "high"
            item["note"] = (
                f"疑似顶部状态栏残留（建议裁剪 {suggestion:.1%}），"
                + ("已默认勾选，请预览确认" if item["auto_checked"] else "请预览确认")
            )
        elif (
            bounds_result["already_cropped"]
            and bounds_result["top_frac"] <= 0
            and bounds_result["residual_top_frac"] <= 0
        ):
            # 检出截图特征（状态栏/导航栏）但未给出可裁建议：可能是已
            # 裁过或界面以内容为主。如实标注，不编造比例，由人工目检决定
            item["auto_ok"] = False
            item["crop_top"] = 0.0
            item["auto_checked"] = False
            item["note"] = "未检出可裁区域（可能已裁剪过），请目检确认"
    return item, False


async def _apply_vlm_review(
    candidates: list[dict], deadline: float
) -> tuple[list[dict], int, int]:
    """VLM 主判（方案 A）：对入围候选逐张判定顶部状态栏/底部进度条，
    算法初筛只负责「圈定疑似范围」（快），最终裁决交给 VLM（准，约
    1.3s/张 × 候选数）——三类结果分别处理：

    - 阳性（检出系统 UI 残留）：保留候选，标注置顶，真残留浮到最前；
    - 阴性（明确未检出）：「算法误判」的候选，按 VLM 主判移除——消除
      用户核心投诉的列表噪音；
    - 未知（Ollama 不可用/判定失败）：保守保留并标注（不误伤真残留，
      也不因服务抖动把疑似全部丢弃）。

    Ollama 不可用时全部落「未知」分支 → 候选完整保留，退回纯算法口径。
    返回 (保留的候选, 阳性数, 移除数)。
    """
    vlm_hit = 0
    vlm_removed = 0
    kept: list[dict] = []
    for idx, c in enumerate(candidates):
        full = _resolve_storage_path(c["file_path"])
        if full is None or not full.exists():
            kept.append(c)
            continue
        try:
            verdict = await _vlm_residue_review(full)
        except Exception as e:
            logger.debug(f"VLM 复核跳过 {c['id']}: {e}")
            verdict = None
        if verdict is True:
            vlm_hit += 1
            c["vlm_residue"] = True
            suffix = "AI 复核：检出系统 UI 残留"
            c["note"] = f"{c['note']}；{suffix}" if c["note"] else suffix
            kept.append(c)
        elif verdict is False:
            # VLM 明确未检出：算法疑似但 VLM 排除 → 从候选移除（主判）
            vlm_removed += 1
            logger.info(f"VLM 主判移除（算法疑似但未检出 UI 残留）: {c['id']}")
            continue
        else:
            # 未知（判定失败/超时）：保守保留，标注待人工确认
            c["vlm_residue"] = None
            suffix = "AI 复核不可用，算法疑似（请人工确认）"
            c["note"] = f"{c['note']}；{suffix}" if c["note"] else suffix
            kept.append(c)
        # 时间预算对 VLM 阶段同样生效，超时中断复核（未复核候选转「未知」保留）
        if time.monotonic() > deadline:
            logger.info(
                f"VLM 复核已达时间预算，中断（候选 {len(candidates)}，"
                f"剩余未复核候选按「未知」保留）"
            )
            for rest in candidates[idx + 1 :]:
                rest["vlm_residue"] = None
                rest["note"] = (
                    f"{rest['note']}；未及 AI 复核（时间预算）"
                    if rest["note"]
                    else "未及 AI 复核（时间预算）"
                )
                kept.append(rest)
            break
    if vlm_hit or vlm_removed:
        logger.info(
            f"VLM 主判: 阳性 {vlm_hit}，移除 {vlm_removed}，"
            f"剩余 {len(kept)} 候选"
        )
    return kept, vlm_hit, vlm_removed
