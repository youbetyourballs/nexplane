# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS reference update executors — reconstitution rollback pattern.

AWS does not support renaming Secrets Manager secrets directly. The
update_secrets_manager_secret_description action updates the secret Description
field to track hostname references. To truly rename a secret you must create
a new secret with the desired name, copy the value, and delete the old one
— that workflow is out of scope for this action.
"""

import logging
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)


async def update_lambda_env_var(cr, connector, db) -> dict:
    params = cr.parameters or {}
    fn_arn = params["function_arn"]
    key = params["env_var_key"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("lambda", connector, region)
    config = client.get_function_configuration(FunctionName=fn_arn)
    env_vars = dict(config.get("Environment", {}).get("Variables", {}))

    if env_vars.get(key) != old_val:
        return {
            "status": "skipped",
            "reason": f"current value '{env_vars.get(key)}' != expected old_value '{old_val}'",
        }

    env_vars[key] = new_val
    client.update_function_configuration(FunctionName=fn_arn, Environment={"Variables": env_vars})

    return {
        "status": "updated",
        "function_arn": fn_arn,
        "key": key,
        "rollback_data": {"function_arn": fn_arn, "env_var_key": key, "old_value": old_val, "region": region},
    }


async def rollback_lambda_env_var(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    fn_arn = rb["function_arn"]
    key = rb["env_var_key"]
    old_val = rb["old_value"]
    region = rb.get("region")

    client = _boto_client("lambda", connector, region)
    config = client.get_function_configuration(FunctionName=fn_arn)
    env_vars = dict(config.get("Environment", {}).get("Variables", {}))
    env_vars[key] = old_val
    client.update_function_configuration(FunctionName=fn_arn, Environment={"Variables": env_vars})
    return {"status": "rolled_back", "function_arn": fn_arn, "key": key}


async def update_ecs_task_def_env(cr, connector, db) -> dict:
    params = cr.parameters or {}
    td_arn = params["task_def_arn"]
    container_name = params["container_name"]
    key = params["env_var_key"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("ecs", connector, region)
    td = client.describe_task_definition(taskDefinition=td_arn)["taskDefinition"]
    containers = [dict(c) for c in td["containerDefinitions"]]

    updated = False
    for container in containers:
        if container["name"] == container_name:
            env = [dict(e) for e in container.get("environment", [])]
            for e in env:
                if e["name"] == key and e["value"] == old_val:
                    e["value"] = new_val
                    updated = True
            container["environment"] = env

    if not updated:
        return {
            "status": "skipped",
            "reason": f"env var {key}={old_val} not found in container {container_name}",
        }

    AWS_MANAGED_FIELDS = {"taskDefinitionArn", "revision", "status", "registeredAt", "registeredBy", "deregisteredAt", "compatibilities", "requiresAttributes"}
    register_kwargs = {k: v for k, v in td.items() if k not in AWS_MANAGED_FIELDS and k != "containerDefinitions"}
    register_kwargs["containerDefinitions"] = containers
    new_td = client.register_task_definition(**register_kwargs)["taskDefinition"]

    return {
        "status": "updated",
        "new_task_def_arn": new_td["taskDefinitionArn"],
        "rollback_data": {"old_task_def_arn": td_arn, "region": region},
    }


async def rollback_ecs_task_def_env(cr, connector, db) -> dict:
    """ECS rollback: the old task definition revision still exists in AWS.
    Operators must manually update services to point back to old_task_def_arn."""
    rb = (cr.execution_result or {}).get("rollback_data", {})
    return {
        "status": "manual_action_required",
        "reason": "ECS task definition rollback requires updating all services that reference the new task def ARN to point back to the old one. See rollback_data for old_task_def_arn.",
        "rollback_data": rb,
    }


async def update_ssm_parameter_value(cr, connector, db) -> dict:
    params = cr.parameters or {}
    param_name = params["parameter_name"]
    old_val = params["old_value"]
    new_val = params["new_value"]
    region = params.get("region")

    client = _boto_client("ssm", connector, region)
    current = client.get_parameter(Name=param_name, WithDecryption=False)["Parameter"]

    if current.get("Type") == "SecureString":
        return {"status": "error", "reason": "SecureString parameters are not supported — use Secrets Manager for encrypted values"}

    if current["Value"] != old_val:
        return {"status": "skipped", "reason": f"current value '{current['Value']}' != expected old_value '{old_val}'"}

    client.put_parameter(Name=param_name, Value=new_val, Overwrite=True)
    return {
        "status": "updated",
        "parameter_name": param_name,
        "rollback_data": {"parameter_name": param_name, "old_value": old_val, "region": region},
    }


async def rollback_ssm_parameter_value(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    client = _boto_client("ssm", connector, region=rb.get("region"))
    client.put_parameter(Name=rb["parameter_name"], Value=rb["old_value"], Overwrite=True)
    return {"status": "rolled_back"}


async def update_secrets_manager_secret_description(cr, connector, db) -> dict:
    """Update the description field of a Secrets Manager secret.
    Saves old description for rollback. AWS does not support secret renaming."""
    params = cr.parameters or {}
    secret_arn = params["secret_arn"]
    old_desc = params["old_description"]
    new_desc = params["new_description"]
    region = params.get("region")

    client = _boto_client("secretsmanager", connector, region)
    current_desc = client.describe_secret(SecretId=secret_arn).get("Description", "")
    if current_desc != old_desc:
        return {
            "status": "skipped",
            "reason": f"current description '{current_desc}' != expected old_description '{old_desc}'",
        }

    client.update_secret(SecretId=secret_arn, Description=new_desc)
    return {
        "status": "updated",
        "secret_arn": secret_arn,
        "rollback_data": {"secret_arn": secret_arn, "old_description": old_desc, "region": region},
    }


async def rollback_secrets_manager_secret_description(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    client = _boto_client("secretsmanager", connector, region=rb.get("region"))
    client.update_secret(SecretId=rb["secret_arn"], Description=rb["old_description"])
    return {"status": "rolled_back"}
