# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user",
    "https://www.googleapis.com/auth/admin.directory.group",
    "https://www.googleapis.com/auth/admin.directory.device.chromeos",
    "https://www.googleapis.com/auth/admin.directory.device.mobile",
    "https://www.googleapis.com/auth/admin.directory.rolemanagement",
    "https://www.googleapis.com/auth/admin.reports.audit.readonly",
]


def get_admin_service(creds: dict, service_name: str, version: str):
    key_info = json.loads(creds["service_account_key_json"])
    credentials = service_account.Credentials.from_service_account_info(key_info, scopes=SCOPES)
    delegated = credentials.with_subject(creds["admin_email"])
    return build(service_name, version, credentials=delegated)
