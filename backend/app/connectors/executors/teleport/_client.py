# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import subprocess
import json


class TeleportClient:
    """Teleport client that wraps the tctl CLI.

    Assumes tctl is installed and either:
    - A valid tctl auth file is in place (managed node), OR
    - The connector supplies a proxy address and auth token for `tctl` login.

    The primary operations (lock/unlock user) are simpler via tctl subprocess
    than via the REST API, which requires mTLS certificates.
    """

    def __init__(self, proxy_addr, auth_token=None, tctl_bin="tctl"):
        self.proxy_addr = proxy_addr  # e.g. "teleport.example.com:3025"
        self.auth_token = auth_token
        self.tctl_bin = tctl_bin

    def _run(self, args, timeout=30):
        cmd = [self.tctl_bin] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"tctl failed ({result.returncode}): {result.stderr.strip()}")
        return result.stdout.strip()

    def lock_user(self, username, ttl="1h", message="Locked by Nexplane"):
        self._run(["lock", f"--user={username}", f"--ttl={ttl}", f"--message={message}"])
        # Get the lock ID so we can delete it on rollback
        lock_id = self._get_lock_id_for_user(username)
        return {"success": True, "username": username, "lock_id": lock_id, "ttl": ttl}

    def _get_lock_id_for_user(self, username):
        try:
            out = self._run(["locks", "ls", "--format=json"])
            locks = json.loads(out) if out else []
            for lock in locks:
                spec = lock.get("spec", {})
                target = spec.get("target", {})
                if target.get("user") == username:
                    return lock.get("metadata", {}).get("name", "")
        except Exception:
            pass
        return ""

    def delete_lock(self, lock_id):
        if not lock_id:
            return {"success": False, "reason": "no_lock_id"}
        self._run(["locks", "rm", lock_id])
        return {"success": True, "lock_id": lock_id, "deleted": True}

    def list_locks(self):
        out = self._run(["locks", "ls", "--format=json"])
        return json.loads(out) if out else []


def get_teleport_client(connector):
    creds = getattr(connector, "credentials", None) or {}
    proxy_addr = creds.get("proxy_addr") or creds.get("proxy_address") or creds.get("url")
    if not proxy_addr:
        return None
    return TeleportClient(
        proxy_addr=proxy_addr,
        auth_token=creds.get("auth_token") or creds.get("token"),
        tctl_bin=creds.get("tctl_bin", "tctl"),
    )
