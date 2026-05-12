"""add SP2 containerization and kubernetes_connector change types

Revision ID: 045
Revises: f510f4f16763
Create Date: 2026-05-12
"""
from alembic import op

revision = '045'
down_revision = 'f510f4f16763'
branch_labels = None
depends_on = None


def upgrade():
    for t in [
        'eks_cluster_create_sdk',
        'eks_cluster_create_cfn',
        'eks_cluster_create_terraform',
        'ecr_repository_create',
        'ecr_repository_delete',
        'kubernetes_connector_create',
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
