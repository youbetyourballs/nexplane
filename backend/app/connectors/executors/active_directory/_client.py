from ldap3 import Server, Connection, ALL


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
    use_ssl = str(creds.get("use_ssl", "false")).lower() == "true"
    port = int(creds.get("port", 636 if use_ssl else 389))
    server = Server(creds["server"], port=port, use_ssl=use_ssl, get_info=ALL)
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
