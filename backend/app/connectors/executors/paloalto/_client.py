# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def get_firewall(creds: dict):
    from panos.firewall import Firewall
    return Firewall(creds["hostname"], api_username=creds["username"], api_password=creds["password"])
