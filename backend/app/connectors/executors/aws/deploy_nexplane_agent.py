import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    nexplane_url = parameters.get('nexplane_url', '')
    nexplane_secret = parameters.get('nexplane_secret', '')

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

    version_url = f"{nexplane_url}/downloads/version"
    script = [
        f"VERSION=$(curl -fsSL {version_url})",
        f"curl -fsSL {nexplane_url}/downloads/nexplane-agent-linux-amd64-${{VERSION}} -o /usr/local/bin/nexplane-agent",
        "chmod +x /usr/local/bin/nexplane-agent",
        f"printf '{systemd_unit}' > /etc/systemd/system/nexplane-agent.service",
        "systemctl daemon-reload",
        "systemctl enable --now nexplane-agent",
        "systemctl is-active nexplane-agent",
    ]

    def _call():
        import time
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script},
        )
        command_id = resp['Command']['CommandId']
        for _ in range(24):
            time.sleep(5)
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
                return result
        return {"Status": "TimedOut", "StandardOutputContent": "", "StandardErrorContent": ""}

    result = await loop.run_in_executor(None, _call)
    if result['Status'] != 'Success':
        raise RuntimeError(f"deploy_nexplane_agent failed: {result.get('StandardErrorContent', '')}")

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
