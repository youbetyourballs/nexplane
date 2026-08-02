# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""ECS rolling deploy executor with multi-gate health-check rollback.

Phases:
  1. Preflight  — validate service + cluster exist; capture old_task_def_arn
  2. Register   — build new task def revision (image_tag + env_var_overrides)
  3. Deploy     — update_service to new task def ARN
  4. Stability  — poll until runningCount==desiredCount and single deployment
  5. ALB gate   — (optional) all target group targets healthy
  6. HTTP probe — (optional) GET health_check_url returns expected status

Any phase 4–6 failure calls update_service back to old_task_def_arn (auto-rollback).
"""

import logging
import time
import requests
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

AWS_MANAGED_FIELDS = {
    "taskDefinitionArn", "revision", "status", "registeredAt",
    "registeredBy", "deregisteredAt", "compatibilities", "requiresAttributes",
}


def _preflight(ecs_client, params: dict) -> dict:
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    image_tag = params.get("image_tag")
    env_var_overrides = params.get("env_var_overrides") or []

    if not image_tag and not env_var_overrides:
        raise ValueError("At least one of image_tag or env_var_overrides must be provided")

    resp = ecs_client.describe_services(cluster=cluster, services=[service_arn])
    services = resp.get("services", [])
    if not services:
        failures = resp.get("failures", [])
        raise ValueError(f"Service {service_arn} not found in cluster {cluster}: {failures}")

    svc = services[0]
    return {"old_task_def_arn": svc["taskDefinition"]}


def _register(ecs_client, params: dict, old_task_def_arn: str) -> str:
    td = ecs_client.describe_task_definition(taskDefinition=old_task_def_arn)["taskDefinition"]
    containers = [dict(c) for c in td["containerDefinitions"]]

    image_tag = params.get("image_tag")
    if image_tag:
        container_name = params.get("container_name")
        if len(containers) > 1 and not container_name:
            raise ValueError(
                "container_name is required when image_tag is set and the task definition "
                "has more than one container"
            )
        target = containers[0] if not container_name else next(
            (c for c in containers if c["name"] == container_name), None
        )
        if target is None:
            raise ValueError(f"Container '{container_name}' not found in task definition")
        target["image"] = image_tag

    env_var_overrides = params.get("env_var_overrides") or []
    for override in env_var_overrides:
        key = override["key"]
        old_val = override["old_value"]
        new_val = override["new_value"]
        container_name = params.get("container_name")
        target_name = container_name if container_name else containers[0]["name"]

        for container in containers:
            if container["name"] == target_name:
                env = [dict(e) for e in container.get("environment", [])]
                found = False
                for e in env:
                    if e["name"] == key:
                        if e["value"] != old_val:
                            raise ValueError(
                                f"env var {key}: expected old_value='{old_val}', "
                                f"found '{e['value']}'"
                            )
                        e["value"] = new_val
                        found = True
                if not found:
                    raise ValueError(
                        f"env var '{key}' not found in container '{target_name}'"
                    )
                container["environment"] = env

    register_kwargs = {k: v for k, v in td.items()
                       if k not in AWS_MANAGED_FIELDS and k != "containerDefinitions"}
    register_kwargs["containerDefinitions"] = containers
    new_td = ecs_client.register_task_definition(**register_kwargs)["taskDefinition"]
    return new_td["taskDefinitionArn"]


_STABILITY_POLL_INTERVAL = 10  # seconds between describe_services calls


def _poll_stability(ecs_client, cluster: str, service_arn: str,
                    timeout: int) -> int:
    """Return elapsed seconds on success; raise TimeoutError on timeout."""
    start = time.time()
    # Always do at least one poll; cap iterations to avoid consuming extra
    # mock side-effects in tests when time.sleep is patched.
    max_polls = max(1, timeout // _STABILITY_POLL_INTERVAL)
    last_svc = None
    for _ in range(max_polls):
        resp = ecs_client.describe_services(cluster=cluster, services=[service_arn])
        svc = resp["services"][0]
        last_svc = svc
        if svc["runningCount"] == svc["desiredCount"] and len(svc.get("deployments", [])) == 1:
            return int(time.time() - start)
        time.sleep(_STABILITY_POLL_INTERVAL)
    raise TimeoutError(
        f"stability timeout after {timeout}s "
        f"(runningCount={last_svc['runningCount']}, "
        f"desiredCount={last_svc['desiredCount']})"
    )


_ALB_POLL_INTERVAL = 10   # seconds between target health checks
_HTTP_POLL_INTERVAL = 5   # seconds between HTTP probe retries


def _check_alb_health(elb_client, target_group_arn: str, timeout: int) -> None:
    """Raise TimeoutError if targets are not all healthy within timeout."""
    max_polls = max(1, timeout // _ALB_POLL_INTERVAL)
    for _ in range(max_polls):
        resp = elb_client.describe_target_health(TargetGroupArn=target_group_arn)
        targets = resp.get("TargetHealthDescriptions", [])
        if targets and all(t["TargetHealth"]["State"] == "healthy" for t in targets):
            return
        time.sleep(_ALB_POLL_INTERVAL)
    raise TimeoutError(f"ALB health timeout after {timeout}s — targets not healthy")


def _check_http_probe(url: str, expected_status: int, timeout: int) -> None:
    """Raise TimeoutError if health endpoint doesn't return expected_status."""
    max_polls = max(1, timeout // _HTTP_POLL_INTERVAL)
    for _ in range(max_polls):
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == expected_status:
                return
        except Exception:
            pass
        time.sleep(_HTTP_POLL_INTERVAL)
    raise TimeoutError(
        f"HTTP probe timeout after {timeout}s — "
        f"expected status {expected_status} from {url}"
    )


def _do_rollback(ecs_client, cluster: str, service_arn: str, old_task_def_arn: str) -> None:
    ecs_client.update_service(cluster=cluster, service=service_arn, taskDefinition=old_task_def_arn)
    logger.info("Auto-rolled back ECS service %s to %s", service_arn, old_task_def_arn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    params = parameters or {}
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    region = params.get("region")
    stability_timeout = int(params.get("stability_timeout_seconds") or 300)
    health_timeout = int(params.get("health_timeout_seconds") or 60)
    target_group_arn = params.get("target_group_arn")
    health_check_url = params.get("health_check_url")
    expected_status = int(params.get("health_check_expected_status") or 200)

    ecs = _boto_client("ecs", connector, region)

    # Phase 1: Preflight
    preflight = _preflight(ecs, params)
    old_task_def_arn = preflight["old_task_def_arn"]

    # Phase 2: Register
    new_task_def_arn = _register(ecs, params, old_task_def_arn)
    logger.info("Registered new task def %s (was %s)", new_task_def_arn, old_task_def_arn)

    # Phase 3: Deploy
    ecs.update_service(cluster=cluster, service=service_arn, taskDefinition=new_task_def_arn)
    logger.info("Updated ECS service %s to %s", service_arn, new_task_def_arn)

    health_checks = {}

    # Phase 4: Stability poll
    try:
        stability_seconds = _poll_stability(ecs, cluster, service_arn, stability_timeout)
    except TimeoutError as e:
        _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
        return {
            "deployed": False,
            "rolled_back": True,
            "old_task_def_arn": old_task_def_arn,
            "new_task_def_arn": new_task_def_arn,
            "rollback_reason": f"stability timeout after {stability_timeout}s: {e}",
            "health_checks": health_checks,
        }

    # Phase 5: ALB health gate (optional)
    if target_group_arn:
        elb = _boto_client("elbv2", connector, region)
        try:
            _check_alb_health(elb, target_group_arn, health_timeout)
            health_checks["alb"] = "passed"
        except TimeoutError:
            _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
            return {
                "deployed": False,
                "rolled_back": True,
                "old_task_def_arn": old_task_def_arn,
                "new_task_def_arn": new_task_def_arn,
                "rollback_reason": f"ALB health timeout after {health_timeout}s",
                "health_checks": health_checks,
            }

    # Phase 6: HTTP probe gate (optional)
    if health_check_url:
        try:
            _check_http_probe(health_check_url, expected_status, health_timeout)
            health_checks["http"] = "passed"
        except TimeoutError:
            _do_rollback(ecs, cluster, service_arn, old_task_def_arn)
            return {
                "deployed": False,
                "rolled_back": True,
                "old_task_def_arn": old_task_def_arn,
                "new_task_def_arn": new_task_def_arn,
                "rollback_reason": f"HTTP probe timeout after {health_timeout}s",
                "health_checks": health_checks,
            }

    return {
        "deployed": True,
        "rolled_back": False,
        "old_task_def_arn": old_task_def_arn,
        "new_task_def_arn": new_task_def_arn,
        "stability_seconds": stability_seconds,
        "health_checks": health_checks,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    params = parameters or {}
    cluster = params["cluster"]
    service_arn = params["service_arn"]
    region = params.get("region")
    old_task_def_arn = execution_result.get("old_task_def_arn", "")

    if not old_task_def_arn:
        return {"rolled_back": False, "reason": "no old_task_def_arn in execution_result"}

    ecs = _boto_client("ecs", connector, region)
    ecs.update_service(cluster=cluster, service=service_arn, taskDefinition=old_task_def_arn)
    logger.info("Rolled back ECS service %s to %s", service_arn, old_task_def_arn)
    return {"rolled_back": True, "old_task_def_arn": old_task_def_arn}
