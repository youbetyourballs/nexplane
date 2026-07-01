# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def get_tio(creds: dict):
    from tenable.io import TenableIO
    return TenableIO(access_key=creds["access_key"], secret_key=creds["secret_key"])
