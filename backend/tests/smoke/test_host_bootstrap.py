# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""One-shot host bootstrap: pulls latest code from GitHub, adds SSH key, enables Tailscale SSH."""
import argparse
import subprocess
import sys
import time

sys.path.insert(0, "/app")

from smoke_helpers import log, _get_aws_boto3_client

HOST_INSTANCE_ID = "i-050bab85006f0b73c"
LAPTOP_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIILQhOwkXHYQ91Mhhjn8tAbDoxXGnXOdWbFh01SNOuTX john.o.terrill@gmail.com"

COMMANDS = [
    f"grep -qxF '{LAPTOP_PUBKEY}' /home/ec2-user/.ssh/authorized_keys || echo '{LAPTOP_PUBKEY}' >> /home/ec2-user/.ssh/authorized_keys",
    "chmod 600 /home/ec2-user/.ssh/authorized_keys",
    "tailscale up --ssh --accept-risk=lose-ssh 2>&1 || true",
    "cd /home/ec2-user/nexplane && git pull origin master 2>&1",
    "echo HOST_BOOTSTRAP_DONE",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--phases", default="BOOTSTRAP")
    args, _ = parser.parse_known_args()

    log("HOST_BOOTSTRAP: starting")
    ssm = _get_aws_boto3_client("ssm")

    resp = ssm.send_command(
        InstanceIds=[HOST_INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": COMMANDS},
        TimeoutSeconds=120,
    )
    cmd_id = resp["Command"]["CommandId"]
    log(f"HOST_BOOTSTRAP: SSM command sent ({cmd_id})")

    for _ in range(30):
        time.sleep(5)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=HOST_INSTANCE_ID)
        status = inv["Status"]
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            output = inv.get("StandardOutputContent", "")
            err = inv.get("StandardErrorContent", "")
            log(f"HOST_BOOTSTRAP: SSM status={status}")
            print(output)
            if err:
                print("STDERR:", err)
            if "HOST_BOOTSTRAP_DONE" in output:
                log("HOST_BOOTSTRAP: complete ✅")
                print("\n============================================================")
                print(" ALL SELECTED PHASES PASSED")
                print("============================================================")
            else:
                log("HOST_BOOTSTRAP: failed ❌", ok=False)
                print("\nSMOKE TEST FAILED")
            return
    log("HOST_BOOTSTRAP: SSM timeout", ok=False)
    print("\nSMOKE TEST FAILED")


if __name__ == "__main__":
    main()
