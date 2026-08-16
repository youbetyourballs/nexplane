# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Managed service upgrade live smoke tests.

These tests run against real cloud resources and apply actual version upgrades.
Use non-production resources. All rollbacks are partial (no downgrade path).

Env vars (each group is independent — set only the ones you have):
  SMOKE_ELASTICACHE_GROUP_ID, SMOKE_ELASTICACHE_TARGET_VERSION
  SMOKE_RDS_PG_INSTANCE, SMOKE_RDS_PG_TARGET_VERSION
  SMOKE_RDS_MYSQL_INSTANCE, SMOKE_RDS_MYSQL_TARGET_VERSION
  SMOKE_DOCDB_CLUSTER_ID, SMOKE_DOCDB_TARGET_VERSION
  SMOKE_MEMORYSTORE_INSTANCE, SMOKE_MEMORYSTORE_LOCATION, SMOKE_MEMORYSTORE_TARGET_VERSION
  SMOKE_CLOUD_SQL_INSTANCE, SMOKE_CLOUD_SQL_TARGET_VERSION
  SMOKE_AZURE_REDIS_RG, SMOKE_AZURE_REDIS_CACHE, SMOKE_AZURE_REDIS_TARGET_VERSION
  SMOKE_AZURE_DB_RG, SMOKE_AZURE_DB_SERVER, SMOKE_AZURE_DB_ENGINE, SMOKE_AZURE_DB_TARGET_VERSION
  SMOKE_OCI_ADB_ID, SMOKE_OCI_ADB_TARGET_VERSION
  SMOKE_OCI_REDIS_CLUSTER_ID, SMOKE_OCI_REDIS_TARGET_VERSION
"""
import os
import pytest


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_ELASTICACHE_GROUP_ID") or not os.getenv("SMOKE_ELASTICACHE_TARGET_VERSION"),
    reason="SMOKE_ELASTICACHE_GROUP_ID or SMOKE_ELASTICACHE_TARGET_VERSION not set",
)
async def test_elasticache_redis_upgrade(live_aws_connector):
    from app.connectors.executors.aws.elasticache_redis_upgrade import execute, rollback

    result = await execute(
        {
            "replication_group_id": os.environ["SMOKE_ELASTICACHE_GROUP_ID"],
            "target_version": os.environ["SMOKE_ELASTICACHE_TARGET_VERSION"],
        },
        [],
        live_aws_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_aws_connector)
    assert rb["rolled_back"] is False  # partial — no downgrade path


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_RDS_PG_INSTANCE") or not os.getenv("SMOKE_RDS_PG_TARGET_VERSION"),
    reason="SMOKE_RDS_PG_INSTANCE or SMOKE_RDS_PG_TARGET_VERSION not set",
)
async def test_rds_postgres_upgrade(live_aws_connector):
    from app.connectors.executors.aws.rds_postgres_upgrade import execute, rollback

    result = await execute(
        {
            "db_instance_identifier": os.environ["SMOKE_RDS_PG_INSTANCE"],
            "target_version": os.environ["SMOKE_RDS_PG_TARGET_VERSION"],
        },
        [],
        live_aws_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_aws_connector)
    assert rb["rolled_back"] is False


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_RDS_MYSQL_INSTANCE") or not os.getenv("SMOKE_RDS_MYSQL_TARGET_VERSION"),
    reason="SMOKE_RDS_MYSQL_INSTANCE or SMOKE_RDS_MYSQL_TARGET_VERSION not set",
)
async def test_rds_mysql_upgrade(live_aws_connector):
    from app.connectors.executors.aws.rds_mysql_upgrade import execute, rollback

    result = await execute(
        {
            "db_instance_identifier": os.environ["SMOKE_RDS_MYSQL_INSTANCE"],
            "target_version": os.environ["SMOKE_RDS_MYSQL_TARGET_VERSION"],
        },
        [],
        live_aws_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_aws_connector)
    assert rb["rolled_back"] is False


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_DOCDB_CLUSTER_ID") or not os.getenv("SMOKE_DOCDB_TARGET_VERSION"),
    reason="SMOKE_DOCDB_CLUSTER_ID or SMOKE_DOCDB_TARGET_VERSION not set",
)
async def test_documentdb_upgrade(live_aws_connector):
    from app.connectors.executors.aws.documentdb_upgrade import execute, rollback

    result = await execute(
        {
            "db_cluster_identifier": os.environ["SMOKE_DOCDB_CLUSTER_ID"],
            "target_version": os.environ["SMOKE_DOCDB_TARGET_VERSION"],
        },
        [],
        live_aws_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_aws_connector)
    assert rb["rolled_back"] is False


# ---------------------------------------------------------------------------
# GCP
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_MEMORYSTORE_INSTANCE")
    or not os.getenv("SMOKE_MEMORYSTORE_LOCATION")
    or not os.getenv("SMOKE_MEMORYSTORE_TARGET_VERSION"),
    reason="SMOKE_MEMORYSTORE_INSTANCE, SMOKE_MEMORYSTORE_LOCATION, or SMOKE_MEMORYSTORE_TARGET_VERSION not set",
)
async def test_memorystore_redis_upgrade(live_gcp_connector):
    from app.connectors.executors.gcp.memorystore_redis_upgrade import execute, rollback

    result = await execute(
        {
            "instance_name": os.environ["SMOKE_MEMORYSTORE_INSTANCE"],
            "location": os.environ["SMOKE_MEMORYSTORE_LOCATION"],
            "target_version": os.environ["SMOKE_MEMORYSTORE_TARGET_VERSION"],
        },
        [],
        live_gcp_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_gcp_connector)
    assert rb["rolled_back"] is False


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_CLOUD_SQL_INSTANCE") or not os.getenv("SMOKE_CLOUD_SQL_TARGET_VERSION"),
    reason="SMOKE_CLOUD_SQL_INSTANCE or SMOKE_CLOUD_SQL_TARGET_VERSION not set",
)
async def test_cloud_sql_upgrade(live_gcp_connector):
    from app.connectors.executors.gcp.cloud_sql_upgrade import execute, rollback

    result = await execute(
        {
            "instance_name": os.environ["SMOKE_CLOUD_SQL_INSTANCE"],
            "target_version": os.environ["SMOKE_CLOUD_SQL_TARGET_VERSION"],
        },
        [],
        live_gcp_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_gcp_connector)
    assert rb["rolled_back"] is False


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_AZURE_REDIS_CACHE")
    or not os.getenv("SMOKE_AZURE_REDIS_RG")
    or not os.getenv("SMOKE_AZURE_REDIS_TARGET_VERSION"),
    reason="SMOKE_AZURE_REDIS_CACHE, SMOKE_AZURE_REDIS_RG, or SMOKE_AZURE_REDIS_TARGET_VERSION not set",
)
async def test_azure_cache_redis_upgrade(live_azure_connector):
    from app.connectors.executors.azure.azure_cache_redis_upgrade import execute, rollback

    result = await execute(
        {
            "resource_group": os.environ["SMOKE_AZURE_REDIS_RG"],
            "cache_name": os.environ["SMOKE_AZURE_REDIS_CACHE"],
            "target_version": os.environ["SMOKE_AZURE_REDIS_TARGET_VERSION"],
        },
        [],
        live_azure_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_azure_connector)
    assert rb["rolled_back"] is False


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_AZURE_DB_SERVER")
    or not os.getenv("SMOKE_AZURE_DB_RG")
    or not os.getenv("SMOKE_AZURE_DB_ENGINE")
    or not os.getenv("SMOKE_AZURE_DB_TARGET_VERSION"),
    reason="SMOKE_AZURE_DB_SERVER, SMOKE_AZURE_DB_RG, SMOKE_AZURE_DB_ENGINE, or SMOKE_AZURE_DB_TARGET_VERSION not set",
)
async def test_azure_database_upgrade(live_azure_connector):
    from app.connectors.executors.azure.azure_database_upgrade import execute, rollback

    result = await execute(
        {
            "resource_group": os.environ["SMOKE_AZURE_DB_RG"],
            "server_name": os.environ["SMOKE_AZURE_DB_SERVER"],
            "engine": os.environ["SMOKE_AZURE_DB_ENGINE"],
            "target_version": os.environ["SMOKE_AZURE_DB_TARGET_VERSION"],
        },
        [],
        live_azure_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_azure_connector)
    assert rb["rolled_back"] is False


# ---------------------------------------------------------------------------
# OCI
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_OCI_ADB_ID") or not os.getenv("SMOKE_OCI_ADB_TARGET_VERSION"),
    reason="SMOKE_OCI_ADB_ID or SMOKE_OCI_ADB_TARGET_VERSION not set",
)
async def test_oci_adb_upgrade(live_oci_connector):
    from app.connectors.executors.oci.oci_adb_upgrade import execute, rollback

    result = await execute(
        {
            "adb_id": os.environ["SMOKE_OCI_ADB_ID"],
            "target_version": os.environ["SMOKE_OCI_ADB_TARGET_VERSION"],
        },
        [],
        live_oci_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_oci_connector)
    assert rb["rolled_back"] is False


@pytest.mark.smoke
@pytest.mark.skipif(
    not os.getenv("SMOKE_OCI_REDIS_CLUSTER_ID") or not os.getenv("SMOKE_OCI_REDIS_TARGET_VERSION"),
    reason="SMOKE_OCI_REDIS_CLUSTER_ID or SMOKE_OCI_REDIS_TARGET_VERSION not set",
)
async def test_oci_cache_upgrade(live_oci_connector):
    from app.connectors.executors.oci.oci_cache_upgrade import execute, rollback

    result = await execute(
        {
            "cluster_id": os.environ["SMOKE_OCI_REDIS_CLUSTER_ID"],
            "target_version": os.environ["SMOKE_OCI_REDIS_TARGET_VERSION"],
        },
        [],
        live_oci_connector,
    )
    assert result["status"] in ("upgraded", "already_at_version")
    rb = await rollback({}, result, live_oci_connector)
    assert rb["rolled_back"] is False
