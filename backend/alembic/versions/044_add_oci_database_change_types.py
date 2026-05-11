"""add OCI database and observability change types

Revision ID: 044
Revises: 043
Create Date: 2026-05-10
"""
from alembic import op

revision = '044'
down_revision = '043'
branch_labels = None
depends_on = None


def upgrade():
    for t in [
        'oci_adb_create',
        'oci_adb_stop',
        'oci_adb_start',
        'oci_adb_delete',
        'oci_adb_backup',
        'oci_mysql_create',
        'oci_mysql_stop',
        'oci_mysql_start',
        'oci_mysql_delete',
        'oci_alarm_create',
        'oci_alarm_delete',
        'oci_logging_enable',
    ]:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
