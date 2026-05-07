import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    instance_id = parameters.get('instance_id', '')
    auth_key = parameters.get('auth_key', '')
    hostname = parameters.get('hostname', '') or instance_id

    if not creds:
        return {
            "action": "tailscale_join",
            "instance_id": instance_id,
            "tailscale_ip": "100.64.0.1",
            "mock": True,
        }

    from ._client import get_boto3_client
    ssm = get_boto3_client(creds, 'ssm')
    loop = asyncio.get_event_loop()

    script = [
        "set -e",
        "curl -fsSL https://tailscale.com/install.sh | sh",
        f"tailscale up --authkey={auth_key} --hostname={hostname} --accept-routes --accept-dns=false",
        # Set OS hostname so agent registers with this name
        f"hostnamectl set-hostname {hostname} 2>/dev/null || hostname {hostname} 2>/dev/null || true",
        "tailscale ip -4",
    ]

    def _call():
        import time
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": script},
        )
        command_id = resp['Command']['CommandId']
        for _ in range(24):  # up to 2 minutes
            time.sleep(5)
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
                return result
        return {"Status": "TimedOut", "StandardOutputContent": "", "StandardErrorContent": ""}

    result = await loop.run_in_executor(None, _call)
    if result['Status'] != 'Success':
        raise RuntimeError(f"tailscale_join SSM command failed: {result.get('StandardErrorContent', '')}")

    tailscale_ip = result.get('StandardOutputContent', '').strip().split('\n')[-1].strip()
    return {
        "action": "tailscale_join",
        "instance_id": instance_id,
        "hostname": hostname,
        "tailscale_ip": tailscale_ip,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.tailscale_remove import execute as remove
    return await remove({"instance_id": parameters.get('instance_id', '')}, [], connector)
