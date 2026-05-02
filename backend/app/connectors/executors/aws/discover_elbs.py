import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_elbs",
        "assets": [
            {"id": "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/prod-alb/abc", "name": "prod-alb", "asset_type": "server", "metadata": {"type": "application", "scheme": "internet-facing", "state": "active"}},
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
                    "asset_type": "server",
                    "metadata": {
                        "type": lb.get('Type'),
                        "scheme": lb.get('Scheme'),
                        "state": lb.get('State', {}).get('Code'),
                        "dns_name": lb.get('DNSName'),
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
