# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import winrm

from app.tunnel.routing import tcp_endpoint

CMD_ALLOWLIST = (
    "sc query",
    "sc qc",
    "tasklist",
    "systeminfo",
    "ipconfig",
    "netstat",
    "dir",
    "type",
    "echo",
    "hostname",
    "ver",
)

PS_ALLOWLIST = (
    "Get-Service",
    "Start-Service",
    "Stop-Service",
    "Restart-Service",
    "Set-Service",
    "Get-EventLog",
    "Get-WinEvent",
    "Install-WindowsFeature",
    "Remove-WindowsFeature",
    "New-Item",
    "Remove-Item",
    "Copy-Item",
    "Invoke-WebRequest",
    "Start-BitsTransfer",
    "Start-Process",
    "Stop-Process",
    "Set-ExecutionPolicy",
    "sc.exe create",
    "sc.exe delete",
    "sc.exe start",
    "sc.exe stop",
)


class WinRMClient:
    def __init__(self, hostname, port, username, password, use_ssl, verify_ssl):
        # type: (str, int, str, str, bool, bool) -> None
        self._hostname = hostname
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._verify_ssl = verify_ssl

    def _session(self):
        # type: () -> winrm.Session
        scheme = "https" if self._use_ssl else "http"
        target = "{}://{}:{}/wsman".format(scheme, self._hostname, self._port)
        server_cert_validation = "validate" if self._verify_ssl else "ignore"
        return winrm.Session(
            target,
            auth=(self._username, self._password),
            transport="ntlm",
            server_cert_validation=server_cert_validation,
        )

    def run_cmd(self, command):
        # type: (str) -> tuple
        """Run a cmd.exe command. Returns (stdout, stderr, exit_code)."""
        session = self._session()
        r = session.run_cmd(command)
        return (
            r.std_out.decode("utf-8", errors="replace"),
            r.std_err.decode("utf-8", errors="replace"),
            r.status_code,
        )

    def run_ps(self, script):
        # type: (str) -> tuple
        """Run a PowerShell script. Returns (stdout, stderr, exit_code)."""
        session = self._session()
        r = session.run_ps(script)
        return (
            r.std_out.decode("utf-8", errors="replace"),
            r.std_err.decode("utf-8", errors="replace"),
            r.status_code,
        )

    def is_allowed_cmd(self, cmd):
        # type: (str) -> bool
        return any(cmd.strip().startswith(p) for p in CMD_ALLOWLIST)

    def is_allowed_ps(self, script):
        # type: (str) -> bool
        return any(script.strip().startswith(p) for p in PS_ALLOWLIST)


def get_winrm_client(connector):
    # type: (...) -> WinRMClient
    creds = getattr(connector, "credentials", {}) or {}
    hostname = creds.get("hostname", "")
    port = int(creds.get("port", 5985))
    username = creds.get("username", "")
    password = creds.get("password", "")
    use_ssl_raw = creds.get("use_ssl", "false")
    use_ssl = use_ssl_raw is True or str(use_ssl_raw).lower() == "true"
    verify_ssl_raw = creds.get("verify_ssl", "false")
    verify_ssl = verify_ssl_raw is True or str(verify_ssl_raw).lower() == "true"
    return WinRMClient(hostname, port, username, password, use_ssl, verify_ssl)


async def prepare_winrm_client(connector):
    # type: (...) -> WinRMClient
    """Build a WinRMClient, routing the session target through the agent tunnel.

    For ``via_agent`` connectors the WinRM session URL is pointed at the
    in-process localhost forwarder (``127.0.0.1:<forwarded_port>``). When routed
    over HTTPS the real server cert cannot be validated through the localhost
    forwarder, so certificate validation is disabled if the connector has
    ``network_tls_skip_verify`` set (otherwise the real cert verification would
    fail against the forwarder endpoint).

    Resolving here (in the async ``execute``) keeps the app event loop free to
    service the forwarder's accept coroutine while the blocking pywinrm calls run
    off the loop in ``run_in_executor``.
    """
    creds = getattr(connector, "credentials", {}) or {}
    hostname = creds.get("hostname", "")
    port = int(creds.get("port", 5985))
    username = creds.get("username", "")
    password = creds.get("password", "")
    use_ssl_raw = creds.get("use_ssl", "false")
    use_ssl = use_ssl_raw is True or str(use_ssl_raw).lower() == "true"
    verify_ssl_raw = creds.get("verify_ssl", "false")
    verify_ssl = verify_ssl_raw is True or str(verify_ssl_raw).lower() == "true"

    eh, ep = await tcp_endpoint(connector, hostname, port)
    if (eh, ep) != (hostname, port):
        # Routed via the forwarder: connect to it, and disable cert validation
        # when the operator opted into skip-verify (real cert can't validate
        # against a localhost forwarder endpoint).
        hostname = eh
        port = ep
        if getattr(connector, "network_tls_skip_verify", False):
            verify_ssl = False

    return WinRMClient(hostname, port, username, password, use_ssl, verify_ssl)
