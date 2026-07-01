# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def get_slack_client(creds: dict, token_type: str = "admin"):
    """Return a slack_sdk.WebClient using admin_token (xoxp-) or bot_token (xoxb-)."""
    from slack_sdk import WebClient
    if token_type == "admin":
        token = creds.get("admin_token") or creds.get("bot_token")
    else:
        token = creds.get("bot_token")
    if not token:
        raise ValueError(f"Slack credential missing '{token_type}_token'")
    return WebClient(token=token)
