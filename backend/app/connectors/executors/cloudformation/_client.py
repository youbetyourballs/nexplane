# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import boto3


def get_client(creds: dict):
    kwargs = {
        "aws_access_key_id": creds.get("access_key_id"),
        "aws_secret_access_key": creds.get("secret_access_key"),
        "region_name": creds.get("region", "us-east-1"),
    }
    if creds.get("session_token"):
        kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client("cloudformation", **kwargs)
