"""标签类别体系优化：PascalCase 重命名 + body_part 存量重分类

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-08-29

背景：9417 个标签中 item_type 5109 / body_part 3995（96.6%），body_part 混杂
款式特征/面料/剪裁；类别命名混用 PascalCase（Atmosphere/Expression/Leg_Posture）
与 snake_case。本迁移做两件事：
1. 类别重命名：Atmosphere→atmosphere、Expression→expression、
   Leg_Posture→leg_posture（与前端/提示词的新 snake_case 体系对齐）；
2. body_part 重分类：按标签名关键词规则拆分到 material（面料）/ design_detail
   （款式细节）/ fit（版型），都不命中的留在 body_part（遗留类别）。

幂等性：重命名只作用于旧值（重跑时旧值已不存在，无行受影响）；重分类只扫描
仍为 body_part 的行（命中规则的行已被移走，重跑时剩余行均不命中规则）。
downgrade 取舍：design_detail / material 仅由本迁移产生，可整体映射回 body_part；
fit 与原有 fit 类别混合，无法区分来源，不做回退（见 downgrade 注释）。
"""
import re

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None

# PascalCase → snake_case 重命名映射
_CATEGORY_RENAMES = [
    ("Atmosphere", "atmosphere"),
    ("Expression", "expression"),
    ("Leg_Posture", "leg_posture"),
]

# body_part 重分类规则（按顺序取第一个命中的类别）：
# 1) 面料词 → material：针织/牛仔/蕾丝/哑光/透肉等材质与质感描述
_MATERIAL_PATTERN = re.compile(
    r"面料|蕾丝|网纱|牛仔|灯芯绒|哑光|缎|绸|针织|毛呢|皮革|皮$|羊绒|羊毛|丝绒"
    r"|纯棉|棉质|网眼|亮片|透明|透肉|弹性|无缝|雪纺|涤|锦纶|尼龙|绒$|纱$|棉$"
)
# 2) 款式词 → design_detail：袖型/领型/腰位/口袋/系带/鞋头跟型等结构性设计特征
_DESIGN_DETAIL_PATTERN = re.compile(
    r"袖|领|腰|摆|口袋|拉链|纽扣|扣|系带|绑带|连帽|吊带|开衩|开叉|荷叶边|花边|褶"
    r"|垫肩|露肩|露背|前襟|无痕|高跟|平底|厚底|尖头|圆头|鱼嘴|玛丽珍|绑腕"
)
# 3) 版型词 → fit：合身程度与整体轮廓剪裁
_FIT_PATTERN = re.compile(
    r"紧身|宽松|修身|贴身|廓形|直筒|收腰|包臀|oversize|阔腿|铅笔|A字|伞状",
    re.IGNORECASE,
)


def _classify_body_part(name: str) -> str | None:
    """按标签名关键词判定 body_part 标签的新类别；都不命中返回 None（留在原类别）。"""
    if _MATERIAL_PATTERN.search(name):
        return "material"
    if _DESIGN_DETAIL_PATTERN.search(name):
        return "design_detail"
    if _FIT_PATTERN.search(name):
        return "fit"
    return None


def upgrade() -> None:
    """类别重命名 + body_part 按关键词规则重分类（幂等可重跑）。"""
    conn = op.get_bind()

    # 1) PascalCase → snake_case 重命名（仅旧值受影响，重跑无行命中）
    for old, new in _CATEGORY_RENAMES:
        conn.execute(
            sa.text("UPDATE tags SET category = :new WHERE category = :old"),
            {"new": new, "old": old},
        )

    # 2) body_part 重分类：命中多条规则时按 material → design_detail → fit 顺序取第一个
    rows = conn.execute(
        sa.text("SELECT id, name FROM tags WHERE category = 'body_part'")
    ).fetchall()
    for tag_id, name in rows:
        new_category = _classify_body_part(name or "")
        if new_category:
            conn.execute(
                sa.text("UPDATE tags SET category = :cat WHERE id = :id"),
                {"cat": new_category, "id": tag_id},
            )


def downgrade() -> None:
    """回退：design_detail / material 整体映射回 body_part + 恢复 PascalCase 重命名。

    取舍说明：fit 不回退——升级后 fit 类别同时包含原有 fit 标签与本次从
    body_part 重分类来的标签，二者已无法区分，强行回退会把原有 fit 标签
    错误地移入 body_part，故保留。
    """
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE tags SET category = 'body_part' "
            "WHERE category IN ('design_detail', 'material')"
        )
    )
    for old, new in _CATEGORY_RENAMES:
        conn.execute(
            sa.text("UPDATE tags SET category = :old WHERE category = :new"),
            {"new": new, "old": old},
        )
