# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only discovery — no state was changed"


def _mock_response():
    return {
        "action": "discover_elbs",
        "assets": [
            {
                "id": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/prod-alb/abc",
                "name": "prod-alb",
                "asset_type": "load_balancer",
                "asset_metadata": {
                    "lb_arn": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/prod-alb/abc",
                    "lb_type": "application",
                    "scheme": "internet-facing",
                    "state": "active",
                    "dns_name": "prod-alb-123.us-east-1.elb.amazonaws.com",
                    "provider": "aws",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_boto3_client
    elbv2 = get_boto3_client(creds, 'elbv2')
    loop = asyncio.get_event_loop()

    def _call():
        paginator = elbv2.get_paginator('describe_load_balancers')
        assets = []
        for page in paginator.paginate():
            for lb in page['LoadBalancers']:
                assets.append({
                    "id": lb['LoadBalancerArn'],
                    "name": lb['LoadBalancerName'],
                    "asset_type": "load_balancer",
                    "asset_metadata": {
                        "lb_arn": lb['LoadBalancerArn'],
                        "lb_type": lb.get('Type'),
                        "scheme": lb.get('Scheme'),
                        "state": lb.get('State', {}).get('Code'),
                        "dns_name": lb.get('DNSName'),
                        "provider": "aws",
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_elbs", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
