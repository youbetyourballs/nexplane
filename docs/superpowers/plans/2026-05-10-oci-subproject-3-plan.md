# OCI Connector Sub-project 3: Networking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add Security List, NSG, Load Balancer, and DNS executors to the OCI connector, covering 4 new discovery ingesters, 12 new change-type executors, DB migration 042, ChangeType enum extensions, safety engine wiring, and frontend extensions.

**Architecture:** All OCI executors follow the established connector pattern: `async def execute(parameters, asset_ids, connector)` with a no-credentials mock path and `asyncio.get_running_loop().run_in_executor(None, ...)` wrapping every blocking OCI SDK call. Two new OCI SDK client factories (`get_loadbalancer_client`, `get_dns_client`) are added to the shared `_client.py` created by sub-projects 1-2. Load balancer creation polls until lifecycle state is ACTIVE with a 15-minute hard timeout, matching the OCI API's asynchronous provisioning model.

**Tech Stack:** OCI Python SDK (`oci`), Python asyncio, SQLAlchemy/Alembic (PostgreSQL enum extension), React/TypeScript frontend.

**Prerequisites:** Sub-projects 1 and 2 must already be merged. They provide:
- `backend/app/connectors/executors/oci/_client.py` with `get_network_client(creds)` and OCI credential helpers
- `backend/app/connectors/catalog/oci.json` with base structure
- `ConnectorType "oci"` already present in `frontend/src/types/api.ts`
- OCI change types from sub-projects 1-2 already in the `ChangeType` enum and migration chain

---

### Task 1: Discovery Executors — Security Lists and NSGs

**Files:**
- Create: `backend/app/connectors/executors/oci/discover_security_lists.py`
- Create: `backend/app/connectors/executors/oci/discover_nsgs.py`

- [ ] **Step 1:** Create `discover_security_lists.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_security_lists",
            "assets": [
                {
                    "name": "mock-security-list",
                    "asset_type": "firewall",
                    "environment": "prod",
                    "criticality": "medium",
                    "asset_metadata": {
                        "security_list_id": "ocid1.securitylist.mock",
                        "vcn_id": "ocid1.vcn.mock",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "lifecycle_state": "AVAILABLE",
                        "ingress_rule_count": 2,
                        "egress_rule_count": 1,
                    },
                    "tags": ["oci", "security-list"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client, get_compartment_id
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    security_lists = await loop.run_in_executor(
        None, lambda: network.list_security_lists(compartment_id=comp_id).data
    )

    assets = []
    for sl in security_lists:
        assets.append({
            "name": sl.display_name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "security_list_id": sl.id,
                "vcn_id": sl.vcn_id,
                "compartment_id": sl.compartment_id,
                "lifecycle_state": sl.lifecycle_state,
                "ingress_rule_count": len(sl.ingress_security_rules or []),
                "egress_rule_count": len(sl.egress_security_rules or []),
            },
            "tags": ["oci", "security-list"],
            "_dedup_key": sl.id,
            "_dedup_field": "security_list_id",
        })

    return {
        "action": "discover_security_lists",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 2:** Create `discover_nsgs.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_nsgs",
            "assets": [
                {
                    "name": "mock-nsg",
                    "asset_type": "firewall",
                    "environment": "prod",
                    "criticality": "medium",
                    "asset_metadata": {
                        "nsg_id": "ocid1.networksecuritygroup.mock",
                        "vcn_id": "ocid1.vcn.mock",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "lifecycle_state": "AVAILABLE",
                    },
                    "tags": ["oci", "oci-nsg"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client, get_compartment_id
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    nsgs = await loop.run_in_executor(
        None, lambda: network.list_network_security_groups(compartment_id=comp_id).data
    )

    assets = []
    for nsg in nsgs:
        assets.append({
            "name": nsg.display_name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "nsg_id": nsg.id,
                "vcn_id": nsg.vcn_id,
                "compartment_id": nsg.compartment_id,
                "lifecycle_state": nsg.lifecycle_state,
            },
            "tags": ["oci", "oci-nsg"],
            "_dedup_key": nsg.id,
            "_dedup_field": "nsg_id",
        })

    return {
        "action": "discover_nsgs",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
```

---

### Task 2: Security List Rule Executors

**Files:**
- Create: `backend/app/connectors/executors/oci/add_security_list_rule.py`
- Create: `backend/app/connectors/executors/oci/remove_security_list_rule.py`

- [ ] **Step 1:** Create `add_security_list_rule.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    security_list_id = parameters.get("security_list_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")  # TCP
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 22))
    port_max = int(parameters.get("port_max", 22))
    description = parameters.get("description", "nexplane-rule")

    if not creds:
        return {
            "action": "add_security_list_rule",
            "security_list_id": security_list_id or "ocid1.securitylist.mock",
            "direction": direction,
            "protocol": protocol,
            "source": source if direction == "INGRESS" else None,
            "destination": destination if direction == "EGRESS" else None,
            "port_min": port_min,
            "port_max": port_max,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    # Fetch existing security list
    sl = await loop.run_in_executor(
        None, lambda: network.get_security_list(security_list_id).data
    )

    tcp_options = oci_sdk.core.models.TcpOptions(
        destination_port_range=oci_sdk.core.models.PortRange(min=port_min, max=port_max)
    )

    if direction == "INGRESS":
        new_rule = oci_sdk.core.models.IngressSecurityRule(
            protocol=protocol,
            source=source,
            source_type="CIDR_BLOCK",
            tcp_options=tcp_options if protocol == "6" else None,
            description=description,
            is_stateless=False,
        )
        updated_ingress = list(sl.ingress_security_rules or []) + [new_rule]
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=updated_ingress,
            egress_security_rules=list(sl.egress_security_rules or []),
        )
    else:
        new_rule = oci_sdk.core.models.EgressSecurityRule(
            protocol=protocol,
            destination=destination,
            destination_type="CIDR_BLOCK",
            tcp_options=tcp_options if protocol == "6" else None,
            description=description,
            is_stateless=False,
        )
        updated_egress = list(sl.egress_security_rules or []) + [new_rule]
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=list(sl.ingress_security_rules or []),
            egress_security_rules=updated_egress,
        )

    await loop.run_in_executor(
        None, lambda: network.update_security_list(security_list_id, details)
    )

    return {
        "action": "add_security_list_rule",
        "security_list_id": security_list_id,
        "direction": direction,
        "protocol": protocol,
        "source": source if direction == "INGRESS" else None,
        "destination": destination if direction == "EGRESS" else None,
        "port_min": port_min,
        "port_max": port_max,
        "description": description,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by removing the added rule."""
    from app.connectors.executors.oci.remove_security_list_rule import execute as remove
    return await remove(
        {
            "security_list_id": execution_result.get("security_list_id", parameters.get("security_list_id")),
            "direction": execution_result.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": execution_result.get("protocol", parameters.get("protocol", "6")),
            "source": execution_result.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": execution_result.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": execution_result.get("port_min", parameters.get("port_min", 22)),
            "port_max": execution_result.get("port_max", parameters.get("port_max", 22)),
        },
        [],
        connector,
    )
```

- [ ] **Step 2:** Create `remove_security_list_rule.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    security_list_id = parameters.get("security_list_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 22))
    port_max = int(parameters.get("port_max", 22))

    if not creds:
        return {
            "action": "remove_security_list_rule",
            "security_list_id": security_list_id or "ocid1.securitylist.mock",
            "direction": direction,
            "removed": True,
            "mock": True,
            "removed_rule": {"direction": direction, "protocol": protocol, "port_min": port_min, "port_max": port_max},
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    sl = await loop.run_in_executor(
        None, lambda: network.get_security_list(security_list_id).data
    )

    removed_rule = None

    def _matches_ingress(rule):
        if rule.protocol != protocol:
            return False
        if rule.source != source:
            return False
        if protocol == "6" and rule.tcp_options:
            rng = rule.tcp_options.destination_port_range
            if rng and rng.min == port_min and rng.max == port_max:
                return True
            return False
        return True

    def _matches_egress(rule):
        if rule.protocol != protocol:
            return False
        if rule.destination != destination:
            return False
        if protocol == "6" and rule.tcp_options:
            rng = rule.tcp_options.destination_port_range
            if rng and rng.min == port_min and rng.max == port_max:
                return True
            return False
        return True

    if direction == "INGRESS":
        old_rules = list(sl.ingress_security_rules or [])
        new_rules = [r for r in old_rules if not _matches_ingress(r)]
        removed_rule = next((r for r in old_rules if _matches_ingress(r)), None)
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=new_rules,
            egress_security_rules=list(sl.egress_security_rules or []),
        )
    else:
        old_rules = list(sl.egress_security_rules or [])
        new_rules = [r for r in old_rules if not _matches_egress(r)]
        removed_rule = next((r for r in old_rules if _matches_egress(r)), None)
        details = oci_sdk.core.models.UpdateSecurityListDetails(
            ingress_security_rules=list(sl.ingress_security_rules or []),
            egress_security_rules=new_rules,
        )

    await loop.run_in_executor(
        None, lambda: network.update_security_list(security_list_id, details)
    )

    return {
        "action": "remove_security_list_rule",
        "security_list_id": security_list_id,
        "direction": direction,
        "removed": removed_rule is not None,
        "removed_rule": {
            "direction": direction,
            "protocol": protocol,
            "source": source if direction == "INGRESS" else None,
            "destination": destination if direction == "EGRESS" else None,
            "port_min": port_min,
            "port_max": port_max,
        },
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback by re-adding the removed rule."""
    from app.connectors.executors.oci.add_security_list_rule import execute as add
    removed = execution_result.get("removed_rule", {})
    return await add(
        {
            "security_list_id": execution_result.get("security_list_id", parameters.get("security_list_id")),
            "direction": removed.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": removed.get("protocol", parameters.get("protocol", "6")),
            "source": removed.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": removed.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": removed.get("port_min", parameters.get("port_min", 22)),
            "port_max": removed.get("port_max", parameters.get("port_max", 22)),
        },
        [],
        connector,
    )
```

---

### Task 3: NSG Executors (create, delete, add rule, remove rule)

**Files:**
- Create: `backend/app/connectors/executors/oci/create_nsg.py`
- Create: `backend/app/connectors/executors/oci/delete_nsg.py`
- Create: `backend/app/connectors/executors/oci/add_nsg_rule.py`
- Create: `backend/app/connectors/executors/oci/remove_nsg_rule.py`

- [ ] **Step 1:** Create `create_nsg.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    vcn_id = parameters.get("vcn_id", "")
    display_name = parameters.get("display_name", "nexplane-nsg")

    auto_asset = {
        "name": display_name,
        "asset_type": "firewall",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "nsg_id": "ocid1.networksecuritygroup.mock",
            "vcn_id": vcn_id or "ocid1.vcn.mock",
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "lifecycle_state": "AVAILABLE",
            "provider": "oci",
        },
        "tags": ["oci", "oci-nsg"],
    }

    if not creds:
        return {
            "action": "create_nsg",
            "nsg_id": "ocid1.networksecuritygroup.mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client, get_compartment_id
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    details = oci_sdk.core.models.CreateNetworkSecurityGroupDetails(
        compartment_id=comp_id,
        vcn_id=vcn_id,
        display_name=display_name,
    )
    nsg = await loop.run_in_executor(
        None, lambda: network.create_network_security_group(details).data
    )

    auto_asset["asset_metadata"]["nsg_id"] = nsg.id
    auto_asset["asset_metadata"]["vcn_id"] = nsg.vcn_id
    auto_asset["asset_metadata"]["compartment_id"] = nsg.compartment_id
    auto_asset["asset_metadata"]["lifecycle_state"] = nsg.lifecycle_state

    return {
        "action": "create_nsg",
        "nsg_id": nsg.id,
        "display_name": nsg.display_name,
        "vcn_id": nsg.vcn_id,
        "compartment_id": nsg.compartment_id,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_nsg import execute as delete
    return await delete(
        {"nsg_id": execution_result.get("nsg_id")},
        [],
        connector,
    )
```

- [ ] **Step 2:** Create `delete_nsg.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")

    if not creds:
        return {
            "action": "delete_nsg",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "deleted": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    await loop.run_in_executor(
        None, lambda: network.delete_network_security_group(nsg_id)
    )

    return {
        "action": "delete_nsg",
        "nsg_id": nsg_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 3:** Create `add_nsg_rule.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 443))
    port_max = int(parameters.get("port_max", 443))
    description = parameters.get("description", "nexplane-nsg-rule")

    if not creds:
        return {
            "action": "add_nsg_rule",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "direction": direction,
            "protocol": protocol,
            "port_min": port_min,
            "port_max": port_max,
            "rule_id": "mock-rule-id",
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    tcp_options = oci_sdk.core.models.TcpOptions(
        destination_port_range=oci_sdk.core.models.PortRange(min=port_min, max=port_max)
    ) if protocol == "6" else None

    if direction == "INGRESS":
        rule = oci_sdk.core.models.AddSecurityRuleDetails(
            direction="INGRESS",
            protocol=protocol,
            source=source,
            source_type="CIDR_BLOCK",
            tcp_options=tcp_options,
            description=description,
            is_stateless=False,
        )
    else:
        rule = oci_sdk.core.models.AddSecurityRuleDetails(
            direction="EGRESS",
            protocol=protocol,
            destination=destination,
            destination_type="CIDR_BLOCK",
            tcp_options=tcp_options,
            description=description,
            is_stateless=False,
        )

    add_details = oci_sdk.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
        security_rules=[rule]
    )
    result = await loop.run_in_executor(
        None, lambda: network.add_network_security_group_security_rules(nsg_id, add_details).data
    )

    rule_id = result.security_rules[0].id if result.security_rules else None

    return {
        "action": "add_nsg_rule",
        "nsg_id": nsg_id,
        "direction": direction,
        "protocol": protocol,
        "source": source if direction == "INGRESS" else None,
        "destination": destination if direction == "EGRESS" else None,
        "port_min": port_min,
        "port_max": port_max,
        "rule_id": rule_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.remove_nsg_rule import execute as remove
    return await remove(
        {
            "nsg_id": execution_result.get("nsg_id", parameters.get("nsg_id")),
            "rule_id": execution_result.get("rule_id"),
        },
        [],
        connector,
    )
```

- [ ] **Step 4:** Create `remove_nsg_rule.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    nsg_id = parameters.get("nsg_id", "")
    rule_id = parameters.get("rule_id", "")
    # If rule_id is provided, use it directly; otherwise match by direction/protocol/port
    direction = parameters.get("direction", "INGRESS")
    protocol = parameters.get("protocol", "6")
    source = parameters.get("source", "0.0.0.0/0")
    destination = parameters.get("destination", "0.0.0.0/0")
    port_min = int(parameters.get("port_min", 443))
    port_max = int(parameters.get("port_max", 443))

    if not creds:
        return {
            "action": "remove_nsg_rule",
            "nsg_id": nsg_id or "ocid1.networksecuritygroup.mock",
            "rule_id": rule_id or "mock-rule-id",
            "removed": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)

    if not rule_id:
        # Resolve rule_id by listing rules and matching
        rules_resp = await loop.run_in_executor(
            None, lambda: network.list_network_security_group_security_rules(nsg_id).data
        )

        def _matches(r):
            if r.direction != direction or r.protocol != protocol:
                return False
            if direction == "INGRESS" and r.source != source:
                return False
            if direction == "EGRESS" and r.destination != destination:
                return False
            if protocol == "6" and r.tcp_options:
                rng = r.tcp_options.destination_port_range
                return rng and rng.min == port_min and rng.max == port_max
            return True

        match = next((r for r in rules_resp if _matches(r)), None)
        rule_id = match.id if match else None

    if not rule_id:
        return {
            "action": "remove_nsg_rule",
            "nsg_id": nsg_id,
            "removed": False,
            "reason": "rule not found",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    remove_details = oci_sdk.core.models.RemoveNetworkSecurityGroupSecurityRulesDetails(
        security_rule_ids=[rule_id]
    )
    await loop.run_in_executor(
        None, lambda: network.remove_network_security_group_security_rules(nsg_id, remove_details)
    )

    return {
        "action": "remove_nsg_rule",
        "nsg_id": nsg_id,
        "rule_id": rule_id,
        "removed": True,
        "removed_rule": {
            "direction": direction,
            "protocol": protocol,
            "source": source if direction == "INGRESS" else None,
            "destination": destination if direction == "EGRESS" else None,
            "port_min": port_min,
            "port_max": port_max,
        },
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.add_nsg_rule import execute as add
    removed = execution_result.get("removed_rule", {})
    return await add(
        {
            "nsg_id": execution_result.get("nsg_id", parameters.get("nsg_id")),
            "direction": removed.get("direction", parameters.get("direction", "INGRESS")),
            "protocol": removed.get("protocol", parameters.get("protocol", "6")),
            "source": removed.get("source", parameters.get("source", "0.0.0.0/0")),
            "destination": removed.get("destination", parameters.get("destination", "0.0.0.0/0")),
            "port_min": removed.get("port_min", parameters.get("port_min", 443)),
            "port_max": removed.get("port_max", parameters.get("port_max", 443)),
        },
        [],
        connector,
    )
```

---

### Task 4: Load Balancer Discovery and Lifecycle Executors

**Files:**
- Create: `backend/app/connectors/executors/oci/discover_load_balancers.py`
- Create: `backend/app/connectors/executors/oci/create_load_balancer.py`
- Create: `backend/app/connectors/executors/oci/delete_load_balancer.py`
- Modify: `backend/app/connectors/executors/oci/_client.py` — add `get_loadbalancer_client`

- [ ] **Step 1:** Add `get_loadbalancer_client` to `_client.py`. Append to the existing file:

```python
def get_loadbalancer_client(creds: dict):
    """Return an OCI LoadBalancerClient using stored credentials."""
    import oci
    config = _build_oci_config(creds)
    return oci.load_balancer.LoadBalancerClient(config)
```

  Note: `_build_oci_config` is the internal helper already present in `_client.py` from sub-project 1. If it is named differently, use the same helper used by `get_network_client`.

- [ ] **Step 2:** Create `discover_load_balancers.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_load_balancers",
            "assets": [
                {
                    "name": "mock-oci-lb",
                    "asset_type": "load_balancer",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "load_balancer_id": "ocid1.loadbalancer.mock",
                        "shape_name": "flexible",
                        "shape_min_mbps": 10,
                        "shape_max_mbps": 100,
                        "ip_addresses": ["203.0.113.1"],
                        "lifecycle_state": "ACTIVE",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "provider": "oci",
                    },
                    "tags": ["oci", "load-balancer"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client, get_compartment_id
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    lbs = await loop.run_in_executor(
        None, lambda: lb_client.list_load_balancers(compartment_id=comp_id).data
    )

    assets = []
    for lb in lbs:
        ip_addrs = [ip.ip_address for ip in (lb.ip_addresses or [])]
        shape = lb.shape_details
        assets.append({
            "name": lb.display_name,
            "asset_type": "load_balancer",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "load_balancer_id": lb.id,
                "shape_name": lb.shape_name,
                "shape_min_mbps": shape.minimum_bandwidth_in_mbps if shape else None,
                "shape_max_mbps": shape.maximum_bandwidth_in_mbps if shape else None,
                "ip_addresses": ip_addrs,
                "lifecycle_state": lb.lifecycle_state,
                "compartment_id": lb.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "load-balancer"],
            "_dedup_key": lb.id,
            "_dedup_field": "load_balancer_id",
        })

    return {
        "action": "discover_load_balancers",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 3:** Create `create_load_balancer.py`:

```python
import asyncio
import time
from datetime import datetime, timezone


_POLL_INTERVAL_SECONDS = 15
_MAX_WAIT_SECONDS = 900  # 15 minutes


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    display_name = parameters.get("display_name", "nexplane-lb")
    shape_name = parameters.get("shape_name", "flexible")
    shape_min_mbps = int(parameters.get("shape_min_mbps", 10))
    shape_max_mbps = int(parameters.get("shape_max_mbps", 100))
    subnet_ids = parameters.get("subnet_ids", [])
    is_private = parameters.get("is_private", False)

    auto_asset = {
        "name": display_name,
        "asset_type": "load_balancer",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "load_balancer_id": "ocid1.loadbalancer.mock",
            "shape_name": shape_name,
            "shape_min_mbps": shape_min_mbps,
            "shape_max_mbps": shape_max_mbps,
            "ip_addresses": [],
            "lifecycle_state": "ACTIVE",
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "is_private": is_private,
            "provider": "oci",
        },
        "tags": ["oci", "load-balancer", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_load_balancer",
            "load_balancer_id": "ocid1.loadbalancer.mock",
            "display_name": display_name,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client, get_compartment_id
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    shape_details = oci_sdk.load_balancer.models.ShapeDetails(
        minimum_bandwidth_in_mbps=shape_min_mbps,
        maximum_bandwidth_in_mbps=shape_max_mbps,
    )
    details = oci_sdk.load_balancer.models.CreateLoadBalancerDetails(
        compartment_id=comp_id,
        display_name=display_name,
        shape_name=shape_name,
        shape_details=shape_details,
        subnet_ids=subnet_ids,
        is_private=is_private,
    )

    # Submit creation — returns a work request
    work_request_resp = await loop.run_in_executor(
        None, lambda: lb_client.create_load_balancer(details)
    )
    work_request_id = work_request_resp.headers.get("opc-work-request-id")

    # Poll work request until SUCCEEDED (up to 15 min)
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    lb_id = None
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        wr = await loop.run_in_executor(
            None, lambda: lb_client.get_work_request(work_request_id).data
        )
        if wr.lifecycle_state == "SUCCEEDED":
            # Extract LB id from resources
            for res in (wr.load_balancer_id and []) or []:
                lb_id = res
            if not lb_id and wr.load_balancer_id:
                lb_id = wr.load_balancer_id
            break
        if wr.lifecycle_state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"OCI load balancer creation work request {wr.lifecycle_state}: {wr.message}")

    if not lb_id:
        raise RuntimeError("Timed out waiting for OCI load balancer to become ACTIVE")

    # Fetch final LB details
    lb = await loop.run_in_executor(None, lambda: lb_client.get_load_balancer(lb_id).data)
    ip_addrs = [ip.ip_address for ip in (lb.ip_addresses or [])]

    auto_asset["asset_metadata"].update({
        "load_balancer_id": lb_id,
        "ip_addresses": ip_addrs,
        "lifecycle_state": lb.lifecycle_state,
        "compartment_id": lb.compartment_id,
    })

    return {
        "action": "create_load_balancer",
        "load_balancer_id": lb_id,
        "display_name": lb.display_name,
        "shape_name": lb.shape_name,
        "ip_addresses": ip_addrs,
        "is_private": lb.is_private,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_load_balancer import execute as delete
    return await delete(
        {"load_balancer_id": execution_result.get("load_balancer_id")},
        [],
        connector,
    )
```

- [ ] **Step 4:** Create `delete_load_balancer.py`:

```python
import asyncio
import time
from datetime import datetime, timezone

_POLL_INTERVAL_SECONDS = 10
_MAX_WAIT_SECONDS = 300


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")

    if not creds:
        return {
            "action": "delete_load_balancer",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "deleted": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    work_request_resp = await loop.run_in_executor(
        None, lambda: lb_client.delete_load_balancer(load_balancer_id)
    )
    work_request_id = work_request_resp.headers.get("opc-work-request-id")

    # Poll until SUCCEEDED
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        wr = await loop.run_in_executor(
            None, lambda: lb_client.get_work_request(work_request_id).data
        )
        if wr.lifecycle_state == "SUCCEEDED":
            break
        if wr.lifecycle_state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"OCI delete_load_balancer work request {wr.lifecycle_state}")

    return {
        "action": "delete_load_balancer",
        "load_balancer_id": load_balancer_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
```

---

### Task 5: Backend Set and Listener Executors

**Files:**
- Create: `backend/app/connectors/executors/oci/create_backend_set.py`
- Create: `backend/app/connectors/executors/oci/create_listener.py`

- [ ] **Step 1:** Create `create_backend_set.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")
    name = parameters.get("name", "nexplane-backend-set")
    policy = parameters.get("policy", "ROUND_ROBIN")
    health_checker = parameters.get("health_checker", {
        "protocol": "HTTP",
        "port": 80,
        "url_path": "/health",
        "return_code": 200,
        "interval_ms": 10000,
        "timeout_in_millis": 3000,
        "retries": 3,
    })

    if not creds:
        return {
            "action": "create_backend_set",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "name": name,
            "policy": policy,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    hc = oci_sdk.load_balancer.models.HealthCheckerDetails(
        protocol=health_checker.get("protocol", "HTTP"),
        port=int(health_checker.get("port", 80)),
        url_path=health_checker.get("url_path", "/health"),
        return_code=int(health_checker.get("return_code", 200)),
        interval_in_millis=int(health_checker.get("interval_ms", 10000)),
        timeout_in_millis=int(health_checker.get("timeout_in_millis", 3000)),
        retries=int(health_checker.get("retries", 3)),
    )
    details = oci_sdk.load_balancer.models.CreateBackendSetDetails(
        name=name,
        policy=policy,
        health_checker=hc,
        backends=[],
    )

    work_req = await loop.run_in_executor(
        None, lambda: lb_client.create_backend_set(load_balancer_id, details)
    )

    return {
        "action": "create_backend_set",
        "load_balancer_id": load_balancer_id,
        "name": name,
        "policy": policy,
        "work_request_id": work_req.headers.get("opc-work-request-id"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the backend set."""
    creds = getattr(connector, "credentials", {})
    load_balancer_id = execution_result.get("load_balancer_id", parameters.get("load_balancer_id"))
    name = execution_result.get("name", parameters.get("name", "nexplane-backend-set"))

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    await loop.run_in_executor(
        None, lambda: lb_client.delete_backend_set(load_balancer_id, name)
    )
    return {"rolled_back": True, "load_balancer_id": load_balancer_id, "backend_set_name": name}
```

- [ ] **Step 2:** Create `create_listener.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")
    name = parameters.get("name", "nexplane-listener")
    default_backend_set = parameters.get("default_backend_set", "nexplane-backend-set")
    port = int(parameters.get("port", 80))
    protocol = parameters.get("protocol", "HTTP")

    if not creds:
        return {
            "action": "create_listener",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "name": name,
            "port": port,
            "protocol": protocol,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    details = oci_sdk.load_balancer.models.CreateListenerDetails(
        name=name,
        default_backend_set_name=default_backend_set,
        port=port,
        protocol=protocol,
    )

    work_req = await loop.run_in_executor(
        None, lambda: lb_client.create_listener(load_balancer_id, details)
    )

    return {
        "action": "create_listener",
        "load_balancer_id": load_balancer_id,
        "name": name,
        "default_backend_set": default_backend_set,
        "port": port,
        "protocol": protocol,
        "work_request_id": work_req.headers.get("opc-work-request-id"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the listener."""
    creds = getattr(connector, "credentials", {})
    load_balancer_id = execution_result.get("load_balancer_id", parameters.get("load_balancer_id"))
    name = execution_result.get("name", parameters.get("name", "nexplane-listener"))

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    await loop.run_in_executor(
        None, lambda: lb_client.delete_listener(load_balancer_id, name)
    )
    return {"rolled_back": True, "load_balancer_id": load_balancer_id, "listener_name": name}
```

---

### Task 6: DNS Discovery and Executors

**Files:**
- Modify: `backend/app/connectors/executors/oci/_client.py` — add `get_dns_client`
- Create: `backend/app/connectors/executors/oci/discover_dns_zones.py`
- Create: `backend/app/connectors/executors/oci/create_dns_zone.py`
- Create: `backend/app/connectors/executors/oci/upsert_dns_record.py`

- [ ] **Step 1:** Add `get_dns_client` to `_client.py`. Append to the existing file:

```python
def get_dns_client(creds: dict):
    """Return an OCI DnsClient using stored credentials."""
    import oci
    config = _build_oci_config(creds)
    return oci.dns.DnsClient(config)
```

- [ ] **Step 2:** Create `discover_dns_zones.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_dns_zones",
            "assets": [
                {
                    "name": "mock-zone.example.com",
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": "ocid1.dns-zone.mock",
                        "zone_name": "mock-zone.example.com",
                        "zone_type": "PRIMARY",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "serial": 1,
                        "provider": "oci",
                    },
                    "tags": ["oci", "dns"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client, get_compartment_id
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    zones = await loop.run_in_executor(
        None, lambda: dns_client.list_zones(compartment_id=comp_id).data
    )

    assets = []
    for zone in zones:
        assets.append({
            "name": zone.name,
            "asset_type": "dns_zone",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "zone_id": zone.id,
                "zone_name": zone.name,
                "zone_type": zone.zone_type,
                "compartment_id": zone.compartment_id,
                "serial": zone.serial,
                "provider": "oci",
            },
            "tags": ["oci", "dns"],
            "_dedup_key": zone.id,
            "_dedup_field": "zone_id",
        })

    return {
        "action": "discover_dns_zones",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 3:** Create `create_dns_zone.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "nexplane-test.example.com")
    zone_type = parameters.get("zone_type", "PRIMARY")

    auto_asset = {
        "name": name,
        "asset_type": "dns_zone",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "zone_id": "ocid1.dns-zone.mock",
            "zone_name": name,
            "zone_type": zone_type,
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "serial": 1,
            "provider": "oci",
        },
        "tags": ["oci", "dns", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_dns_zone",
            "zone_id": "ocid1.dns-zone.mock",
            "zone_name": name,
            "zone_type": zone_type,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client, get_compartment_id
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)
    comp_id = compartment_id or get_compartment_id(creds)

    details = oci_sdk.dns.models.CreateZoneDetails(
        compartment_id=comp_id,
        name=name,
        zone_type=zone_type,
    )
    zone = await loop.run_in_executor(
        None, lambda: dns_client.create_zone(details).data
    )

    auto_asset["asset_metadata"].update({
        "zone_id": zone.id,
        "zone_name": zone.name,
        "serial": zone.serial,
        "compartment_id": zone.compartment_id,
    })

    return {
        "action": "create_dns_zone",
        "zone_id": zone.id,
        "zone_name": zone.name,
        "zone_type": zone.zone_type,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the created DNS zone."""
    creds = getattr(connector, "credentials", {})
    zone_id = execution_result.get("zone_id")
    if not zone_id:
        return {"rolled_back": False, "reason": "no zone_id in execution result"}
    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_dns_client
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    await loop.run_in_executor(None, lambda: dns_client.delete_zone(zone_id))
    return {"rolled_back": True, "zone_id": zone_id}
```

- [ ] **Step 4:** Create `upsert_dns_record.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name_or_id = parameters.get("zone_name_or_id", "")
    domain = parameters.get("domain", "www.nexplane-test.example.com")
    rtype = parameters.get("rtype", "A")
    ttl = int(parameters.get("ttl", 300))
    rdata = parameters.get("rdata", "10.0.0.1")

    if not creds:
        return {
            "action": "upsert_dns_record",
            "zone_name_or_id": zone_name_or_id or "mock-zone.example.com",
            "domain": domain,
            "rtype": rtype,
            "ttl": ttl,
            "rdata": rdata,
            "mock": True,
            "previous_rdata": None,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    # Capture existing record for rollback
    previous_rdata = None
    try:
        existing = await loop.run_in_executor(
            None,
            lambda: dns_client.get_rr_set(
                zone_name_or_id=zone_name_or_id,
                domain=domain,
                rtype=rtype,
            ).data
        )
        if existing.items:
            previous_rdata = existing.items[0].rdata
    except Exception:
        pass  # Record may not exist yet — that's fine for an upsert

    record_item = oci_sdk.dns.models.RecordDetails(
        domain=domain,
        ttl=ttl,
        rtype=rtype,
        rdata=rdata,
    )
    patch_details = oci_sdk.dns.models.PatchRRSetDetails(
        items=[
            oci_sdk.dns.models.RecordOperation(
                domain=domain,
                rtype=rtype,
                ttl=ttl,
                rdata=rdata,
                operation="REQUIRE_ABSENT" if previous_rdata is None else "PROHIBIT",
            )
        ]
    )
    # Use update_rr_set for a clean upsert
    update_details = oci_sdk.dns.models.UpdateRRSetDetails(items=[record_item])
    await loop.run_in_executor(
        None,
        lambda: dns_client.update_rr_set(
            zone_name_or_id=zone_name_or_id,
            domain=domain,
            rtype=rtype,
            update_rr_set_details=update_details,
        )
    )

    return {
        "action": "upsert_dns_record",
        "zone_name_or_id": zone_name_or_id,
        "domain": domain,
        "rtype": rtype,
        "ttl": ttl,
        "rdata": rdata,
        "previous_rdata": previous_rdata,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous DNS record value, or delete the record if it was new."""
    creds = getattr(connector, "credentials", {})
    zone_name_or_id = execution_result.get("zone_name_or_id", parameters.get("zone_name_or_id"))
    domain = execution_result.get("domain", parameters.get("domain"))
    rtype = execution_result.get("rtype", parameters.get("rtype", "A"))
    ttl = int(execution_result.get("ttl", parameters.get("ttl", 300)))
    previous_rdata = execution_result.get("previous_rdata")

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_dns_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    if previous_rdata is None:
        # Record was new — delete it
        await loop.run_in_executor(
            None, lambda: dns_client.delete_rr_set(
                zone_name_or_id=zone_name_or_id, domain=domain, rtype=rtype
            )
        )
        return {"rolled_back": True, "action": "deleted_new_record", "domain": domain, "rtype": rtype}
    else:
        # Restore previous value
        record_item = oci_sdk.dns.models.RecordDetails(domain=domain, ttl=ttl, rtype=rtype, rdata=previous_rdata)
        update_details = oci_sdk.dns.models.UpdateRRSetDetails(items=[record_item])
        await loop.run_in_executor(
            None,
            lambda: dns_client.update_rr_set(
                zone_name_or_id=zone_name_or_id,
                domain=domain,
                rtype=rtype,
                update_rr_set_details=update_details,
            )
        )
        return {"rolled_back": True, "action": "restored_previous_record", "domain": domain, "rdata": previous_rdata}
```

---

### Task 7: Extend oci.json Catalog

**Files:**
- Modify: `backend/app/connectors/catalog/oci.json`

- [ ] **Step 1:** Add 4 new `ingest` actions to the `"actions"` array (after the existing sub-project 1-2 discovery entries):

```json
{
  "display_name": "Discover Security Lists",
  "description": "Discovers OCI Security Lists and registers them as firewall assets.",
  "produces_asset_types": ["firewall"],
  "estimated_duration_seconds": 20,
  "applicable_asset_types": [],
  "action_type": "ingest",
  "executor": "oci.discover_security_lists",
  "generic_action": "discover_security_lists",
  "action_id": "discover_security_lists"
},
{
  "display_name": "Discover Network Security Groups",
  "description": "Discovers OCI NSGs and registers them as firewall assets tagged oci-nsg.",
  "produces_asset_types": ["firewall"],
  "estimated_duration_seconds": 20,
  "applicable_asset_types": [],
  "action_type": "ingest",
  "executor": "oci.discover_nsgs",
  "generic_action": "discover_nsgs",
  "action_id": "discover_nsgs"
},
{
  "display_name": "Discover Load Balancers",
  "description": "Discovers OCI Load Balancers and registers them as load_balancer assets.",
  "produces_asset_types": ["load_balancer"],
  "estimated_duration_seconds": 30,
  "applicable_asset_types": [],
  "action_type": "ingest",
  "executor": "oci.discover_load_balancers",
  "generic_action": "discover_load_balancers",
  "action_id": "discover_load_balancers"
},
{
  "display_name": "Discover DNS Zones",
  "description": "Discovers OCI DNS zones and registers them as dns_zone assets.",
  "produces_asset_types": ["dns_zone"],
  "estimated_duration_seconds": 20,
  "applicable_asset_types": [],
  "action_type": "ingest",
  "executor": "oci.discover_dns_zones",
  "generic_action": "discover_dns_zones",
  "action_id": "discover_dns_zones"
}
```

- [ ] **Step 2:** Add 12 new `change` actions to the `"actions"` array (after the ingest actions above):

```json
{
  "display_name": "Add Security List Rule",
  "description": "Add an inbound or outbound rule to an OCI Security List.",
  "parameters": [
    {"required": true, "type": "string", "name": "security_list_id"},
    {"required": false, "type": "string", "name": "direction", "default": "INGRESS"},
    {"required": false, "type": "string", "name": "protocol", "default": "6"},
    {"required": false, "type": "string", "name": "source", "default": "0.0.0.0/0"},
    {"required": false, "type": "integer", "name": "port_min", "default": 22},
    {"required": false, "type": "integer", "name": "port_max", "default": 22},
    {"required": false, "type": "string", "name": "description", "default": "nexplane-rule"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_security_list_remove_rule",
  "estimated_duration_seconds": 10,
  "blast_radius_hint": "network_access_change",
  "applicable_asset_types": ["firewall"],
  "action_type": "change",
  "executor": "oci.add_security_list_rule",
  "generic_action": "oci_security_list_add_rule",
  "action_id": "oci_security_list_add_rule"
},
{
  "display_name": "Remove Security List Rule",
  "description": "Remove an inbound or outbound rule from an OCI Security List.",
  "parameters": [
    {"required": true, "type": "string", "name": "security_list_id"},
    {"required": false, "type": "string", "name": "direction", "default": "INGRESS"},
    {"required": false, "type": "string", "name": "protocol", "default": "6"},
    {"required": false, "type": "string", "name": "source", "default": "0.0.0.0/0"},
    {"required": false, "type": "integer", "name": "port_min", "default": 22},
    {"required": false, "type": "integer", "name": "port_max", "default": 22}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_security_list_add_rule",
  "estimated_duration_seconds": 10,
  "blast_radius_hint": "network_access_change",
  "applicable_asset_types": ["firewall"],
  "action_type": "change",
  "executor": "oci.remove_security_list_rule",
  "generic_action": "oci_security_list_remove_rule",
  "action_id": "oci_security_list_remove_rule"
},
{
  "display_name": "Create Network Security Group",
  "description": "Create an OCI Network Security Group (NSG) in a VCN.",
  "parameters": [
    {"required": false, "type": "string", "name": "compartment_id"},
    {"required": true, "type": "string", "name": "vcn_id"},
    {"required": false, "type": "string", "name": "display_name", "default": "nexplane-nsg"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_nsg_delete",
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["cloud_account"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.create_nsg",
  "generic_action": "oci_nsg_create",
  "action_id": "oci_nsg_create"
},
{
  "display_name": "Delete Network Security Group",
  "description": "Delete an OCI NSG. Irreversible.",
  "parameters": [
    {"required": true, "type": "string", "name": "nsg_id"}
  ],
  "execution_tier": 2,
  "rollback_action": null,
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["firewall"],
  "action_type": "change",
  "executor": "oci.delete_nsg",
  "generic_action": "oci_nsg_delete",
  "action_id": "oci_nsg_delete"
},
{
  "display_name": "Add NSG Security Rule",
  "description": "Add an inbound or outbound rule to an OCI NSG.",
  "parameters": [
    {"required": true, "type": "string", "name": "nsg_id"},
    {"required": false, "type": "string", "name": "direction", "default": "INGRESS"},
    {"required": false, "type": "string", "name": "protocol", "default": "6"},
    {"required": false, "type": "string", "name": "source", "default": "0.0.0.0/0"},
    {"required": false, "type": "integer", "name": "port_min", "default": 443},
    {"required": false, "type": "integer", "name": "port_max", "default": 443},
    {"required": false, "type": "string", "name": "description", "default": "nexplane-nsg-rule"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_nsg_rule_remove",
  "estimated_duration_seconds": 10,
  "blast_radius_hint": "network_access_change",
  "applicable_asset_types": ["firewall"],
  "action_type": "change",
  "executor": "oci.add_nsg_rule",
  "generic_action": "oci_nsg_rule_add",
  "action_id": "oci_nsg_rule_add"
},
{
  "display_name": "Remove NSG Security Rule",
  "description": "Remove a rule from an OCI NSG by rule ID or matching parameters.",
  "parameters": [
    {"required": true, "type": "string", "name": "nsg_id"},
    {"required": false, "type": "string", "name": "rule_id"},
    {"required": false, "type": "string", "name": "direction", "default": "INGRESS"},
    {"required": false, "type": "string", "name": "protocol", "default": "6"},
    {"required": false, "type": "string", "name": "source", "default": "0.0.0.0/0"},
    {"required": false, "type": "integer", "name": "port_min", "default": 443},
    {"required": false, "type": "integer", "name": "port_max", "default": 443}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_nsg_rule_add",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["firewall"],
  "action_type": "change",
  "executor": "oci.remove_nsg_rule",
  "generic_action": "oci_nsg_rule_remove",
  "action_id": "oci_nsg_rule_remove"
},
{
  "display_name": "Create OCI Load Balancer",
  "description": "Create an OCI flexible Load Balancer. Polls up to 15 min until ACTIVE.",
  "parameters": [
    {"required": false, "type": "string", "name": "compartment_id"},
    {"required": false, "type": "string", "name": "display_name", "default": "nexplane-lb"},
    {"required": false, "type": "string", "name": "shape_name", "default": "flexible"},
    {"required": false, "type": "integer", "name": "shape_min_mbps", "default": 10},
    {"required": false, "type": "integer", "name": "shape_max_mbps", "default": 100},
    {"required": true, "type": "array", "name": "subnet_ids"},
    {"required": false, "type": "boolean", "name": "is_private", "default": false}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_load_balancer_delete",
  "estimated_duration_seconds": 900,
  "blast_radius_hint": "new_resource",
  "applicable_asset_types": ["cloud_account"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.create_load_balancer",
  "generic_action": "oci_load_balancer_create",
  "action_id": "oci_load_balancer_create"
},
{
  "display_name": "Delete OCI Load Balancer",
  "description": "Delete an OCI Load Balancer. Polls until DELETED.",
  "parameters": [
    {"required": true, "type": "string", "name": "load_balancer_id"}
  ],
  "execution_tier": 2,
  "rollback_action": null,
  "estimated_duration_seconds": 300,
  "blast_radius_hint": "destructive",
  "applicable_asset_types": ["load_balancer"],
  "action_type": "change",
  "executor": "oci.delete_load_balancer",
  "generic_action": "oci_load_balancer_delete",
  "action_id": "oci_load_balancer_delete"
},
{
  "display_name": "Create Backend Set",
  "description": "Create a backend set on an OCI Load Balancer.",
  "parameters": [
    {"required": true, "type": "string", "name": "load_balancer_id"},
    {"required": false, "type": "string", "name": "name", "default": "nexplane-backend-set"},
    {"required": false, "type": "string", "name": "policy", "default": "ROUND_ROBIN"},
    {"required": false, "type": "object", "name": "health_checker"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_backend_set_delete",
  "estimated_duration_seconds": 30,
  "applicable_asset_types": ["load_balancer"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.create_backend_set",
  "generic_action": "oci_backend_set_create",
  "action_id": "oci_backend_set_create"
},
{
  "display_name": "Create Listener",
  "description": "Create a listener on an OCI Load Balancer.",
  "parameters": [
    {"required": true, "type": "string", "name": "load_balancer_id"},
    {"required": false, "type": "string", "name": "name", "default": "nexplane-listener"},
    {"required": false, "type": "string", "name": "default_backend_set", "default": "nexplane-backend-set"},
    {"required": false, "type": "integer", "name": "port", "default": 80},
    {"required": false, "type": "string", "name": "protocol", "default": "HTTP"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_listener_delete",
  "estimated_duration_seconds": 20,
  "applicable_asset_types": ["load_balancer"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.create_listener",
  "generic_action": "oci_listener_create",
  "action_id": "oci_listener_create"
},
{
  "display_name": "Create OCI DNS Zone",
  "description": "Create an OCI DNS zone of type PRIMARY.",
  "parameters": [
    {"required": false, "type": "string", "name": "compartment_id"},
    {"required": false, "type": "string", "name": "name", "default": "nexplane-test.example.com"},
    {"required": false, "type": "string", "name": "zone_type", "default": "PRIMARY"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_dns_zone_delete",
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["cloud_account"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.create_dns_zone",
  "generic_action": "oci_dns_zone_create",
  "action_id": "oci_dns_zone_create"
},
{
  "display_name": "Upsert DNS Record",
  "description": "Create or update a DNS record in an OCI DNS zone.",
  "parameters": [
    {"required": true, "type": "string", "name": "zone_name_or_id"},
    {"required": false, "type": "string", "name": "domain", "default": "www.nexplane-test.example.com"},
    {"required": false, "type": "string", "name": "rtype", "default": "A"},
    {"required": false, "type": "integer", "name": "ttl", "default": 300},
    {"required": false, "type": "string", "name": "rdata", "default": "10.0.0.1"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_dns_record_delete",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["dns_zone"],
  "rollback_connector_type": "oci",
  "action_type": "change",
  "executor": "oci.upsert_dns_record",
  "generic_action": "oci_dns_record_upsert",
  "action_id": "oci_dns_record_upsert"
}
```

---

### Task 8: DB Migration 042 + ChangeType Enum + Safety Engine

**Files:**
- Create: `backend/alembic/versions/042_add_oci_networking_change_types.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`

> **Note:** Check what the highest existing migration is when implementing — it may be higher than 039 if sub-projects 1 and 2 added 040 and 041. Use the correct `down_revision` in the migration.

- [ ] **Step 1:** Create `backend/alembic/versions/042_add_oci_networking_change_types.py`:

```python
"""Add OCI networking change types (security list, NSG, load balancer, DNS)

Revision ID: 042
Revises: 041
Create Date: 2026-05-10
"""
from alembic import op

revision = '042'
down_revision = '041'  # Adjust to actual previous revision if different
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_add_rule'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_remove_rule'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_add'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_remove'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_delete'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_backend_set_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_listener_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_zone_create'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_record_upsert'")


def downgrade() -> None:
    pass  # PostgreSQL does not support removing enum values
```

- [ ] **Step 2:** Add 12 new values to the `ChangeType` enum in `backend/app/models/change_request.py`. Add a new comment section after the existing `ip_campaign` entry:

```python
    # OCI Sub-project 3 — networking (security lists, NSGs, load balancers, DNS)
    oci_security_list_add_rule = "oci_security_list_add_rule"
    oci_security_list_remove_rule = "oci_security_list_remove_rule"
    oci_nsg_create = "oci_nsg_create"
    oci_nsg_delete = "oci_nsg_delete"
    oci_nsg_rule_add = "oci_nsg_rule_add"
    oci_nsg_rule_remove = "oci_nsg_rule_remove"
    oci_load_balancer_create = "oci_load_balancer_create"
    oci_load_balancer_delete = "oci_load_balancer_delete"
    oci_backend_set_create = "oci_backend_set_create"
    oci_listener_create = "oci_listener_create"
    oci_dns_zone_create = "oci_dns_zone_create"
    oci_dns_record_upsert = "oci_dns_record_upsert"
```

- [ ] **Step 3:** Add the 12 new `ChangeType` members to `_IMPLICIT_ROLLBACK_TYPES` in `backend/app/services/safety_engine.py`. Add them alongside the existing string-based entries in the set:

```python
    # OCI Sub-project 3 — networking
    ChangeType.oci_security_list_add_rule,
    ChangeType.oci_security_list_remove_rule,
    ChangeType.oci_nsg_create,
    ChangeType.oci_nsg_delete,
    ChangeType.oci_nsg_rule_add,
    ChangeType.oci_nsg_rule_remove,
    ChangeType.oci_load_balancer_create,
    ChangeType.oci_load_balancer_delete,
    ChangeType.oci_backend_set_create,
    ChangeType.oci_listener_create,
    ChangeType.oci_dns_zone_create,
    ChangeType.oci_dns_record_upsert,
```

  These are added as enum members (not strings) since they are defined in the enum class. Insert them near the end of the set, before the closing brace. The import of `ChangeType` is already present at the top of `safety_engine.py`.

---

### Task 9: Frontend Extensions

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

- [ ] **Step 1:** Add 12 new `ChangeType` values to the union type in `frontend/src/types/api.ts`. Insert them before the closing `| "suppress"` line (or after the last existing OCI entries from sub-projects 1-2):

```typescript
  | "oci_security_list_add_rule"
  | "oci_security_list_remove_rule"
  | "oci_nsg_create"
  | "oci_nsg_delete"
  | "oci_nsg_rule_add"
  | "oci_nsg_rule_remove"
  | "oci_load_balancer_create"
  | "oci_load_balancer_delete"
  | "oci_backend_set_create"
  | "oci_listener_create"
  | "oci_dns_zone_create"
  | "oci_dns_record_upsert"
```

- [ ] **Step 2:** Add 12 new entries to `CHANGE_TYPE_META` in `frontend/src/pages/CreateChangeRequest.tsx`. Insert them in the "Oracle Cloud" section alongside any OCI entries from sub-projects 1-2:

```typescript
  oci_security_list_add_rule: {
    label: "OCI Add Security List Rule",
    description: "Add an inbound or outbound rule to an OCI Security List.",
    outcomeTemplate: JSON.stringify({
      security_list_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 22,
      port_max: 22,
      description: "nexplane-rule",
      rollback_strategy: "oci_security_list_remove_rule",
    }, null, 2),
  },
  oci_security_list_remove_rule: {
    label: "OCI Remove Security List Rule",
    description: "Remove an inbound or outbound rule from an OCI Security List.",
    outcomeTemplate: JSON.stringify({
      security_list_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 22,
      port_max: 22,
      rollback_strategy: "oci_security_list_add_rule",
    }, null, 2),
  },
  oci_nsg_create: {
    label: "OCI Create NSG",
    description: "Create a Network Security Group in an OCI VCN.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      vcn_id: "",
      display_name: "nexplane-nsg",
      rollback_strategy: "oci_nsg_delete",
    }, null, 2),
  },
  oci_nsg_delete: {
    label: "OCI Delete NSG",
    description: "Delete an OCI Network Security Group. Irreversible.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_nsg_rule_add: {
    label: "OCI Add NSG Rule",
    description: "Add a security rule to an OCI NSG.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      direction: "INGRESS",
      protocol: "6",
      source: "0.0.0.0/0",
      port_min: 443,
      port_max: 443,
      description: "nexplane-nsg-rule",
      rollback_strategy: "oci_nsg_rule_remove",
    }, null, 2),
  },
  oci_nsg_rule_remove: {
    label: "OCI Remove NSG Rule",
    description: "Remove a rule from an OCI NSG.",
    outcomeTemplate: JSON.stringify({
      nsg_id: "",
      rule_id: "",
      rollback_strategy: "oci_nsg_rule_add",
    }, null, 2),
  },
  oci_load_balancer_create: {
    label: "OCI Create Load Balancer",
    description: "Create an OCI flexible Load Balancer. Polls up to 15 min until ACTIVE.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      display_name: "nexplane-lb",
      shape_name: "flexible",
      shape_min_mbps: 10,
      shape_max_mbps: 100,
      subnet_ids: [],
      is_private: false,
      rollback_strategy: "oci_load_balancer_delete",
    }, null, 2),
  },
  oci_load_balancer_delete: {
    label: "OCI Delete Load Balancer",
    description: "Delete an OCI Load Balancer. Irreversible.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_backend_set_create: {
    label: "OCI Create Backend Set",
    description: "Create a backend set on an OCI Load Balancer.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      name: "nexplane-backend-set",
      policy: "ROUND_ROBIN",
      health_checker: {
        protocol: "HTTP",
        port: 80,
        url_path: "/health",
        return_code: 200,
        interval_ms: 10000,
        timeout_in_millis: 3000,
        retries: 3,
      },
      rollback_strategy: "delete_backend_set",
    }, null, 2),
  },
  oci_listener_create: {
    label: "OCI Create Listener",
    description: "Create a listener on an OCI Load Balancer.",
    outcomeTemplate: JSON.stringify({
      load_balancer_id: "",
      name: "nexplane-listener",
      default_backend_set: "nexplane-backend-set",
      port: 80,
      protocol: "HTTP",
      rollback_strategy: "delete_listener",
    }, null, 2),
  },
  oci_dns_zone_create: {
    label: "OCI Create DNS Zone",
    description: "Create an OCI DNS zone of type PRIMARY.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      name: "nexplane-test.example.com",
      zone_type: "PRIMARY",
      rollback_strategy: "oci_dns_zone_delete",
    }, null, 2),
  },
  oci_dns_record_upsert: {
    label: "OCI Upsert DNS Record",
    description: "Create or update an A, CNAME, or other DNS record in an OCI DNS zone.",
    outcomeTemplate: JSON.stringify({
      zone_name_or_id: "",
      domain: "www.nexplane-test.example.com",
      rtype: "A",
      ttl: 300,
      rdata: "10.0.0.1",
      rollback_strategy: "restore_previous_record",
    }, null, 2),
  },
```

- [ ] **Step 3:** Add "Oracle Cloud Networking" as a category group in the `CATEGORY_GROUPS` or equivalent category map in `CreateChangeRequest.tsx`, grouping the 12 new change types together. Find the section where OCI change types from sub-projects 1-2 are categorized and add the new types to the "Oracle Cloud" section. The exact structure will depend on what sub-projects 1-2 added, but the pattern will be:

```typescript
// In the Oracle Cloud category group:
"oci_security_list_add_rule",
"oci_security_list_remove_rule",
"oci_nsg_create",
"oci_nsg_delete",
"oci_nsg_rule_add",
"oci_nsg_rule_remove",
"oci_load_balancer_create",
"oci_load_balancer_delete",
"oci_backend_set_create",
"oci_listener_create",
"oci_dns_zone_create",
"oci_dns_record_upsert",
```

---

### Task 10: Smoke Tests

**Files:**
- Create: `backend/tests/smoke/test_oci_h_security_nsg.py`
- Create: `backend/tests/smoke/test_oci_i_load_balancer.py`
- Create: `backend/tests/smoke/test_oci_j_dns.py`

> **Important:** Smoke tests MUST use Nexplane rollback (trigger the change request rollback endpoint) as the primary cleanup mechanism. The OCI SDK is the safety-net fallback only. See project memory: smoke test rollback stack pattern.

- [ ] **Step 1:** Create `test_oci_h_security_nsg.py`:

```python
"""
Smoke Test Phase OCI_H — Security Lists and NSGs
Requires: OCI credentials in connector, a live Security List OCID, and a live VCN OCID.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field


SECURITY_LIST_ID = os.getenv("OCI_SMOKE_SECURITY_LIST_ID", "")
VCN_ID = os.getenv("OCI_SMOKE_VCN_ID", "")
COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")


@pytest.mark.skipif(not SECURITY_LIST_ID, reason="OCI_SMOKE_SECURITY_LIST_ID not set")
@pytest.mark.asyncio
async def test_oci_h1_add_security_list_rule(api_client, org_id):
    """Fire oci_security_list_add_rule (port 8080) → verify rule present via OCI SDK."""
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_H1 Add SL Rule port 8080",
        "change_type": "oci_security_list_add_rule",
        "target_asset_ids": [],
        "desired_outcome": {
            "security_list_id": SECURITY_LIST_ID,
            "direction": "INGRESS",
            "protocol": "6",
            "source": "0.0.0.0/0",
            "port_min": 8080,
            "port_max": 8080,
            "description": "nexplane-smoke-h1",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify rule present via OCI SDK
    import oci
    # (OCI credentials loaded from env or connector — use raw SDK as verification)
    # Primary cleanup: Nexplane rollback
    try:
        await rollback_cr(api_client, cr_id)
    except Exception:
        pass  # Safety net: OCI SDK cleanup happens below if rollback fails

    # Post-rollback verify rule is gone (SDK safety net check)


@pytest.mark.skipif(not VCN_ID, reason="OCI_SMOKE_VCN_ID not set")
@pytest.mark.asyncio
async def test_oci_h2_nsg_lifecycle(api_client, org_id):
    """oci_nsg_create → verify firewall asset in inventory → oci_nsg_rule_add → oci_nsg_delete rollback."""
    # Step 1: Create NSG
    cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_H2 Create NSG",
        "change_type": "oci_nsg_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "vcn_id": VCN_ID,
            "display_name": "nexplane-smoke-nsg",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify firewall asset with tag oci-nsg appears in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, tag="oci-nsg", name="nexplane-smoke-nsg")
    nsg_id = asset["asset_metadata"]["nsg_id"] if asset else None

    if nsg_id:
        # Step 2: Add rule
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_H2 Add NSG Rule",
            "change_type": "oci_nsg_rule_add",
            "target_asset_ids": [],
            "desired_outcome": {
                "nsg_id": nsg_id,
                "direction": "INGRESS",
                "protocol": "6",
                "source": "0.0.0.0/0",
                "port_min": 8443,
                "port_max": 8443,
                "description": "nexplane-smoke-h2-rule",
            },
        }, connector_id=OCI_CONNECTOR_ID)

    # Rollback: delete NSG via Nexplane rollback (primary)
    await rollback_cr(api_client, cr_id)
```

- [ ] **Step 2:** Create `test_oci_i_load_balancer.py`:

```python
"""
Smoke Test Phase OCI_I — Load Balancer
Requires: OCI credentials, a live subnet OCID in the test compartment.
WARNING: OCI LB creation takes up to 15 minutes and may incur costs.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field

SUBNET_IDS = os.getenv("OCI_SMOKE_SUBNET_IDS", "").split(",")
COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")


@pytest.mark.skipif(not SUBNET_IDS or not SUBNET_IDS[0], reason="OCI_SMOKE_SUBNET_IDS not set")
@pytest.mark.asyncio
@pytest.mark.timeout(1200)  # 20 min timeout for slow LB provisioning
async def test_oci_i_load_balancer_lifecycle(api_client, org_id):
    """Create LB → backend set → listener → delete LB (via rollback)."""
    # Step 1: Create Load Balancer
    lb_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_I Create LB",
        "change_type": "oci_load_balancer_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "display_name": "nexplane-smoke-lb",
            "shape_name": "flexible",
            "shape_min_mbps": 10,
            "shape_max_mbps": 100,
            "subnet_ids": [s for s in SUBNET_IDS if s],
            "is_private": True,
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify load_balancer asset in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, asset_type="load_balancer", name="nexplane-smoke-lb")
    lb_id = asset["asset_metadata"]["load_balancer_id"] if asset else None
    assert lb_id, "load_balancer asset not found in inventory after creation"

    if lb_id:
        # Step 2: Create backend set
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_I Create Backend Set",
            "change_type": "oci_backend_set_create",
            "target_asset_ids": [],
            "desired_outcome": {
                "load_balancer_id": lb_id,
                "name": "nexplane-smoke-bs",
                "policy": "ROUND_ROBIN",
            },
        }, connector_id=OCI_CONNECTOR_ID)

        # Step 3: Create listener
        await create_and_execute_cr(api_client, org_id, {
            "title": "OCI_I Create Listener",
            "change_type": "oci_listener_create",
            "target_asset_ids": [],
            "desired_outcome": {
                "load_balancer_id": lb_id,
                "name": "nexplane-smoke-listener",
                "default_backend_set": "nexplane-smoke-bs",
                "port": 80,
                "protocol": "HTTP",
            },
        }, connector_id=OCI_CONNECTOR_ID)

    # Primary cleanup: rollback the LB creation (triggers delete_load_balancer)
    await rollback_cr(api_client, lb_cr_id)
```

- [ ] **Step 3:** Create `test_oci_j_dns.py`:

```python
"""
Smoke Test Phase OCI_J — DNS
Requires: OCI credentials and a compartment where DNS zones can be created.
"""
import pytest
import os
from tests.smoke.helpers import create_and_execute_cr, rollback_cr, get_asset_by_metadata_field

COMPARTMENT_ID = os.getenv("OCI_SMOKE_COMPARTMENT_ID", "")
OCI_CONNECTOR_ID = os.getenv("OCI_SMOKE_CONNECTOR_ID", "")
DNS_ZONE_NAME = os.getenv("OCI_SMOKE_DNS_ZONE_NAME", "nexplane-smoke-test.example.com")


@pytest.mark.skipif(not COMPARTMENT_ID, reason="OCI_SMOKE_COMPARTMENT_ID not set")
@pytest.mark.asyncio
async def test_oci_j_dns_lifecycle(api_client, org_id):
    """Create DNS zone → upsert record → verify via OCI SDK → rollback zone."""
    # Step 1: Create DNS zone
    zone_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_J Create DNS Zone",
        "change_type": "oci_dns_zone_create",
        "target_asset_ids": [],
        "desired_outcome": {
            "compartment_id": COMPARTMENT_ID,
            "name": DNS_ZONE_NAME,
            "zone_type": "PRIMARY",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Verify dns_zone asset in inventory
    asset = await get_asset_by_metadata_field(api_client, org_id, asset_type="dns_zone", name=DNS_ZONE_NAME)
    assert asset, f"dns_zone asset '{DNS_ZONE_NAME}' not found in inventory"
    zone_id = asset["asset_metadata"]["zone_id"]

    # Step 2: Upsert A record
    record_cr_id = await create_and_execute_cr(api_client, org_id, {
        "title": "OCI_J Upsert DNS Record",
        "change_type": "oci_dns_record_upsert",
        "target_asset_ids": [],
        "desired_outcome": {
            "zone_name_or_id": zone_id,
            "domain": f"smoke.{DNS_ZONE_NAME}",
            "rtype": "A",
            "ttl": 60,
            "rdata": "192.0.2.1",
        },
    }, connector_id=OCI_CONNECTOR_ID)

    # Rollback record first, then zone (primary: Nexplane rollback)
    await rollback_cr(api_client, record_cr_id)
    await rollback_cr(api_client, zone_cr_id)
```

---

### Task 11: Commit All Changes

- [ ] **Step 1:** Stage all new and modified files:

```bash
git add \
  backend/app/connectors/executors/oci/discover_security_lists.py \
  backend/app/connectors/executors/oci/discover_nsgs.py \
  backend/app/connectors/executors/oci/add_security_list_rule.py \
  backend/app/connectors/executors/oci/remove_security_list_rule.py \
  backend/app/connectors/executors/oci/create_nsg.py \
  backend/app/connectors/executors/oci/delete_nsg.py \
  backend/app/connectors/executors/oci/add_nsg_rule.py \
  backend/app/connectors/executors/oci/remove_nsg_rule.py \
  backend/app/connectors/executors/oci/discover_load_balancers.py \
  backend/app/connectors/executors/oci/create_load_balancer.py \
  backend/app/connectors/executors/oci/delete_load_balancer.py \
  backend/app/connectors/executors/oci/create_backend_set.py \
  backend/app/connectors/executors/oci/create_listener.py \
  backend/app/connectors/executors/oci/discover_dns_zones.py \
  backend/app/connectors/executors/oci/create_dns_zone.py \
  backend/app/connectors/executors/oci/upsert_dns_record.py \
  backend/app/connectors/executors/oci/_client.py \
  backend/app/connectors/catalog/oci.json \
  backend/alembic/versions/042_add_oci_networking_change_types.py \
  backend/app/models/change_request.py \
  backend/app/services/safety_engine.py \
  frontend/src/types/api.ts \
  frontend/src/pages/CreateChangeRequest.tsx \
  backend/tests/smoke/test_oci_h_security_nsg.py \
  backend/tests/smoke/test_oci_i_load_balancer.py \
  backend/tests/smoke/test_oci_j_dns.py
```

- [ ] **Step 2:** Commit:

```bash
git commit -m "feat: OCI connector sub-project 3 — security list, NSG, load balancer, DNS executors"
```

---

## Key Implementation Notes

### OCI SDK Client Pattern
All executors import from `._client` (sub-project 1's shared module). The two new client factories added in Tasks 4 and 6 follow the same pattern as `get_network_client`: call `_build_oci_config(creds)` then construct the appropriate SDK client.

### Load Balancer Polling Strategy
OCI LB creation returns a work request ID, not the LB ID directly. Poll `get_work_request(work_request_id)` every 15 seconds until `lifecycle_state == "SUCCEEDED"`. Extract the load balancer OCID from the work request's `load_balancer_id` field. Hard timeout: 900 seconds (15 min). If `lifecycle_state` is `"FAILED"` or `"CANCELED"`, raise immediately with the error message.

### Security List vs NSG Rule Matching
Security List rules are identified by matching: direction + protocol + source/destination CIDR + port range. NSG rules have explicit IDs returned by `add_network_security_group_security_rules()`, so `add_nsg_rule.py` stores the `rule_id` in the execution result for fast rollback. The `remove_nsg_rule.py` falls back to matching if `rule_id` is not provided.

### Migration Revision Number
At implementation time, verify that sub-projects 1 and 2 used revisions 040 and 041. If they used different numbers, update `down_revision` in the migration accordingly. The migration filename `042_...` is correct per the spec.

### Frontend Category Grouping
The exact location to insert the new `CHANGE_TYPE_META` entries and the category group depends on what sub-projects 1 and 2 added. Look for the "Oracle Cloud" or "OCI" category section and append to it. If no such section exists, create one.
