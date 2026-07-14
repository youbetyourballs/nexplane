# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS scan executors — emit unified hit schema for reference_scan orchestrator."""

import base64
import logging
import boto3
from typing import Any

logger = logging.getLogger(__name__)


def _boto_client(service: str, connector, region: str | None = None) -> Any:
    creds = connector.credentials or {}
    kwargs: dict = {
        "aws_access_key_id": creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name": region or creds.get("region", "us-east-1"),
    }
    if creds.get("session_token"):
        kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client(service, **kwargs)


def _matches(value: str, search_terms: list[str]) -> list[str]:
    if not value:
        return []
    return [t for t in search_terms if t.lower() in value.lower()]


def _hit(
    surface: str,
    location: str,
    matched_term: str,
    snippet: str,
    stable_id: str | None = None,
    hostname: str | None = None,
    surface_metadata: dict | None = None,
) -> dict:
    return {
        "surface": surface,
        "location": location,
        "matched_term": matched_term,
        "snippet": snippet,
        "consumer_identity": {
            "stable_id": stable_id,
            "hostname": hostname,
            "surface_metadata": surface_metadata or {},
        },
    }


async def scan_lambda_env_vars(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("lambda", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page.get("Functions", []):
            scanned += 1
            arn = fn["FunctionArn"]
            env_vars = fn.get("Environment", {}).get("Variables", {})
            for key, val in env_vars.items():
                for term in _matches(val, search_terms):
                    hits.append(_hit(
                        surface="aws_lambda_env",
                        location=arn,
                        matched_term=term,
                        snippet=f"{key}={val}",
                        stable_id=arn,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ecs_task_defs(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("ecs", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_task_definitions")
    for page in paginator.paginate(status="ACTIVE"):
        for arn in page.get("taskDefinitionArns", []):
            td = client.describe_task_definition(taskDefinition=arn)["taskDefinition"]
            scanned += 1
            for container in td.get("containerDefinitions", []):
                for env in container.get("environment", []):
                    for term in _matches(env.get("value", ""), search_terms):
                        hits.append(_hit(
                            surface="aws_ecs_task_def",
                            location=arn,
                            matched_term=term,
                            snippet=f"{env['name']}={env['value']} (container: {container['name']})",
                            stable_id=arn,
                        ))
                for secret in container.get("secrets", []):
                    for term in _matches(secret.get("valueFrom", ""), search_terms):
                        hits.append(_hit(
                            surface="aws_ecs_task_def",
                            location=arn,
                            matched_term=term,
                            snippet=f"secret {secret['name']} -> {secret['valueFrom']}",
                            stable_id=arn,
                        ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_rds_parameter_groups(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("rds", connector, region)
    hits = []
    scanned = 0
    pg_paginator = client.get_paginator("describe_db_parameter_groups")
    for page in pg_paginator.paginate():
        for pg in page.get("DBParameterGroups", []):
            scanned += 1
            pg_name = pg["DBParameterGroupName"]
            pg_arn = pg["DBParameterGroupArn"]
            pp = client.get_paginator("describe_db_parameters")
            for ppage in pp.paginate(DBParameterGroupName=pg_name):
                for param in ppage.get("Parameters", []):
                    val = param.get("ParameterValue", "")
                    for term in _matches(val, search_terms):
                        hits.append(_hit(
                            surface="aws_rds_parameter_group",
                            location=pg_arn,
                            matched_term=term,
                            snippet=f"{param['ParameterName']}={val}",
                            stable_id=pg_arn,
                        ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_secrets_manager_metadata(cr, connector, db) -> dict:
    """Scans secret names, descriptions, and tags ONLY — never secret values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("secretsmanager", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("list_secrets")
    for page in paginator.paginate():
        for secret in page.get("SecretList", []):
            scanned += 1
            arn = secret["ARN"]
            name = secret.get("Name", "")
            desc = secret.get("Description", "")
            tags_str = " ".join(f"{t['Key']}={t['Value']}" for t in secret.get("Tags", []))
            for field_val, field_name in [(name, "name"), (desc, "description"), (tags_str, "tags")]:
                for term in _matches(field_val, search_terms):
                    hits.append(_hit(
                        surface="aws_secrets_manager_metadata",
                        location=arn,
                        matched_term=term,
                        snippet=f"{field_name}: {field_val}",
                        stable_id=arn,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ssm_parameters_metadata(cr, connector, db) -> dict:
    """Scans SSM parameter names and descriptions ONLY — never parameter values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    client = _boto_client("ssm", connector, region)
    hits = []
    scanned = 0
    paginator = client.get_paginator("describe_parameters")
    for page in paginator.paginate():
        for param in page.get("Parameters", []):
            scanned += 1
            name = param.get("Name", "")
            desc = param.get("Description", "")
            for field_val, field_name in [(name, "name"), (desc, "description")]:
                for term in _matches(field_val, search_terms):
                    hits.append(_hit(
                        surface="aws_ssm_parameter_metadata",
                        location=name,
                        matched_term=term,
                        snippet=f"{field_name}: {field_val}",
                        stable_id=name,
                    ))
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}


async def scan_ec2_user_data(cr, connector, db) -> dict:
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    region = params.get("region")
    instance_ids = params.get("instance_ids")
    client = _boto_client("ec2", connector, region)
    hits = []
    scanned = 0
    paginator_kwargs: dict = {}
    if instance_ids:
        paginator_kwargs["InstanceIds"] = instance_ids
    paginator = client.get_paginator("describe_instances")
    for page in paginator.paginate(**paginator_kwargs):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                if instance.get("State", {}).get("Name") != "running":
                    continue
                instance_id = instance["InstanceId"]
                scanned += 1
                try:
                    resp = client.describe_instance_attribute(InstanceId=instance_id, Attribute="userData")
                    ud_b64 = resp.get("UserData", {}).get("Value", "")
                    if not ud_b64:
                        continue
                    user_data = base64.b64decode(ud_b64).decode("utf-8", errors="replace")
                    for term in _matches(user_data, search_terms):
                        line = next(
                            (ln for ln in user_data.splitlines() if term.lower() in ln.lower()),
                            user_data[:120],
                        )
                        hits.append(_hit(
                            surface="aws_ec2_user_data",
                            location=instance_id,
                            matched_term=term,
                            snippet=line.strip()[:200],
                            stable_id=instance_id,
                        ))
                except Exception as e:
                    logger.warning("Failed to read user data for %s: %s", instance_id, e)
    return {"hits": hits, "scan_summary": {"scanned": scanned, "matched": len(hits)}}
