# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: disable a vulnerable network protocol on Linux via OpenSSL/sysctl config."""
from typing import Any

_CONFIG_FILE = "/etc/ssl/openssl.cnf"

_PROTOCOL_MIN_VERSION = {
    "tls10": "TLSv1.2",
    "tls11": "TLSv1.2",
    "sslv3": "TLSv1",
    "smbv1": None,
    "ntlmv1": None,
}

_SMB_DISABLE_CMD = (
    "sysctl -w kernel.smb1_support=0 2>/dev/null; "
    "echo 'kernel.smb1_support=0' >> /etc/sysctl.d/99-nexplane.conf"
)
_NTLM_DISABLE_CMD = (
    "echo 'NTLM_DISABLE=yes' >> /etc/security/limits.conf"
)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    protocol = parameters["protocol"]
    min_ver = _PROTOCOL_MIN_VERSION.get(protocol)

    if min_ver is not None:
        pre_line_raw = await connector.run_command(
            f"grep -m1 'MinProtocol' {_CONFIG_FILE} 2>/dev/null || echo ''"
        )
        original_line = pre_line_raw.strip() or None
        await connector.run_command(
            f"grep -q 'MinProtocol' {_CONFIG_FILE} 2>/dev/null && "
            f"sed -i 's/MinProtocol.*/MinProtocol = {min_ver}/' {_CONFIG_FILE} || "
            f"echo 'MinProtocol = {min_ver}' >> {_CONFIG_FILE}"
        )
        return {
            "success": True,
            "protocol": protocol,
            "pre_state": {"original_line": original_line, "config_file": _CONFIG_FILE},
        }

    if protocol == "smbv1":
        await connector.run_command(_SMB_DISABLE_CMD)
        return {"success": True, "protocol": protocol, "pre_state": {"original_line": None, "config_file": None}}

    if protocol == "ntlmv1":
        await connector.run_command(_NTLM_DISABLE_CMD)
        return {"success": True, "protocol": protocol, "pre_state": {"original_line": None, "config_file": None}}

    raise ValueError(f"Unsupported protocol: {protocol}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    pre_state = execution_result.get("pre_state", {})
    config_file = pre_state.get("config_file") or _CONFIG_FILE
    original_line = pre_state.get("original_line")

    if original_line:
        await connector.run_command(
            f"sed -i 's/MinProtocol.*/{original_line}/' {config_file}"
        )
    else:
        await connector.run_command(
            f"sed -i '/^MinProtocol/d' {config_file}"
        )
    return {"rolled_back": True}
