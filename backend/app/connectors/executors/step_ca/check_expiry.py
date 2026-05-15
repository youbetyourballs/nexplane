from __future__ import annotations
"""Check TLS certificate expiry on a host:port endpoint via step CLI.

Falls back to Python ssl module if step CLI is unavailable.
"""
import ssl
import socket
from datetime import datetime, timezone
from ._client import get_step_ca_client


def _check_via_ssl(host: str, port: int, timeout: int = 10) -> dict:
    """Pure-Python TLS cert check — no step CLI dependency.

    Uses DER binary form to parse the cert when CERT_NONE is set, since
    getpeercert() returns an empty dict without cert verification.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            # Binary form works regardless of verification mode
            cert_der = ssock.getpeercert(binary_form=True)
            cert = ssock.getpeercert()  # may be empty with CERT_NONE

    # Parse dates — try DER first (reliable), fall back to text dict
    not_before_dt: datetime
    not_after_dt: datetime
    subject_cn: str = ""
    issuer_cn: str = ""
    san_list: list = []

    if cert_der:
        try:
            from cryptography import x509 as _x509
            from cryptography.hazmat.primitives.hashes import SHA256 as _SHA256
            cert_obj = _x509.load_der_x509_certificate(cert_der)
            # not_valid_before_utc/not_valid_after_utc added in cryptography 42.x
            # Fall back to not_valid_before/not_valid_after for older versions
            try:
                not_before_dt = cert_obj.not_valid_before_utc
                not_after_dt = cert_obj.not_valid_after_utc
            except AttributeError:
                not_before_dt = cert_obj.not_valid_before.replace(tzinfo=timezone.utc)  # type: ignore[attr-defined]
                not_after_dt = cert_obj.not_valid_after.replace(tzinfo=timezone.utc)  # type: ignore[attr-defined]
            try:
                subject_cn = cert_obj.subject.get_attributes_for_oid(
                    _x509.NameOID.COMMON_NAME)[0].value
            except Exception:
                pass
            try:
                issuer_cn = cert_obj.issuer.get_attributes_for_oid(
                    _x509.NameOID.COMMON_NAME)[0].value
            except Exception:
                pass
            try:
                san_ext = cert_obj.extensions.get_extension_for_class(
                    _x509.SubjectAlternativeName)
                san_list = san_ext.value.get_values_for_type(_x509.DNSName)
            except Exception:
                pass
        except ImportError:
            # cryptography not available — parse text dict (works when cert is not None)
            cert = cert or {}
            fmt = "%b %d %H:%M:%S %Y %Z"
            not_after_str = cert.get("notAfter", "Jan 1 00:00:00 2099 GMT")
            not_before_str = cert.get("notBefore", "Jan 1 00:00:00 2020 GMT")
            not_after_dt = datetime.strptime(not_after_str, fmt).replace(tzinfo=timezone.utc)
            not_before_dt = datetime.strptime(not_before_str, fmt).replace(tzinfo=timezone.utc)
            subject_cn = dict(x[0] for x in cert.get("subject", [])).get("commonName", "")
            issuer_cn = dict(x[0] for x in cert.get("issuer", [])).get("commonName", "")
            san_list = [v for (k, v) in cert.get("subjectAltName", []) if k == "DNS"]
    else:
        # No cert obtained — connection succeeded but no cert (should not happen with TLS)
        now = datetime.now(timezone.utc)
        return {
            "host": host, "port": port,
            "not_before": None, "not_after": None,
            "remaining_seconds": 0, "remaining_days": 0,
            "subject_cn": None, "issuer_cn": None, "san": [],
            "expired": False, "warning": True,
            "method": "ssl_module",
            "error": "no_cert_obtained",
        }

    now = datetime.now(timezone.utc)
    # Ensure timezone-aware comparison
    if not_after_dt.tzinfo is None:
        not_after_dt = not_after_dt.replace(tzinfo=timezone.utc)
    if not_before_dt.tzinfo is None:
        not_before_dt = not_before_dt.replace(tzinfo=timezone.utc)
    remaining_seconds = int((not_after_dt - now).total_seconds())
    remaining_days = remaining_seconds // 86400

    return {
        "host": host,
        "port": port,
        "not_before": not_before_dt.isoformat(),
        "not_after": not_after_dt.isoformat(),
        "remaining_seconds": remaining_seconds,
        "remaining_days": remaining_days,
        "subject_cn": subject_cn,
        "issuer_cn": issuer_cn,
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
