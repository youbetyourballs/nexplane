# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Falco SSM client — writes rules and restarts Falco via AWS SSM Run Command."""
from typing import Optional
import time


class FalcoSSMClient:
    def __init__(self, ssm_client, instance_id: str):
        self.ssm = ssm_client
        self.instance_id = instance_id

    def _run_command(self, commands: list, timeout: int = 60) -> str:
        """Send SSM RunShellScript and return stdout."""
        resp = self.ssm.send_command(
            InstanceIds=[self.instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            TimeoutSeconds=timeout,
        )
        command_id = resp["Command"]["CommandId"]
        deadline = time.time() + timeout + 30
        while time.time() < deadline:
            time.sleep(5)
            try:
                out = self.ssm.get_command_invocation(
                    CommandId=command_id, InstanceId=self.instance_id
                )
                status = out["Status"]
                if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                    if status != "Success":
                        raise RuntimeError(
                            f"SSM command failed ({status}): {out.get('StandardErrorContent', '')}"
                        )
                    return out.get("StandardOutputContent", "")
            except self.ssm.exceptions.InvocationDoesNotExist:
                pass
        raise TimeoutError(f"SSM command {command_id} timed out")

    def write_rule(self, rule_yaml: str) -> str:
        """Append a rule block to /etc/falco/falco_rules.local.yaml."""
        # Use tee to append; ensure the file ends with a newline first
        escaped = rule_yaml.replace("'", "'\\''")
        commands = [
            "touch /etc/falco/falco_rules.local.yaml",
            f"printf '%s\\n' '{escaped}' >> /etc/falco/falco_rules.local.yaml",
        ]
        return self._run_command(commands)

    def remove_rule(self, rule_name: str) -> str:
        """Remove a rule block identified by rule_name from falco_rules.local.yaml."""
        # Use Python on the host to remove the rule block
        py_script = (
            "import re, sys\n"
            "path = '/etc/falco/falco_rules.local.yaml'\n"
            "with open(path) as f:\n"
            "    text = f.read()\n"
            f"pattern = r'- rule: {rule_name}.*?(?=\\n- rule:|\\Z)'\n"
            "cleaned = re.sub(pattern, '', text, flags=re.DOTALL).strip()\n"
            "with open(path, 'w') as f:\n"
            "    f.write(cleaned + '\\n' if cleaned else '')\n"
            "print('removed')\n"
        )
        commands = [f"python3 -c \"{py_script}\""]
        return self._run_command(commands)

    def restart_falco(self) -> str:
        return self._run_command(["systemctl restart falco"])

    def read_rules_file(self) -> str:
        return self._run_command(["cat /etc/falco/falco_rules.local.yaml"])


def get_falco_ssm_client(connector, ssm_client=None) -> Optional[FalcoSSMClient]:
    creds = getattr(connector, "credentials", None) or {}
    instance_id = creds.get("instance_id") or creds.get("ssm_instance_id")
    if not instance_id:
        return None
    if ssm_client is None:
        import boto3
        ssm_client = boto3.client("ssm", region_name=creds.get("region", "us-east-1"))
    return FalcoSSMClient(ssm_client=ssm_client, instance_id=instance_id)
