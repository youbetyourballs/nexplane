# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import copy
import io
import socket

import paramiko

from app.tunnel.routing import tcp_endpoint

ALLOWED_PREFIXES = (
    "systemctl status",
    "systemctl list-units",
    "systemctl is-active",
    "journalctl",
    "tail -n",
    "df -h",
    "df -",
    "free -h",
    "free -",
    "uptime",
    "uname -",
    "hostname",
    "cat /etc/os-release",
    "ps aux",
)


async def prepare_ssh_target(connector, creds: dict) -> dict:
    """Resolve the connector's routing endpoint for the SSH target.

    For ``via_agent`` connectors this returns a copy of ``creds`` annotated with
    ``_forward_host`` / ``_forward_port`` pointing at the in-process localhost
    forwarder. ``hostname`` is left untouched so host-key checking still targets
    the real host. For direct connectors the creds are returned unchanged (copy).

    Resolving here (in the async ``execute``) keeps the app event loop free to
    service the forwarder's accept coroutine, while the blocking paramiko
    connect stays off the loop inside ``run_in_executor``.
    """
    creds = copy.copy(creds)
    host = creds.get("hostname")
    port = int(creds.get("port", 22))
    if not host:
        return creds
    eh, ep = await tcp_endpoint(connector, host, port)
    if (eh, ep) != (host, port):
        creds["_forward_host"] = eh
        creds["_forward_port"] = ep
    return creds


def get_ssh_client(creds: dict) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": creds["hostname"],
        "username": creds["username"],
        "timeout": 30,
    }
    if creds.get("_forward_host"):
        # Routed via the agent tunnel: open a socket to the localhost forwarder
        # and hand it to paramiko. ``hostname`` stays the real host so host-key
        # verification is unchanged; do NOT pass ``port`` when ``sock`` is given.
        sock = socket.create_connection(
            (creds["_forward_host"], int(creds["_forward_port"])), timeout=30
        )
        kwargs["sock"] = sock
    else:
        kwargs["port"] = int(creds.get("port", 22))
    if creds.get("private_key"):
        kwargs["pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(creds["private_key"]))
    elif creds.get("password"):
        kwargs["password"] = creds["password"]
    client.connect(**kwargs)
    return client


WRITE_ALLOWED_PREFIXES = (
    "sudo systemctl start",
    "sudo systemctl stop",
    "sudo systemctl restart",
    "sudo systemctl enable",
    "sudo systemctl disable",
    "curl -fsSL",
    "sudo /tmp/nexplane-agent",
    "sudo apt-get install",
    "sudo yum install",
    "sudo dnf install",
    "chmod +x",
    "mkdir -p",
    "sudo mkdir -p",
    "sudo semodule -r",
    "sudo rm -f /tmp/nexplane-agent",
    "sudo rm -f /usr/local/bin/nexplane-agent",
    "sudo rm -f /etc/systemd/system/nexplane-agent.service",
    "sudo systemctl daemon-reload",
)


def is_allowed(cmd: str) -> bool:
    return any(cmd.strip().startswith(p) for p in ALLOWED_PREFIXES)


def is_write_allowed(cmd: str) -> bool:
    return any(cmd.strip().startswith(p) for p in WRITE_ALLOWED_PREFIXES)
