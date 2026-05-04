import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _build_ssm_inventory(instance_id: str, region: str) -> str:
    return (
        "[targets]\n"
        + instance_id + "\n\n"
        + "[targets:vars]\n"
        + "ansible_connection=community.aws.aws_ssm\n"
        + "ansible_aws_ssm_region=" + region + "\n"
        + "ansible_aws_ssm_timeout=60\n"
    )


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


async def run_playbook(
    instance_id: str,
    playbook_content: str,
    connector,
    check_mode: bool = False,
    extra_vars: dict | None = None,
) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    region = creds.get('region', 'us-east-1') if creds else 'us-east-1'

    if not creds:
        return {
            "stdout": "mock ansible run — no credentials",
            "rc": 0,
            "mock": True,
        }

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    def _run():
        work_dir = tempfile.mkdtemp(prefix="nexplane-ansible-")
        try:
            inventory_path = os.path.join(work_dir, "inventory.ini")
            playbook_path = os.path.join(work_dir, "playbook.yml")
            Path(inventory_path).write_text(_build_ssm_inventory(instance_id, region))
            Path(playbook_path).write_text(playbook_content)

            cmd = [
                "ansible-playbook",
                "-i", inventory_path,
                playbook_path,
                "--timeout", "60",
            ]
            if check_mode:
                cmd.append("--check")
            if extra_vars:
                import json
                cmd += ["--extra-vars", json.dumps(extra_vars)]

            result = subprocess.run(
                cmd, env=env, capture_output=True, text=True, timeout=300,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "rc": result.returncode,
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    result = await loop.run_in_executor(None, _run)
    if result["rc"] != 0:
        raise RuntimeError("ansible-playbook failed (rc=" + str(result["rc"]) + "):\n" + result["stderr"])
    return result
