import asyncio
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


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
    tf_content = parameters.get('tf_content', '')
    working_dir = parameters.get('working_dir', '')

    if not tf_content and not working_dir:
        return {
            "action": "terraform_plan_local",
            "plan_output": "mock plan: no changes",
            "plan_file": "/tmp/mock.tfplan",
            "working_dir": "/tmp/mock-tf",
            "mock": True,
        }

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        work_dir = working_dir or tempfile.mkdtemp(prefix="nexplane-tf-")
        plan_file = os.path.join(work_dir, "tfplan")

        if tf_content:
            Path(os.path.join(work_dir, "main.tf")).write_text(tf_content)

        init_result = subprocess.run(
            ["terraform", "init", "-no-color"],
            cwd=work_dir, env=env, capture_output=True, text=True, timeout=300,
        )
        if init_result.returncode != 0:
            raise RuntimeError(f"terraform init failed:\n{init_result.stderr}")

        plan_result = subprocess.run(
            ["terraform", "plan", "-no-color", f"-out={plan_file}"],
            cwd=work_dir, env=env, capture_output=True, text=True, timeout=180,
        )
        if plan_result.returncode != 0:
            raise RuntimeError(f"terraform plan failed:\n{plan_result.stderr}")

        return {"work_dir": work_dir, "plan_file": plan_file, "output": plan_result.stdout}

    result = await loop.run_in_executor(None, _run)
    return {
        "action": "terraform_plan_local",
        "plan_output": result["output"],
        "plan_file": result["plan_file"],
        "working_dir": result["work_dir"],
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "terraform plan has no rollback"}
