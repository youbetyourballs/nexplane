import asyncio
import time as _time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    nexplane_url = parameters.get('nexplane_url', '')
    nexplane_secret = parameters.get('nexplane_secret', '')
    # download_url allows using a different URL (e.g. public IP) for the binary download
    # while the agent connects HOME via nexplane_url (e.g. Tailscale IP)
    download_url = parameters.get('download_url', '') or nexplane_url

    if not creds:
        return {
            "action": "deploy_nexplane_agent",
            "instance_id": instance_id,
            "deployed": True,
            "mock": True,
        }

    from ._client import get_boto3_client
    ssm = get_boto3_client(creds, 'ssm')
    loop = asyncio.get_event_loop()

    systemd_unit = (
        "[Unit]\\n"
        "Description=Nexplane Agent\\n"
        "After=network.target\\n\\n"
        "[Service]\\n"
        f"Environment=NEXPLANE_URL={nexplane_url}\\n"
        f"Environment=NEXPLANE_SECRET={nexplane_secret}\\n"
        "ExecStart=/usr/local/bin/nexplane-agent\\n"
        "Restart=always\\n"
        "RestartSec=10\\n\\n"
        "[Install]\\n"
        "WantedBy=multi-user.target"
    )

    # Build a single shell script to avoid SSM variable scoping issues
    # Total must fit inside SSM executionTimeout (600s below).
    inline_script = f"""#!/bin/bash
set -eux
# Wait for download URL to be reachable (up to 30s)
for i in $(seq 1 6); do
  curl -fsSL --max-time 5 '{download_url}/downloads/version' > /dev/null 2>&1 && break
  echo "Waiting for download URL connectivity ($i/6)..."
  sleep 5
done
VERSION=$(curl -fsSL --max-time 10 '{download_url}/downloads/version' | tr -d '[:space:]')
echo "Version: $VERSION"
curl -fsSL --max-time 180 "{download_url}/downloads/nexplane-agent-linux-amd64-$VERSION" -o /usr/local/bin/nexplane-agent
chmod +x /usr/local/bin/nexplane-agent
cat > /etc/systemd/system/nexplane-agent.service << 'SYSTEMD_EOF'
[Unit]
Description=Nexplane Agent
After=network.target

[Service]
Environment=NEXPLANE_URL={nexplane_url}
Environment=NEXPLANE_SECRET={nexplane_secret}
ExecStart=/usr/local/bin/nexplane-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF
systemctl daemon-reload
systemctl enable nexplane-agent
systemctl start nexplane-agent || true
sleep 5
# Report status (don't fail script if service is still starting)
systemctl status nexplane-agent --no-pager || true
echo "deploy_complete"
"""
    script = [inline_script]

    def _send_and_wait():
        """Send SSM command and poll. Returns (status, stdout, stderr)."""
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                "commands": script,
                "executionTimeout": ["600"],  # 10 min script execution timeout
            },
            TimeoutSeconds=30,  # delivery timeout only
        )
        command_id = resp['Command']['CommandId']
        for _ in range(120):  # poll up to 10 minutes
            _time.sleep(5)
            inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if inv['Status'] not in ('Pending', 'InProgress', 'Delayed'):
                return inv
        return {"Status": "TimedOut", "StandardOutputContent": "", "StandardErrorContent": "timed out"}

    # Retry the entire send+wait cycle for Undeliverable (SSM agent briefly offline after Tailscale join)
    last_result = None
    for attempt in range(1, 4):
        last_result = await loop.run_in_executor(None, _send_and_wait)
        if last_result['Status'] == 'Success':
            break
        if last_result['Status'] == 'Undeliverable' and attempt < 3:
            await asyncio.sleep(20)
            continue
        break

    if last_result['Status'] != 'Success':
        stdout = last_result.get('StandardOutputContent', '')
        stderr = last_result.get('StandardErrorContent', '')
        raise RuntimeError(
            f"deploy_nexplane_agent failed ({last_result['Status']}): {stderr or stdout or 'no output'}"
        )

    return {
        "action": "deploy_nexplane_agent",
        "instance_id": instance_id,
        "nexplane_url": nexplane_url,
        "deployed": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.remove_nexplane_agent import execute as remove
    return await remove({"instance_id": parameters.get('instance_id', '')}, [], connector)
