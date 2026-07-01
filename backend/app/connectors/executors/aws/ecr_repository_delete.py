# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: ecr_delete_repository — delete ECR repository."""
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    import boto3
    repo_name = parameters.get("repository_name")
    if not repo_name:
        raise ValueError("Missing required parameter: repository_name")
    region = parameters.get("region", "us-east-1")
    dry_run = bool(parameters.get("dry_run", False))
    if dry_run:
        return {"repository_name": repo_name, "status": "dry_run"}
    creds = getattr(connector, "credentials", {}) or {}
    session = boto3.Session(
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        region_name=region,
    )
    ecr = session.client("ecr")
    try:
        ecr.delete_repository(repositoryName=repo_name, force=True)
        return {"repository_name": repo_name, "status": "deleted"}
    except Exception as e:
        return {"repository_name": repo_name, "status": "error", "error": str(e)}
