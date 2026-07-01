# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
    inventory_content: str | None = None,
) -> dict:
    creds = getattr(connector, 'credentials', {}) or {}
    region = creds.get('region', 'us-east-1') if creds else 'us-east-1'

    if not creds and instance_id != 'localhost':
        return {
            "stdout": "mock ansible run — no credentials",
            "rc": 0,
            "mock": True,
        }

    env = await _get_aws_env(connector)
    loop = asyncio.get_event_loop()

    # Build inventory: use provided content, or SSM for real instances, or ad-hoc localhost
    if inventory_content is not None:
        inv = inventory_content
    elif instance_id == 'localhost':
        inv = "localhost,"  # ad-hoc inventory string — trailing comma required
    else:
        inv = _build_ssm_inventory(instance_id, region)

    def _run():
        work_dir = tempfile.mkdtemp(prefix="nexplane-ansible-")
        try:
            playbook_path = os.path.join(work_dir, "playbook.yml")
            Path(playbook_path).write_text(playbook_content)

            if inv == "localhost,":
                # Ad-hoc inventory string passed directly on command line
                inventory_arg = "localhost,"
                inventory_path = None
            elif inv is not None:
                inventory_path = os.path.join(work_dir, "inventory.ini")
                Path(inventory_path).write_text(inv)
                inventory_arg = inventory_path
            else:
                inventory_path = None
                inventory_arg = None

            cmd = ["ansible-playbook"]
            if inventory_arg:
                cmd += ["-i", inventory_arg]
            cmd += [playbook_path, "--timeout", "60"]
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
        out_snippet = result.get("stdout", "")[-2000:]
        err_snippet = result.get("stderr", "")[-1000:]
        raise RuntimeError(
            f"ansible-playbook failed (rc={result['rc']}):\n"
            f"STDOUT: {out_snippet}\nSTDERR: {err_snippet}"
        )
    return result
