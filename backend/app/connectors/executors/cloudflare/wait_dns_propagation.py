import asyncio
from datetime import datetime, timezone

_CHECK_RESOLVERS = ["1.1.1.1", "8.8.8.8"]


async def _dns_query(record_name: str, expected_value: str, resolver_ip: str) -> bool:
    loop = asyncio.get_event_loop()

    def _lookup():
        try:
            import dns.resolver
            r = dns.resolver.Resolver()
            r.nameservers = [resolver_ip]
            answers = r.resolve(record_name, "A")
            return any(str(rdata) == expected_value for rdata in answers)
        except Exception:
            return False

    try:
        return await loop.run_in_executor(None, _lookup)
    except Exception:
        return False


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ttl = int(parameters.get("ttl", 60))
    record_name = parameters.get("record_name", "")
    new_value = parameters.get("new_value", "")
    creds = getattr(connector, "credentials", {})

    if not creds or not record_name or not new_value:
        return {
            "action": "wait_dns_propagation",
            "ttl_seconds": ttl,
            "propagated": True,
            "mock": True,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    max_wait = min(ttl, 300)
    interval = 10
    deadline = asyncio.get_event_loop().time() + max_wait

    while asyncio.get_event_loop().time() < deadline:
        results = await asyncio.gather(*[
            _dns_query(record_name, new_value, resolver)
            for resolver in _CHECK_RESOLVERS
        ])
        if all(results):
            return {
                "action": "wait_dns_propagation",
                "ttl_seconds": ttl,
                "propagated": True,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        await asyncio.sleep(interval)

    return {
        "action": "wait_dns_propagation",
        "ttl_seconds": ttl,
        "propagated": False,
        "note": f"Propagation not confirmed within {max_wait}s — may still be in progress",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "propagation wait has no rollback"}
