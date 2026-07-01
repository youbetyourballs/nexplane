# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import ssl

from ldap3 import Server, Connection, ALL, Tls

from app.tunnel.routing import tcp_endpoint


def _ldap_use_ssl(creds: dict) -> bool:
    return str(creds.get("use_ssl", "false")).lower() == "true"


def _ldap_port(creds: dict) -> int:
    return int(creds.get("port", 636 if _ldap_use_ssl(creds) else 389))


async def prepare_ad_target(connector, creds: dict) -> dict:
    """Return a copy of creds with the LDAP endpoint stamped for tunnel routing.

    When the connector's network_path is via_agent, the raw LDAP TCP connection
    is routed through a localhost forwarder. We stamp `_forward_host`/`_forward_port`
    (and `_forward_skip_verify`) so the threaded get_connection() below builds its
    ldap3 Server against the forwarder instead of the real DC. Direct connectors
    get an unchanged copy.
    """
    server = creds.get("server")
    if not server:
        return dict(creds)
    port = _ldap_port(creds)
    ep_host, ep_port = await tcp_endpoint(connector, server, port)
    out = dict(creds)
    if (ep_host, ep_port) != (server, port):
        out["_forward_host"] = ep_host
        out["_forward_port"] = ep_port
        # Routed LDAPS to a localhost forwarder cannot verify the real cert host.
        if _ldap_use_ssl(creds) and getattr(connector, "network_tls_skip_verify", False):
            out["_forward_skip_verify"] = True
    return out


def get_winrm_session(creds: dict, dc_hostname: str | None = None):
    """Return a winrm.Session using connector winrm_* credentials.

    Uses basic transport + run_ps() which base64-encodes scripts via
    -EncodedCommand, bypassing cmd.exe pipe interpretation issues.
    """
    import winrm

    host = dc_hostname or creds.get("winrm_hostname", "")
    port = int(creds.get("winrm_port", 5985))
    use_ssl = str(creds.get("winrm_use_ssl", "false")).lower() == "true"
    scheme = "https" if use_ssl else "http"

    return winrm.Session(
        target=f"{scheme}://{host}:{port}/wsman",
        auth=(creds["winrm_username"], creds["winrm_password"]),
        transport="basic",
        server_cert_validation="ignore",
    )


def run_winrm_ps(creds: dict, script: str, dc_hostname: str | None = None) -> tuple[str, str, int]:
    """Run a PowerShell script via WinRM Session.run_ps() and return (stdout, stderr, rc)."""
    s = get_winrm_session(creds, dc_hostname)
    result = s.run_ps(script)
    stdout = result.std_out.decode("utf-8", errors="replace").strip() if result.std_out else ""
    stderr = result.std_err.decode("utf-8", errors="replace").strip() if result.std_err else ""
    return stdout, stderr, result.status_code


def get_connection(creds: dict) -> Connection:
    use_ssl = _ldap_use_ssl(creds)
    # When routed via the agent tunnel, prepare_ad_target() has stamped the
    # forwarder endpoint into the creds; connect there instead of the real DC.
    host = creds.get("_forward_host", creds["server"])
    port = int(creds.get("_forward_port", _ldap_port(creds)))
    tls = None
    if use_ssl and creds.get("_forward_skip_verify"):
        tls = Tls(validate=ssl.CERT_NONE)
    server = Server(host, port=port, use_ssl=use_ssl, get_info=ALL, tls=tls)
    return Connection(server, user=creds["bind_dn"], password=creds["bind_password"], auto_bind=True)


def has_ssm_transport(creds: dict) -> bool:
    """True when credentials specify an EC2 instance to reach via SSM instead of direct LDAP."""
    return bool(creds.get("ssm_instance_id"))


async def run_ssm_powershell(creds: dict, commands: list[str], timeout: int = 60) -> str:
    """Run PowerShell commands on a DC via SSM and return stdout. Raises on failure."""
    import asyncio
    import boto3

    instance_id = creds["ssm_instance_id"]
    region = creds.get("ssm_region", "us-east-1")
    aws_key = creds.get("aws_access_key_id")
    aws_secret = creds.get("aws_secret_access_key")

    def _sync():
        kwargs: dict = {"region_name": region}
        if aws_key and aws_secret:
            kwargs["aws_access_key_id"] = aws_key
            kwargs["aws_secret_access_key"] = aws_secret
        ssm = boto3.client("ssm", **kwargs)
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunPowerShellScript",
            Parameters={"commands": commands},
            TimeoutSeconds=timeout,
        )
        cmd_id = resp["Command"]["CommandId"]
        import time
        deadline = time.time() + timeout + 30
        while time.time() < deadline:
            time.sleep(5)
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            status = inv["Status"]
            if status == "Success":
                return inv.get("StandardOutputContent", "")
            if status in ("Failed", "TimedOut", "Cancelled"):
                err = inv.get("StandardErrorContent", "")
                raise RuntimeError(f"SSM command {status}: {err[:300]}")
        raise TimeoutError(f"SSM command did not complete within {timeout}s")

    return await asyncio.get_event_loop().run_in_executor(None, _sync)
