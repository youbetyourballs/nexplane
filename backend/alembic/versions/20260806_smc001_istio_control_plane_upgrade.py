"""add_service_mesh_cicd_change_types

Revision ID: 20260806_smc001
Revises: 20260803_001
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = '20260806_smc001'
down_revision: Union[str, None] = '20260803_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'istio_control_plane_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'kong_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'cert_manager_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'gitlab_upgrade'")
        op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'jenkins_upgrade'")


def downgrade() -> None:
    pass  # PostgreSQL cannot drop enum values
