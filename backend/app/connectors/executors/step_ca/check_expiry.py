from __future__ import annotations
"""Check TLS certificate expiry on a host:port endpoint via step CLI.

Falls back to Python ssl module if step CLI is unavailable.
"""
import ssl
import socket
from datetime import datetime, timezone
from ._client import get_step_ca_client


def _check_via_ssl(host: str, port: int, timeout: int = 10) -> dict:
    """Pure-Python TLS cert check — no step CLI dependency."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    not_after_str = cert.get("notAfter", "")
    not_before_str = cert.get("notBefore", "")
    # Format: 'May 15 00:00:00 2026 GMT'
    fmt = "%b %d %H:%M:%S %Y %Z"
    not_after_dt = datetime.strptime(not_after_str, fmt).replace(tzinfo=timezone.utc)
    not_before_dt = datetime.strptime(not_before_str, fmt).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    remaining_seconds = int((not_after_dt - now).total_seconds())
    remaining_days = remaining_seconds // 86400

    subject = dict(x[0] for x in cert.get("subject", []))
    issuer = dict(x[0] for x in cert.get("issuer", []))
    san_list = [v for (k, v) in cert.get("subjectAltName", []) if k == "DNS"]

    return {
        "host": host,
        "port": port,
        "not_before": not_before_dt.isoformat(),
        "not_after": not_after_dt.isoformat(),
        "remaining_seconds": remaining_seconds,
        "remaining_days": remaining_days,
        "subject_cn": subject.get("commonName"),
        "issuer_cn": issuer.get("commonName"),
        "san": san_list,
        "expired": remaining_seconds < 0,
        "warning": remaining_days < 30,
        "method": "ssl_module",
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    host = parameters.get("host", "")
    port = int(parameters.get("port", 443))
    warning_threshold_days = int(parameters.get("warning_threshold_days", 30))

    if not host:
        raise ValueError("host parameter is required")

    # Try step CLI first if client configured, fall back to ssl module
    client = get_step_ca_client(connector)
    expiry_info = None

    if client:
        try:
            info = client.check_endpoint_expiry(host, port)
            remaining_s = info.get("remaining_seconds") or 0
            expiry_info = {
                "host": host,
                "port": port,
                "not_before": info.get("not_before"),
                "not_after": info.get("not_after"),
                "remaining_seconds": remaining_s,
                "remaining_days": remaining_s // 86400,
                "subject_cn": info.get("subject"),
                "issuer_cn": info.get("issuer"),
                "expired": remaining_s < 0,
                "warning": (remaining_s // 86400) < warning_threshold_days,
                "method": "step_cli",
            }
        except Exception:
            pass

    if expiry_info is None:
        expiry_info = _check_via_ssl(host, port)
        expiry_info["warning"] = expiry_info["remaining_days"] < warning_threshold_days

    return {
        "action": "step_ca_check_expiry",
        "status": "checked",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        **expiry_info,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_expiry is read-only — no rollback needed"}
