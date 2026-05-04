import asyncio
import os
import shutil
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
    cleanup_dir = parameters.get('cleanup_dir', True)

    if not working_dir:
        return {"action": "terraform_destroy_local", "output": "mock destroy: resources removed", "mock": True}

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(
            ["terraform", "destroy", "-no-color", "-auto-approve"],
            cwd=working_dir, env=env, capture_output=True, text=True, timeout=300,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            raise RuntimeError(f"terraform destroy failed:\n{result.stderr}")
        if cleanup_dir:
            shutil.rmtree(working_dir, ignore_errors=True)
        return output

    output = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_destroy_local",
        "output": output,
        "working_dir": working_dir,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terraform destroy has no further rollback"}
