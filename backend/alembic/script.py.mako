"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    """执行升级迁移。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """执行降级迁移（回滚）。"""
    ${downgrades if downgrades else "pass"}
