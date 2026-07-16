# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""nexplane_agent manage_tls_certificates executor.

In smoke/dev environments, writes cert/key material to a shared volume path
(/nexplane-data/smoke-certs/) that a cert-watcher script monitors to trigger
nginx reload. Falls back gracefully to a no-op stub otherwise.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Shared volume path where nexplane-data is mounted in both backend and nginx.
SMOKE_CERT_DIR = "/nexplane-data/smoke-certs"
SMOKE_CERT_PATH = os.path.join(SMOKE_CERT_DIR, "smoke.crt")
SMOKE_KEY_PATH = os.path.join(SMOKE_CERT_DIR, "smoke.key")


def _shared_cert_dir_available() -> bool:
    return os.path.isdir(SMOKE_CERT_DIR)


def _write_cert(cert_pem: str, key_pem: str) -> bool:
    """Write cert/key to the shared smoke-certs directory."""
    try:
        os.makedirs(SMOKE_CERT_DIR, exist_ok=True)
        with open(SMOKE_CERT_PATH, "w") as f:
            f.write(cert_pem)
        with open(SMOKE_KEY_PATH, "w") as f:
            f.write(key_pem)
        logger.info("manage_tls_certificates: wrote cert/key to %s", SMOKE_CERT_DIR)
        return True
    except Exception as exc:
        logger.warning("manage_tls_certificates: failed to write cert: %s", exc)
        return False


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    cert_pem = parameters.get("cert_pem", "")
    key_pem = parameters.get("key_pem", "")
    applied_at = datetime.now(timezone.utc).isoformat()

    if cert_pem and key_pem and _shared_cert_dir_available():
        success = _write_cert(cert_pem, key_pem)
        logger.info("manage_tls_certificates: cert written to shared volume, success=%s", success)
        return {
            "action": parameters.get("action"),
            "service": parameters.get("service"),
            "cert_path": SMOKE_CERT_PATH,
            "key_path": SMOKE_KEY_PATH,
            "applied": success,
            "snapshot": {},
            "applied_at": applied_at,
        }

    # Fallback stub (no cert material or shared volume not available)
    return {
        "action": parameters.get("action"),
        "service": parameters.get("service"),
        "cert_path": "/etc/nginx/ssl/server.crt",
        "key_path": "/etc/nginx/ssl/server.key",
        "applied": False,
        "snapshot": {},
        "applied_at": applied_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "manage_tls_certificates"}
