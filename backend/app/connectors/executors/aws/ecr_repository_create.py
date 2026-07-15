# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: ecr_create_repository — create ECR repository via boto3."""
from __future__ import annotations

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Create an ECR repository.

    Parameters:
      - repository_name (str, required)
      - region (str, optional, default "us-east-1")
    """
    import boto3

    repo_name = parameters.get("repository_name")
    if not repo_name:
        raise ValueError("Missing required parameter: repository_name")

    region = parameters.get("region", "us-east-1")
    dry_run = bool(parameters.get("dry_run", False))

    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )

    if dry_run:
        account_id = "123456789012"
        return {
            "repository_name": repo_name,
            "repository_uri": f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}",
            "region": region,
            "status": "dry_run",
            "_auto_asset": {
                "name": repo_name,
                "asset_type": "container_image",
                "asset_metadata": {"repository_uri": f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}", "region": region, "dry_run": True},
            },
        }

    ecr = session.client("ecr")
    try:
        resp = ecr.create_repository(repositoryName=repo_name)
        repository_uri = resp["repository"]["repositoryUri"]
    except ecr.exceptions.RepositoryAlreadyExistsException:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
        repository_uri = resp["repositories"][0]["repositoryUri"]

    return {
        "repository_name": repo_name,
        "repository_uri": repository_uri,
        "region": region,
        "status": "created",
        "_auto_asset": {
            "name": repo_name,
            "asset_type": "container_image",
            "asset_metadata": {"repository_uri": repository_uri, "region": region},
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    import boto3
    repo_name = execution_result.get("repository_name")
    region = execution_result.get("region", "us-east-1")
    if not repo_name or execution_result.get("status") == "dry_run":
        return {"rolled_back": False}
    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )
    ecr = session.client("ecr")
    try:
        ecr.delete_repository(repositoryName=repo_name, force=True)
        return {"rolled_back": True, "repository_name": repo_name}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
