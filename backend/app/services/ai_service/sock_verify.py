"""高风险袜类细分二次验证：腿部放大裁剪 + 定向提问。

背景（数据诊断）：8B 视觉模型在整图分析时经常把「黑色连裤袜」看错成
「黑色过膝袜」（看不清袜口/裆部时按常见搭配联想填空，最热单品「黑色过膝袜」
占产出 24%，远超真实比例）。仅靠提示词防守不根治——模型的视觉判据
（袜口边、裸露大腿、裆部连接）在整图里常不可见。

本模块在对模型输出做**落库前**的二道校验：
- 只对「高风险细分」（过膝袜/大腿袜/长筒袜，与连裤袜最易混淆）触发；
- 用原图**下半身放大裁剪**（腿部是判别信息的主要区域）发给模型一次
  定向提问（只回答袜类判定，无整图其它信息干扰）；
- 验证结果与模型第一轮不一致时改写 items（过膝/大腿袜改判为连裤袜等），
  解析/请求失败一律静默保持原样（保守）。

验证调用失败不阻断分析（降级为第一轮结果），日志 raw_response 始终
保留模型原始输出以便追溯，改写只发生在落库标签上。
"""

import asyncio
import base64
import json
import re
from io import BytesIO

from PIL import Image

from app.config import settings
from app.services.ai_service.common import logger

# 与连裤袜最易混淆的高风险细分（命中才触发二次验证）
RISK_SOCK_MARKERS = ("过膝袜", "大腿袜", "长筒袜")
# 非风险袜类标记（出现则跳过验证，避免把正确的短袜/中筒/堆堆袜也拉去校验）
_SAFE_SOCK_MARKERS = ("中筒袜", "短袜", "及踝袜", "堆堆袜", "踩脚袜", "连裤袜", "吊带袜")

# 定向验证提示词：只回答袜类判定（无颜色/款式/风格干扰，专注判别锚点）
VERIFY_PROMPT = (
    "这是同一套穿搭的腿部放大裁剪图。只输出一个 JSON 对象，不要任何其他文字：\n"
    '{"sock": "过膝袜 | 大腿袜 | 长筒袜 | 连裤袜 | 无袜子 | 不确定"}\n'
    "判定规则（严格按可见证据，不要按常见搭配联想）：\n"
    "- 过膝袜/大腿袜：膝盖上方可见一圈独立袜口边（罗纹/蕾丝/提花收口），"
    "且袜口之上可见裸露大腿皮肤；\n"
    "- 长筒袜：袜口在膝盖下方（膝下或及膝），袜口之上可见膝盖/小腿皮肤；\n"
    "- 连裤袜：腿部从脚到裙摆/画面顶端被同一连续布料包裹，看不到任何独立袜口边，"
    "大腿根区域两腿布料相连（裆部）或上沿完全看不到；\n"
    "- 无袜子：只有裸露皮肤（光腿），或腿部被长靴/长裤等各类鞋裤覆盖（那不是袜子）；\n"
    "- 证据不足、看不清 → 不确定。"
)


def find_risky_sock_types(tags_data: dict) -> list[str]:
    """从解析结果 items 中找出高风险袜类 type 列表（过膝袜/大腿袜/长筒袜）。

    仅命中含「风险细分」且不含「安全袜类」的条目（如「黑色吊带袜」不会误触发；
    「蕾丝过膝袜」命中；「白色堆堆袜」跳过）。
    """
    risky: list[str] = []
    items = tags_data.get("items") if isinstance(tags_data, dict) else None
    if not isinstance(items, list):
        return risky
    for it in items:
        if not isinstance(it, dict):
            continue
        ty = str(it.get("type", ""))
        if any(m in ty for m in RISK_SOCK_MARKERS) and not any(m in ty for m in _SAFE_SOCK_MARKERS):
            risky.append(ty)
    return risky


def _extract_color_prefix(type_name: str) -> str:
    """从 type 提取颜色前缀（如「黑色过膝袜」→「黑色」），供改写目标词复用。

    取袜类标记词（过膝袜/大腿袜/长筒袜）之前的全部文本作为前缀；
    模型命名惯例是「颜色(+款式) + 袜类词」，前缀即颜色部分。
    """
    idx = len(type_name)
    for marker in RISK_SOCK_MARKERS:
        i = type_name.find(marker)
        if i >= 0 and i < idx:
            idx = i
    return type_name[:idx] if idx > 0 else ""


def _replace_type(orig: str, target: str) -> str:
    """构造改写后的 type：保持颜色前缀 + 目标袜类词（保留特征细节由 features 承载）。"""
    color = _extract_color_prefix(orig)
    return f"{color}{target}" if color else target


def _crop_lower_body_sync(file_path: str, ratio: float = 0.6, max_side: int = 1024) -> str:
    """裁剪图片下半身（腿部判别区）并转 JPEG base64；失败抛异常由调用方兜底。"""
    storage_root = settings.storage_root.resolve()
    full_path = (storage_root / file_path).resolve()
    full_path.relative_to(storage_root)  # 路径穿越防护（与 _read_image_base64 同口径）
    with Image.open(full_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        # 腿部通常在画面下半部：裁出底部 ratio 高度
        top = int(h * (1.0 - ratio))
        im = im.crop((0, top, w, h))
        # 缩到长边 ≤max_side，控制 token 成本
        scale = min(1.0, max_side / max(im.size))
        if scale < 1.0:
            im = im.resize((int(im.width * scale), int(im.height * scale)))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def _crop_lower_body(file_path: str, ratio: float = 0.6, max_side: int = 1024) -> str:
    """异步裁剪下半身（线程池执行）。"""
    return await asyncio.to_thread(_crop_lower_body_sync, file_path, ratio, max_side)


def _parse_verdict(raw: str) -> str | None:
    """解析定向验证的 JSON 答案；合法且未知词返回 None（保持原样的保守语义）。"""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    sock = str(obj.get("sock", "")).strip()
    return sock if sock in ("过膝袜", "大腿袜", "长筒袜", "连裤袜", "无袜子", "不确定") else None


async def maybe_verify_socks(
    file_path: str,
    tags_data: dict,
    model_cfg: dict,
    model_name: str,
    inspiration_id: str,
) -> dict:
    """对含高风险袜类的解析结果做二次验证；无风险或验证失败返回原数据。

    改写语义（与验证答案一一对应）：
    - 过膝袜/大腿袜 → 保持原样（模型确认了膝上袜口证据）；
    - 长筒袜 → 改写为「{颜色}长筒袜」（膝下筒袜，降级细分）；
    - 连裤袜 → 改写为「{颜色}连裤袜」（本轮最大收益：过膝袜误报纠正为连裤袜）；
    - 无袜子 → 删除该 item（验证发现根本没穿袜子，整图阶段是幻觉）；
    - 不确定 / 解析失败 / 请求异常 → 保持原样（保守，不误伤）。
    """
    risky = find_risky_sock_types(tags_data)
    if not risky:
        return tags_data

    try:
        crop_data = await _crop_lower_body(file_path)
    except Exception as e:
        logger.warning(f"袜类验证裁剪失败（跳过验证，按原结果保存）{inspiration_id}: {e}")
        return tags_data

    try:
        from app.services.ai_service.analyze import _call_ollama_vision

        raw = await _call_ollama_vision(
            crop_data,
            VERIFY_PROMPT,
            model_cfg,
            0.0,
            f"{inspiration_id}#sock",
            model_name,
        )
    except Exception as e:
        logger.warning(f"袜类验证调用失败（跳过验证，按原结果保存）{inspiration_id}: {e}")
        return tags_data

    verdict = _parse_verdict(raw)
    if verdict is None:
        logger.info(f"袜类验证无有效判定（保持原样）{inspiration_id}: {raw[:120]}")
        return tags_data
    logger.info(
        f"袜类二次验证 {inspiration_id}: 原始={risky} → 判定={verdict}"
    )

    items = tags_data.get("items")
    if not isinstance(items, list):
        return tags_data

    new_items: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            new_items.append(it)
            continue
        ty = str(it.get("type", ""))
        if any(m in ty for m in RISK_SOCK_MARKERS) and not any(
            m in ty for m in _SAFE_SOCK_MARKERS
        ):
            if verdict in ("过膝袜", "大腿袜"):
                # 验证确认膝上袜口证据：保持原 type
                new_items.append(it)
            elif verdict == "长筒袜":
                it = dict(it)
                it["type"] = _replace_type(ty, "长筒袜")
                new_items.append(it)
            elif verdict == "连裤袜":
                it = dict(it)
                it["type"] = _replace_type(ty, "连裤袜")
                new_items.append(it)
            elif verdict == "无袜子":
                # 验证未发现袜子：删除该 item
                logger.info(f"袜类验证判定无袜子，删除该单品 {inspiration_id}: {ty}")
                continue
            else:  # 不确定：保持原样
                new_items.append(it)
        else:
            new_items.append(it)
    tags_data = dict(tags_data)
    tags_data["items"] = new_items
    return tags_data
