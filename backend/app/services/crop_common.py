"""手机图裁剪的公共助手：竖屏比例常数与存储路径解析（crop_* 系列共用）。

从 `services/crop_service.py` 拆出的一小块。**必须先于** `crop_scan.py` 抽出：候选扫描
与裁剪两条链路都要用这几个名字，任何一个留给原模块都会让新模块反向 import 造成循环。

- ``MIN_RATIO`` / ``CONTENT_MIN_RATIO``：竖屏截图判定下限（完整截图 / content 模式；
  content 模式放宽的原因与「分两段扫描」的由来见下方长注释）；
- ``DEFAULT_CROP_TOP`` / ``DEFAULT_CROP_BOTTOM``：默认裁掉的顶部/底部比例；
- ``_resolve_storage_path``：库内相对路径 → 存储根内的绝对路径（含越界防御）。
"""

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


# 竖屏截图判定：高/宽 ≥ 1.75（9:16≈1.78、19.5:9≈2.17）
MIN_RATIO = 1.75
# content 模式的竖屏下限放宽到 1.3：已被裁剪过的截图比例可能掉到 1.3~1.75
# （原 2.17 裁 40% 后 ≈1.3），顶部状态栏残留仍需二次裁剪；内容边界检测
# 自带「内容区占比」与「残留簇 + 后随内容更高」校验兜底，不会误裁普通照片。
# ⚠ 这个区间按「第二轮」扫描，不与完整截图混在一轮里——原因见 _STAGE_FULL/_STAGE_CROPPED。
CONTENT_MIN_RATIO = 1.3

# ── content 模式分段扫描（2026-09 实测修复）──
# 检测每张要完整解码（实测 ~85 ms），60 秒预算只够 ~630 张；而全库 9,714 张手动上传里
# 只有 1,578 张是完整截图比例（ratio ≥ _FULL_SCREENSHOT_RATIO），合格候选 618 张**全部**
# 在这一段；1.75~2.0 的 940 张合格 0 张，1.3~1.75 的 6,860 张只有 2 张（ratio 1.69、
# 字形强证据命中——状态栏大多已在第一次裁剪时被切掉）。旧实现把两段混在一起按 id 随机
# 顺序解码，实测连扫两轮（627 / 597 张）候选为 0 —— 用户看到的就是「内容边界检测失效」。
# 现在分两段：先扫完整截图（高产出，第一轮就出候选），扫完再扫已裁过的（只认强字形证据）。
# 默认裁剪比例（相对图片高度）
DEFAULT_CROP_TOP = 0.03  # 顶部 3%（状态栏区域）
DEFAULT_CROP_BOTTOM = 0.05  # 底部 5%（底部导航栏/手势条区域）


def _resolve_storage_path(rel_path: str | None) -> Path | None:
    """把库中相对路径解析为存储根内的绝对路径；为空或越出存储根时返回 None。

    防御性校验：素材记录若被篡改（如 ``../`` 越界路径），拒绝访问存储根之外的文件。

    参数:
        rel_path: 库中存储的相对路径（如 images/2026-08/xxx.jpg）

    返回:
        存储根内的绝对路径；路径为空或解析后越出存储根时返回 None
    """
    if not rel_path:
        return None
    full = (settings.storage_root / rel_path).resolve()
    if not full.is_relative_to(settings.storage_root.resolve()):
        logger.warning(f"素材文件路径越出存储根，已拒绝访问: {rel_path}")
        return None
    return full
