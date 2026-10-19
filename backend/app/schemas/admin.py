"""`admin` 模块的请求/响应模型（从 routers/ 归位，见 CLAUDE.md：schemas/ 放 Pydantic 模型）。"""

from pydantic import BaseModel, Field

class NearDuplicateScanRequest(BaseModel):
    """近似重复检测请求参数（扫描动作 + 哈希缓存补算副作用，故用 POST）。"""

    # limit=0 表示全量扫描（服务层约定）：phash 已缓存时分组是纯内存运算，
    # 万级素材也在秒级完成，比「随机抽样漏检后反复扫」体验更好。随机抽样
    # 仍可显式传 500/1000/2000/5000；上限 100000 仅作防御，实际素材数远低于此。
    limit: int = Field(1000, ge=0, le=100000, description="随机抽样图片数量上限；0 表示全量扫描")
    threshold: int = Field(32, ge=1, le=256, description="汉明距离阈值（越小越严格，768 位 RGB dHash）")


class VectorBackfillRequest(BaseModel):
    """向量回填触发参数。"""

    rebuild_text: bool = Field(
        default=False,
        description="为 True 时全量重建文本向量（公式版本升级后启用正文语义搜索），跳过图像向量",
    )


class PhoneCropRequest(BaseModel):
    """手机图剪裁请求参数。"""

    mode: str = Field(default="auto", description="auto 黑边自动检测 / ratio 固定比例 / content 内容边界检测")
    crop_top: float = Field(default=0.03, ge=0, lt=0.5, description="顶部裁剪比例（仅 ratio 模式）")
    crop_bottom: float = Field(default=0.05, ge=0, lt=0.5, description="底部裁剪比例（仅 ratio 模式）")
    limit: int = Field(default=200, ge=1, le=1000, description="单次最多返回候选数")
    cursor: str | None = Field(default=None, description="分页游标（上一批返回的 next_cursor，继续扫描剩余素材）")
    time_budget: float = Field(default=60.0, ge=5, le=300, description="单次扫描时间预算（秒），预算耗尽返回已找到候选")
    vlm_review: bool = Field(default=True, description="AI 复核（较慢，约 1.3s/候选）：VLM 判断顶部状态栏/底部进度条，阳性候选置顶并标注")


class PhoneCropApplyRequest(BaseModel):
    """手机图剪裁执行请求：用户勾选确认的素材 ID 列表。"""

    ids: list[str] = Field(..., description="勾选确认要裁剪的素材 ID 列表")
    mode: str = Field(default="auto", description="auto 黑边自动检测 / ratio 固定比例 / content 内容边界检测")
    crop_top: float = Field(default=0.03, ge=0, lt=0.5, description="顶部裁剪比例（仅 ratio 模式）")
    crop_bottom: float = Field(default=0.05, ge=0, lt=0.5, description="底部裁剪比例（仅 ratio 模式）")
