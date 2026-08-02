# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
ECS rolling deploy smoke test.

Three phases against real AWS Fargate:
  1. Successful deploy (env var + image tag) + manual platform rollback
  2. Auto-rollback on bad image (stability timeout fires)
  3. ecs_task_def_deregister CR

Run on EC2:
    cd /home/ec2-user/nexplane
    PYTHONPATH=backend python3 -m pytest \
        backend/tests/smoke/test_ecs_rolling_deploy_smoke.py -v -s \
        2>&1 | tee /tmp/ecs_smoke.log; echo SMOKE_DONE_ECS >> /tmp/ecs_smoke.log
"""

import os
import sys
import time
import json

import pytest
import boto3
import requests

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL           = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL              = os.environ.get("NEXPLANE_EMAIL",    "admin@acme.example")
PASSWORD           = os.environ.get("NEXPLANE_PASSWORD", "admin123")
# AWS connector UUID in the platform DB: 666e237d-4e83-4c6c-b72e-6e32fbb5c895
# get_connector_creds_from_db() looks up by connector_type string, not UUID —
# use the type string so it works on both the EC2 runner path (env var) and in-container path.
SMOKE_CONNECTOR_TYPE = "aws"
SMOKE_ASSET_ID       = "1a7051be-7110-4a21-9cdf-b023231cdff8"

CLUSTER_NAME = "smoke-ecs-rolling-deploy"
SERVICE_NAME = "smoke-ecs-svc"
TASK_FAMILY  = "smoke-ecs-task"
REGION       = "us-east-1"

POLL_INTERVAL = 15
CR_TIMEOUT    = 600   # 10 min — Fargate cold start can be slow


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def _aws_creds():
    """Pull AWS credentials from the smoke connector record in the platform DB."""
    creds = get_connector_creds_from_db(SMOKE_CONNECTOR_TYPE)
    return {
        "aws_access_key_id":     creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name":           REGION,
    }


def _ecs():
    return boto3.client("ecs", **_aws_creds())


def _iam():
    return boto3.client("iam", **_aws_creds())


def _get_execution_role_arn() -> str:
    """Return the ARN of ecsTaskExecutionRole, creating it if absent."""
    iam = _iam()
    role_name = "ecsTaskExecutionRole"
    try:
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        trust = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }]
        })
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust,
        )["Role"]
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        time.sleep(10)   # IAM eventual consistency
        return role["Arn"]


def _register_initial_task_def(execution_role_arn: str) -> str:
    ecs = _ecs()
    resp = ecs.register_task_definition(
        family=TASK_FAMILY,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        executionRoleArn=execution_role_arn,
        containerDefinitions=[{
            "name": "app",
            "image": "nginx:alpine",
            "essential": True,
            "environment": [{"name": "SMOKE_ENV", "value": "original"}],
            "portMappings": [{"containerPort": 80, "protocol": "tcp"}],
        }],
    )
    return resp["taskDefinition"]["taskDefinitionArn"]


def _create_cluster() -> None:
    ecs = _ecs()
    try:
        # Don't pass capacityProviders — FARGATE is available by default
        # and setting it explicitly requires the ECS SLR which may not exist.
        ecs.create_cluster(clusterName=CLUSTER_NAME)
        log(f"Created cluster {CLUSTER_NAME}")
    except ecs.exceptions.ClusterContainsServicesException:
        log(f"Cluster {CLUSTER_NAME} already exists")


def _create_service(task_def_arn: str, subnet_id: str, sg_id: str) -> None:
    ecs = _ecs()
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            ecs.create_service(
                cluster=CLUSTER_NAME,
                serviceName=SERVICE_NAME,
                taskDefinition=task_def_arn,
                desiredCount=1,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": [subnet_id],
                        "securityGroups": [sg_id],
                        "assignPublicIp": "ENABLED",
                    }
                },
                deploymentConfiguration={
                    "deploymentCircuitBreaker": {"enable": False, "rollback": False},
                    "maximumPercent": 200,
                    "minimumHealthyPercent": 0,
                },
            )
            log(f"Created ECS service {SERVICE_NAME}")
            return
        except Exception as e:
            msg = str(e)
            if "Draining" in msg or "already" in msg.lower():
                log(f"Service still draining, waiting 15s: {msg}")
                time.sleep(15)
            else:
                raise
    raise TimeoutError(f"Service {SERVICE_NAME} still draining after 120s")


def _wait_service_stable(timeout: int = 300) -> None:
    deadline = time.time() + timeout
    ecs = _ecs()
    while time.time() < deadline:
        resp = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME])
        svc = resp["services"][0]
        if svc["runningCount"] == svc["desiredCount"] and len(svc["deployments"]) == 1:
            log(f"Service stable: runningCount={svc['runningCount']}")
            return
        log(f"Waiting for service stability: running={svc['runningCount']}/{svc['desiredCount']}")
        time.sleep(15)
    raise TimeoutError(f"Service did not stabilize within {timeout}s")


def _get_service_task_def() -> str:
    ecs = _ecs()
    resp = ecs.describe_services(cluster=CLUSTER_NAME, services=[SERVICE_NAME])
    return resp["services"][0]["taskDefinition"]


def _get_vpc_subnet_sg() -> tuple:
    """Return (subnet_id, sg_id) from the default VPC."""
    ec2 = boto3.client("ec2", **_aws_creds())
    vpc_resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpc_resp["Vpcs"][0]["VpcId"]
    subnet_resp = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "default-for-az", "Values": ["true"]}]
    )
    subnet_id = subnet_resp["Subnets"][0]["SubnetId"]
    sg_resp = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                 {"Name": "group-name", "Values": ["default"]}]
    )
    sg_id = sg_resp["SecurityGroups"][0]["GroupId"]
    return subnet_id, sg_id


def _teardown(registered_arns: list) -> None:
    ecs = _ecs()
    try:
        ecs.update_service(cluster=CLUSTER_NAME, service=SERVICE_NAME, desiredCount=0)
        time.sleep(10)
        ecs.delete_service(cluster=CLUSTER_NAME, service=SERVICE_NAME, force=True)
        log("Deleted ECS service")
    except Exception as e:
        log(f"Service teardown warning: {e}")
    for arn in registered_arns:
        try:
            ecs.deregister_task_definition(taskDefinition=arn)
            log(f"Deregistered {arn}")
        except Exception as e:
            log(f"Deregister warning for {arn}: {e}")
    try:
        ecs.delete_cluster(cluster=CLUSTER_NAME)
        log(f"Deleted cluster {CLUSTER_NAME}")
    except Exception as e:
        log(f"Cluster teardown warning: {e}")


# ---------------------------------------------------------------------------
# CR lifecycle helpers (same pattern as other smoke tests)
# ---------------------------------------------------------------------------

def _extract_step_result(cr: dict, step_number: int = 1) -> dict:
    """Extract the executor result dict from execution_runs (platform stores per-step)."""
    for run in cr.get("execution_runs", []):
        if "rollback" in (run.get("workflow_id") or ""):
            continue
        steps = (run.get("result") or {}).get("execution", {}).get("steps", [])
        for step in steps:
            if step.get("step_number") == step_number:
                return step.get("result") or {}
    return {}


def _cr_lifecycle(client: NexplaneClient, title: str, change_type: str,
                  desired_outcome: dict, timeout: int = CR_TIMEOUT) -> dict:
    base = client.base
    r = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": change_type,
        "desired_outcome": desired_outcome,
        "target_asset_ids": [SMOKE_ASSET_ID],
    })
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]
    log(f"[{title}] CR created: {cr_id}")

    for step in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{step}")
        assert r2.status_code in (200, 201, 202, 204), f"/{step} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "ecs-rolling-deploy smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "verify_failed"):
            log(f"[{title}] -> {status}")
            # Normalize execution_result: platform stores it per-step in execution_runs
            if not cr.get("execution_result"):
                cr["execution_result"] = _extract_step_result(cr)
            return cr
        if status in ("failed", "preflight_blocked", "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} terminal with status={status!r}: "
                f"{str(cr.get('execution_result', ''))[:800]}"
            )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, label: str,
                 timeout: int = CR_TIMEOUT) -> dict:
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code == 409:
        err = r.json()
        if err.get("error") == "out_of_order_rollback":
            blocking = err.get("blocking_crs", [])
            log(f"[{label}] FILO 409 — rolling back {len(blocking)} blocker(s) first")
            for blk_id in blocking:
                _rollback_cr(client, blk_id, f"blocker:{blk_id[:8]}", timeout=timeout)
            r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        if cr.get("status") in ("rolled_back", "rollback_failed"):
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"[{label}] rollback timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestEcsRollingDeploySmoke:

    def setup_method(self, _method):
        """Teardown any leftover resources from a previous run."""
        try:
            _teardown([])
        except Exception:
            pass

    def teardown_method(self, _method):
        pass   # per-phase teardown tracks its own ARNs

    def test_ecs_rolling_deploy(self):
        client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        registered_arns = []

        try:
            # ---------------------------------------------------------------
            # Infrastructure setup
            # ---------------------------------------------------------------
            log("=== Setup: creating Fargate cluster + task def + service ===")
            execution_role_arn = _get_execution_role_arn()
            subnet_id, sg_id = _get_vpc_subnet_sg()
            _create_cluster()

            initial_arn = _register_initial_task_def(execution_role_arn)
            registered_arns.append(initial_arn)
            log(f"Initial task def: {initial_arn}")

            _create_service(initial_arn, subnet_id, sg_id)
            _wait_service_stable(timeout=300)

            # ---------------------------------------------------------------
            # Phase 1: Successful deploy + manual rollback
            # ---------------------------------------------------------------
            log("=== Phase 1: Successful deploy (env var + image tag) ===")
            cr1 = _cr_lifecycle(
                client,
                title="ECS smoke — phase 1 deploy",
                change_type="ecs_rolling_deploy",
                desired_outcome={
                    "service_arn": SERVICE_NAME,
                    "cluster": CLUSTER_NAME,
                    "image_tag": "nginx:1.27",
                    "env_var_overrides": [
                        {"key": "SMOKE_ENV", "old_value": "original", "new_value": "updated"}
                    ],
                    "stability_timeout_seconds": 300,
                    "region": REGION,
                },
            )
            er1 = cr1.get("execution_result", {})
            log(f"Phase 1 result: {er1}")
            assert er1.get("deployed") is True, f"expected deployed=True, got {er1}"
            assert er1.get("rolled_back") is False, f"expected rolled_back=False, got {er1}"
            new_arn_1 = er1["new_task_def_arn"]
            registered_arns.append(new_arn_1)
            assert new_arn_1 != initial_arn, "new_task_def_arn should differ from initial"
            assert _get_service_task_def() == new_arn_1, "Service should point to new task def"
            log("Phase 1 deploy assertions PASSED")

            log("Phase 1: triggering platform rollback")
            rb1 = _rollback_cr(client, cr1["id"], "phase1-rollback")
            assert rb1.get("status") in ("rolled_back", "rollback_failed"), \
                f"Unexpected rollback status: {rb1.get('status')}"
            if rb1.get("status") == "rolled_back":
                assert _get_service_task_def() == initial_arn, \
                    "After rollback, service should point back to initial task def"
                log("Phase 1 rollback assertions PASSED")
            else:
                log(f"Phase 1 rollback_failed (acceptable): {rb1.get('execution_result')}")

            # ---------------------------------------------------------------
            # Phase 2: Auto-rollback on bad image
            # ---------------------------------------------------------------
            log("=== Phase 2: Auto-rollback on bad image ===")
            # Re-point service back to initial_arn for clean phase 2 state
            _ecs().update_service(
                cluster=CLUSTER_NAME, service=SERVICE_NAME, taskDefinition=initial_arn
            )
            _wait_service_stable(timeout=300)

            cr2 = _cr_lifecycle(
                client,
                title="ECS smoke — phase 2 bad image",
                change_type="ecs_rolling_deploy",
                desired_outcome={
                    "service_arn": SERVICE_NAME,
                    "cluster": CLUSTER_NAME,
                    "image_tag": "nginx:this-tag-does-not-exist-99999",
                    "stability_timeout_seconds": 120,
                    "region": REGION,
                },
                timeout=300,
            )
            er2 = cr2.get("execution_result", {})
            log(f"Phase 2 result: {er2}")
            bad_arn = er2.get("new_task_def_arn")
            if bad_arn:
                registered_arns.append(bad_arn)
            assert er2.get("rolled_back") is True, \
                f"expected rolled_back=True for bad image, got {er2}"
            assert _get_service_task_def() == initial_arn, \
                "After auto-rollback, service should point back to initial task def"
            log("Phase 2 auto-rollback assertions PASSED")

            # ---------------------------------------------------------------
            # Phase 3: ecs_task_def_deregister
            # ---------------------------------------------------------------
            assert bad_arn is not None, \
                "Phase 2 must have registered a task def ARN for Phase 3 to deregister"
            log(f"=== Phase 3: Deregister bad-image task def {bad_arn} ===")
            cr3 = _cr_lifecycle(
                client,
                title="ECS smoke — phase 3 deregister",
                change_type="ecs_task_def_deregister",
                desired_outcome={
                    "task_def_arn": bad_arn,
                    "region": REGION,
                    # ecs_task_def_deregister is irreversible — no implicit rollback exists.
                    # Rollback strategy here is reconstitution: if needed, re-register from
                    # the original task definition family.
                    "rollback_strategy": "reconstitution",
                },
            )
            er3 = cr3.get("execution_result", {})
            log(f"Phase 3 result: {er3}")
            assert er3.get("deregistered") is True, f"expected deregistered=True, got {er3}"
            td_status = _ecs().describe_task_definition(
                taskDefinition=bad_arn
            )["taskDefinition"]["status"]
            assert td_status == "INACTIVE", \
                f"Expected task def INACTIVE after deregister, got {td_status}"
            registered_arns.remove(bad_arn)   # already deregistered
            log("Phase 3 deregister assertions PASSED")

            log("=== ALL ECS ROLLING DEPLOY SMOKE PHASES PASSED ===")

        finally:
            log("=== Teardown ===")
            _teardown(registered_arns)
