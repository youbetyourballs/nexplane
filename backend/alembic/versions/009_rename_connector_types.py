"""rename _mock connector types to production names

Revision ID: 009
Revises: 008
Create Date: 2026-05-02 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename each _mock enum value to its production name.
    # PostgreSQL requires: add new value, migrate data, drop old value is not directly
    # supported — instead we use ALTER TYPE ... RENAME VALUE (PostgreSQL 10+).
    op.execute("ALTER TYPE connector_type RENAME VALUE 'aws_mock' TO 'aws'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'azure_mock' TO 'azure'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'cloudflare_mock' TO 'cloudflare'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'okta_mock' TO 'okta'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'paloalto_mock' TO 'paloalto'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'ssh_runner_mock' TO 'ssh'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'active_directory_mock' TO 'active_directory'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'crowdstrike_mock' TO 'crowdstrike'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'tenable_mock' TO 'tenable'")


def downgrade() -> None:
    op.execute("ALTER TYPE connector_type RENAME VALUE 'aws' TO 'aws_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'azure' TO 'azure_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'cloudflare' TO 'cloudflare_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'okta' TO 'okta_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'paloalto' TO 'paloalto_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'ssh' TO 'ssh_runner_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'active_directory' TO 'active_directory_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'crowdstrike' TO 'crowdstrike_mock'")
    op.execute("ALTER TYPE connector_type RENAME VALUE 'tenable' TO 'tenable_mock'")
