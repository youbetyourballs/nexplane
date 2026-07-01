# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""step-ca client — wraps the `step` CLI via subprocess.

The step CLI must be installed on the machine running the executor
(typically an EC2 runner or Docker container with step pre-installed).
Alternatively, the admin REST API is used for certificate inspection.
"""
import json
import os
import subprocess
import tempfile
from typing import Optional


class StepCAClient:
    def __init__(self, ca_url: str, fingerprint: str,
                 provisioner: str = "admin",
                 provisioner_password: Optional[str] = None,
                 step_cli: str = "step"):
        self.ca_url = ca_url.rstrip("/")
        self.fingerprint = fingerprint
        self.provisioner = provisioner
        self.provisioner_password = provisioner_password
        self.step_cli = step_cli

    def _run(self, args: list, input_data: Optional[str] = None, timeout: int = 60) -> str:
        """Run a step CLI command and return stdout. Raises on non-zero exit."""
        env = os.environ.copy()
        result = subprocess.run(
            [self.step_cli] + args,
            input=input_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"step command failed: {' '.join(args)}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def bootstrap(self) -> str:
        """Configure step CLI to trust this CA."""
        return self._run([
            "ca", "bootstrap",
            "--ca-url", self.ca_url,
            "--fingerprint", self.fingerprint,
            "--install",
        ])

    def issue_certificate(self, subject: str, san: str, output_cert: str,
                          output_key: str, not_after: str = "24h") -> dict:
        """Issue a certificate using ACME or JWK provisioner via step CLI.

        Returns dict with cert_path and key_path.
        """
        args = [
            "ca", "certificate",
            subject,
            output_cert,
            output_key,
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--san", san,
            "--not-after", not_after,
            "--provisioner", self.provisioner,
        ]
        if self.provisioner_password:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.provisioner_password)
                pw_file = f.name
            args += ["--provisioner-password-file", pw_file]
        else:
            args += ["--no-password", "--insecure"]

        self._run(args)
        return {"cert_path": output_cert, "key_path": output_key}

    def inspect_certificate(self, cert_path: str) -> dict:
        """Return parsed JSON of a certificate file."""
        out = self._run(["certificate", "inspect", cert_path, "--format", "json"])
        return json.loads(out)

    def check_endpoint_expiry(self, host: str, port: int = 443, timeout: int = 10) -> dict:
        """Return remaining validity information for a TLS cert on a live endpoint."""
        out = self._run([
            "certificate", "inspect",
            f"https://{host}:{port}",
            "--format", "json",
            "--insecure",
        ], timeout=timeout)
        data = json.loads(out)
        validity = data.get("validity", {})
        return {
            "host": host,
            "port": port,
            "not_before": validity.get("start"),
            "not_after": validity.get("end"),
            "remaining_seconds": validity.get("remainingSeconds"),
            "subject": data.get("subject", {}).get("commonName"),
            "issuer": data.get("issuer", {}).get("commonName"),
        }

    def revoke_certificate(self, serial: str) -> str:
        args = [
            "ca", "revoke", serial,
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--provisioner", self.provisioner,
        ]
        if self.provisioner_password:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.provisioner_password)
                args += ["--provisioner-password-file", f.name]
        return self._run(args)

    def list_certificates(self) -> list[dict]:
        """List certificates issued by this step-CA. Returns list with serial, subject, expiry (datetime)."""
        from datetime import datetime, timezone
        out = self._run([
            "ca", "admin", "list",
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--format", "json",
        ])
        try:
            raw = json.loads(out)
        except json.JSONDecodeError:
            raw = []
        if not isinstance(raw, list):
            raw = [raw] if raw else []
        certs = []
        for item in raw:
            expiry_str = item.get("expiry") or item.get("not_after")
            expiry_dt = None
            if expiry_str:
                try:
                    expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            certs.append({
                "serial": item.get("serial", ""),
                "subject": item.get("subject", ""),
                "expiry": expiry_dt,
            })
        return certs

    def renew_certificate(self, serial: str) -> None:
        """Trigger ACME renewal for a certificate by serial number."""
        args = [
            "ca", "renew",
            "--ca-url", self.ca_url,
            "--root", "/etc/step/certs/root_ca.crt",
            "--serial", serial,
        ]
        if self.provisioner_password:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(self.provisioner_password)
                args += ["--provisioner-password-file", f.name]
        self._run(args)

    @classmethod
    def from_connector(cls, connector) -> "StepCAClient":
        """Construct a StepCAClient from a Connector ORM object."""
        creds = getattr(connector, "credentials", None) or {}
        ca_url = creds.get("ca_url") or creds.get("url", "")
        return cls(
            ca_url=ca_url,
            fingerprint=creds.get("fingerprint", ""),
            provisioner=creds.get("provisioner", "admin"),
            provisioner_password=creds.get("provisioner_password") or creds.get("password"),
            step_cli=creds.get("step_cli", "step"),
        )


def get_step_ca_client(connector) -> Optional[StepCAClient]:
    creds = getattr(connector, "credentials", None) or {}
    ca_url = creds.get("ca_url") or creds.get("url")
    fingerprint = creds.get("fingerprint", "")
    if not ca_url:
        return None
    return StepCAClient(
        ca_url=ca_url,
        fingerprint=fingerprint,
        provisioner=creds.get("provisioner", "admin"),
        provisioner_password=creds.get("provisioner_password") or creds.get("password"),
        step_cli=creds.get("step_cli", "step"),
    )
