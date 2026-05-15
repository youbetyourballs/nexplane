from __future__ import annotations
"""Rotate (reissue) a TLS certificate using step-ca.

Optionally deploys the new cert to a remote host via SSM and triggers a reload.
"""
import os
import tempfile
from datetime import datetime, timezone
from ._client import get_step_ca_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    subject = parameters.get("subject", "")
    san = parameters.get("san") or subject
    not_after = parameters.get("not_after", "720h")  # 30 days default
    deploy_via_ssm = parameters.get("deploy_via_ssm", False)
    instance_id = parameters.get("instance_id", "")
    cert_dest_path = parameters.get("cert_dest_path", "/etc/ssl/certs/nexplane.crt")
    key_dest_path = parameters.get("key_dest_path", "/etc/ssl/private/nexplane.key")
    reload_command = parameters.get("reload_command", "nginx -s reload")

    if not subject:
        raise ValueError("subject parameter is required")

    client = get_step_ca_client(connector)
    if not client:
        return {
            "action": "step_ca_rotate_cert",
            "status": "skipped",
            "reason": "no_step_ca_credentials",
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "cert.crt")
        key_path = os.path.join(tmpdir, "cert.key")

        issue_result = client.issue_certificate(
            subject=subject,
            san=san,
            output_cert=cert_path,
            output_key=key_path,
            not_after=not_after,
        )

        # Read cert content for optional SSM deployment
        cert_content = open(cert_path).read()
        key_content = open(key_path).read()

        ssm_result = None
        if deploy_via_ssm and instance_id:
            ssm_result = _deploy_via_ssm(
                instance_id=instance_id,
                cert_content=cert_content,
                key_content=key_content,
                cert_dest_path=cert_dest_path,
                key_dest_path=key_dest_path,
                reload_command=reload_command,
            )

    return {
        "action": "step_ca_rotate_cert",
        "status": "issued",
        "subject": subject,
        "san": san,
        "not_after": not_after,
        "deployed_via_ssm": ssm_result is not None,
        "ssm_result": ssm_result,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


def _deploy_via_ssm(instance_id: str, cert_content: str, key_content: str,
                    cert_dest_path: str, key_dest_path: str,
                    reload_command: str) -> dict:
    try:
        import boto3
        ssm = boto3.client("ssm")
        # Escape single quotes in content for shell heredoc safety
        cert_escaped = cert_content.replace("'", "'\"'\"'")
        key_escaped = key_content.replace("'", "'\"'\"'")
        command = f"""
set -e
cat > {cert_dest_path} << 'CERTEOF'
{cert_escaped}
CERTEOF
cat > {key_dest_path} << 'KEYEOF'
{key_escaped}
KEYEOF
chmod 644 {cert_dest_path}
chmod 600 {key_dest_path}
{reload_command} || true
echo "CERT_DEPLOYED"
"""
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
            TimeoutSeconds=30,
        )
        return {"command_id": resp["Command"]["CommandId"], "status": "sent"}
    except Exception as e:
        return {"error": str(e), "status": "failed"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Certificate rotation is not reversible — old cert is expired or superseded. Revoke via step-ca revoke if needed.",
    }
