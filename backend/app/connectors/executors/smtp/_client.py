# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import smtplib
import ssl


def get_smtp_connection(creds: dict) -> smtplib.SMTP:
    host = creds["host"]
    port = int(creds.get("port", 587))
    use_tls = str(creds.get("use_tls", "true")).lower() == "true"

    if port == 465:
        context = ssl.create_default_context()
        conn = smtplib.SMTP_SSL(host, port, context=context)
    else:
        conn = smtplib.SMTP(host, port)
        if use_tls:
            conn.starttls()

    username = creds.get("username")
    password = creds.get("password")
    if username and password:
        conn.login(username, password)

    return conn
