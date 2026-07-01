# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json


def get_credentials(creds: dict):
    from google.oauth2 import service_account
    key_json = json.loads(creds["service_account_key_json"])
    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/compute",
    ]
    return service_account.Credentials.from_service_account_info(key_json, scopes=scopes)


def get_project_id(creds: dict) -> str:
    return creds["project_id"]
