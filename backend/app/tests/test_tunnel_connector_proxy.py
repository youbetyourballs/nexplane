# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import asyncio, re
from app.tunnel.connector_proxy import ConnectorSocksProxy

def test_proxy_url_shape():
    async def run():
        p = ConnectorSocksProxy()
        url = await p.proxy_url("agent-77")
        assert re.match(r"^socks5://agent-77:[A-Za-z0-9_-]+@127\.0\.0\.1:\d+$", url), url
        # second call reuses the same listener/token
        url2 = await p.proxy_url("agent-88")
        assert url.split("@")[1] == url2.split("@")[1]  # same host:port
        assert url.split(":")[2].split("@")[0] == url2.split(":")[2].split("@")[0]  # same token
        await p.stop()
    asyncio.run(run())
