import asyncio
import os
import subprocess
from datetime import datetime, timezone


async def _get_aws_env(connector) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    env = {**os.environ}
    if creds.get('access_key_id'):
        env['AWS_ACCESS_KEY_ID'] = creds['access_key_id']
        env['AWS_SECRET_ACCESS_KEY'] = creds.get('secret_access_key', '')
        env['AWS_DEFAULT_REGION'] = creds.get('region', 'us-east-1')
        if creds.get('session_token'):
            env['AWS_SESSION_TOKEN'] = creds['session_token']
    return env


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    working_dir = parameters.get('working_dir', '')
    plan_file = parameters.get('plan_file', '')

    if not working_dir:
        return {"action": "terraform_apply_local", "output": "mock apply: resources created", "mock": True}

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        cmd = ["terraform", "apply", "-no-color", "-auto-approve"]
        if plan_file and os.path.exists(plan_file):
            cmd.append(plan_file)
        result = subprocess.run(
            cmd, cwd=working_dir, env=env, capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"terraform apply failed:\n{result.stderr}")
        return result.stdout

    output = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_apply_local",
        "output": output,
        "working_dir": working_dir,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.terraform_local.terraform_destroy_local import execute as destroy
    return await destroy({"working_dir": execution_result.get("working_dir", "")}, [], connector)
