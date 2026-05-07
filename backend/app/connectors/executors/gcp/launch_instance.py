import asyncio
from datetime import datetime, timezone


_AGENT_STARTUP_TEMPLATE = """#!/bin/bash
set -e
NEXPLANE_URL="{nexplane_url}"
NEXPLANE_SECRET="{nexplane_secret}"
VERSION=$(curl -fsSL https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version)
curl -fsSL "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-linux-amd64-${{VERSION}}" \
  -o /usr/local/bin/nexplane-agent
chmod +x /usr/local/bin/nexplane-agent

cat > /etc/systemd/system/nexplane-agent.service <<EOF
[Unit]
Description=Nexplane Agent
After=network.target
StartLimitIntervalSec=0

[Service]
ExecStart=/usr/local/bin/nexplane-agent --control-plane $NEXPLANE_URL --secret $NEXPLANE_SECRET --mode service
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nexplane-agent
systemctl start nexplane-agent
"""


_WINDOWS_AGENT_PS1 = r"""# Install Tailscale for Windows
$tsInstaller = "$env:TEMP\tailscale-setup.exe"
Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup.exe" `
  -OutFile $tsInstaller -UseBasicParsing
Start-Process $tsInstaller -Args "/S" -Wait
Start-Sleep -Seconds 10
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --authkey="{tailscale_auth_key}" --hostname="{hostname}" --accept-routes

# Download and install Nexplane agent
$version = (Invoke-WebRequest `
  "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version" `
  -UseBasicParsing).Content.Trim()
$agentUrl = "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$version.exe"
Invoke-WebRequest $agentUrl -OutFile "C:\nexplane-agent.exe" -UseBasicParsing

New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane-agent.exe --control-plane {nexplane_url} --secret {nexplane_secret} --mode service" `
  -StartupType Automatic -Description "Nexplane Agent" -ErrorAction SilentlyContinue
Start-Service "NexplaneAgent"
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    machine_type = parameters.get("machine_type", "e2-micro")
    zone = parameters.get("zone", "us-central1-a")
    image_family = parameters.get("image_family", "ubuntu-2204-lts")
    image_project = parameters.get("image_project", "ubuntu-os-cloud")
    connection_mode = parameters.get("connection_mode", "agent_startup")
    nexplane_url = parameters.get("nexplane_url", "")
    nexplane_secret = parameters.get("nexplane_secret", "")
    ssh_public_key = parameters.get("ssh_public_key", "")
    network_tags = parameters.get("network_tags", [])
    tailscale_auth_key = parameters.get("tailscale_auth_key", "")
    os_type = parameters.get("os", "linux")
    labels = parameters.get("labels", {"managed-by": "nexplane"})

    # Override image and machine type for Windows
    if os_type == "windows":
        image_family = parameters.get("image_family", "windows-server-2022-dc")
        image_project = parameters.get("image_project", "windows-cloud")
        machine_type = parameters.get("machine_type", "e2-medium")  # Windows needs >=4GB RAM

    auto_asset = {
        "name": name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "instance_name": name,
            "zone": zone,
            "machine_type": machine_type,
            "connection_mode": connection_mode,
            "image_family": image_family,
            "provider": "gcp",
            "os_type": os_type,
        },
        "tags": ["gce", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "launch_instance",
            "instance_name": name,
            "zone": zone,
            "machine_type": machine_type,
            "connection_mode": connection_mode,
            "status": "STAGING",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_running_loop()

    # Resolve latest image from the family
    images_client = compute_v1.ImagesClient(credentials=credentials)
    image = await loop.run_in_executor(
        None, lambda: images_client.get_from_family(project=image_project, family=image_family)
    )
    image_link = image.self_link

    # Build metadata items and network tags based on connection_mode
    if connection_mode == "agent_startup":
        if os_type == "windows":
            # Windows uses windows-startup-script-ps1 metadata key
            ps1_script = _WINDOWS_AGENT_PS1.format(
                tailscale_auth_key=tailscale_auth_key,
                hostname=name,
                nexplane_url=nexplane_url,
                nexplane_secret=nexplane_secret,
            )
            metadata_items = [
                compute_v1.Items(key="windows-startup-script-ps1", value=ps1_script)
            ]
            tags_list = list(network_tags)
        else:
            tailscale_section = ""
            if tailscale_auth_key:
                tailscale_section = f"""# Install and join Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{name}" --accept-routes
"""
            startup_script = parameters.get(
                "startup_script",
                tailscale_section + _AGENT_STARTUP_TEMPLATE.format(
                    nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
                ),
            )
            metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)]
            tags_list = list(network_tags)
    elif connection_mode == "iap":
        startup_script = parameters.get("startup_script", "")
        metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)] if startup_script else []
        tags_list = list(set(network_tags) | {"allow-iap-ssh"})
    else:  # ssh
        metadata_items = []
        if ssh_public_key:
            metadata_items.append(compute_v1.Items(key="ssh-keys", value=f"ubuntu:{ssh_public_key}"))
        tags_list = list(network_tags)

    instance_body = compute_v1.Instance(
        name=name,
        machine_type=f"zones/{zone}/machineTypes/{machine_type}",
        disks=[
            compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(source_image=image_link),
            )
        ],
        network_interfaces=[compute_v1.NetworkInterface(
            network="global/networks/default",
            access_configs=[compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT")]
        )],
        metadata=compute_v1.Metadata(items=metadata_items),
        tags=compute_v1.Tags(items=tags_list),
        labels=labels,
    )

    instances_client = compute_v1.InstancesClient(credentials=credentials)
    op = await loop.run_in_executor(
        None, lambda: instances_client.insert(project=project, zone=zone, instance_resource=instance_body)
    )

    # Wait for RUNNING state
    from app.connectors.executors.gcp.wait_instance_state import execute as wait
    await wait({"instance_name": name, "zone": zone, "target_state": "RUNNING"}, [], connector)

    # Fetch internal IP
    instance = await loop.run_in_executor(
        None, lambda: instances_client.get(project=project, zone=zone, instance=name)
    )
    internal_ip = ""
    if instance.network_interfaces:
        internal_ip = instance.network_interfaces[0].network_i_p or ""

    auto_asset["asset_metadata"]["internal_ip"] = internal_ip

    return {
        "action": "launch_instance",
        "instance_name": name,
        "zone": zone,
        "machine_type": machine_type,
        "connection_mode": connection_mode,
        "internal_ip": internal_ip,
        "operation": op.name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_instance import execute as delete
    return await delete(
        {
            "instance_name": execution_result.get("instance_name", parameters.get("name")),
            "zone": execution_result.get("zone", parameters.get("zone", "us-central1-a")),
        },
        [], connector,
    )
