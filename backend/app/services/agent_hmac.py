# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import hmac as _hmac
import hashlib
import json


def compute_job_signature(secret: str, job_id: str, command: str, parameters: dict) -> str:
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    message = f"{job_id}:{command}:{canonical}".encode()
    return _hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_job_signature(
    secret: str, job_id: str, command: str, parameters: dict, signature: str
) -> bool:
    expected = compute_job_signature(secret, job_id, command, parameters)
    return _hmac.compare_digest(expected, signature)
