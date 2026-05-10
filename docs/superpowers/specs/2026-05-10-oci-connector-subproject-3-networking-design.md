# OCI Connector — Sub-project 3: Networking Design

## Scope

Security Lists, Network Security Groups (NSGs), OCI LBaaS (Load Balancer), and OCI DNS. Follows patterns from sub-projects 1-2.

**Key design decisions:**
- Security Lists and NSGs → `firewall` asset type (same as AWS security groups)
- OCI Load Balancers → `load_balancer` asset type (exact fit)
- OCI DNS Zones → `dns_zone` asset type (exact fit)
- VCN subnets already created in sub-project 1; security list updates operate on existing subnets' security lists

---

## New Change Types (12)

```
oci_security_list_add_rule      Add inbound/outbound rule to a security list
oci_security_list_remove_rule   Remove rule from a security list
oci_nsg_create                  Create a Network Security Group
oci_nsg_delete                  Delete an NSG
oci_nsg_rule_add                Add rules to an NSG
oci_nsg_rule_remove             Remove rules from an NSG
oci_load_balancer_create        Create an OCI Load Balancer
oci_load_balancer_delete        Delete an OCI Load Balancer
oci_backend_set_create          Create a backend set on a load balancer
oci_listener_create             Create a listener on a load balancer
oci_dns_zone_create             Create an OCI DNS zone
oci_dns_record_upsert           Create or update a DNS record
```

All 12 added to `_IMPLICIT_ROLLBACK_TYPES`.

---

## New Files

```
backend/app/connectors/executors/oci/
  discover_security_lists.py
  discover_nsgs.py
  discover_load_balancers.py
  discover_dns_zones.py
  add_security_list_rule.py
  remove_security_list_rule.py
  create_nsg.py
  delete_nsg.py
  add_nsg_rule.py
  remove_nsg_rule.py
  create_load_balancer.py
  delete_load_balancer.py
  create_backend_set.py
  create_listener.py
  create_dns_zone.py
  upsert_dns_record.py

backend/alembic/versions/042_add_oci_networking_change_types.py
```

New OCI SDK clients in `_client.py`:
```python
def get_loadbalancer_client(creds) -> oci.load_balancer.LoadBalancerClient
def get_dns_client(creds) -> oci.dns.DnsClient
```

---

## Discovery

### `discover_security_lists.py`
- Calls `network.list_security_lists(compartment_id)`
- Each security list → `firewall` asset:
  ```json
  {
    "name": "<display_name>",
    "asset_type": "firewall",
    "asset_metadata": {
      "security_list_id": "ocid1.securitylist...",
      "vcn_id": "ocid1.vcn...",
      "compartment_id": "...",
      "lifecycle_state": "AVAILABLE",
      "ingress_rule_count": 3,
      "egress_rule_count": 1
    },
    "tags": ["oci", "security-list"]
  }
  ```
- Dedup key: `security_list_id`

### `discover_nsgs.py`
- Calls `network.list_network_security_groups(compartment_id)`
- Each NSG → `firewall` asset tagged `oci-nsg`
- Dedup key: `nsg_id`

### `discover_load_balancers.py`
- Calls `loadbalancer.list_load_balancers(compartment_id)`
- Each LB → `load_balancer` asset:
  ```json
  {
    "name": "<display_name>",
    "asset_type": "load_balancer",
    "asset_metadata": {
      "load_balancer_id": "ocid1.loadbalancer...",
      "shape_name": "flexible",
      "shape_min_mbps": 10,
      "shape_max_mbps": 100,
      "ip_addresses": ["<public_ip>"],
      "lifecycle_state": "ACTIVE",
      "compartment_id": "..."
    },
    "tags": ["oci", "load-balancer"]
  }
  ```
- Dedup key: `load_balancer_id`

### `discover_dns_zones.py`
- Calls `dns.list_zones(compartment_id)`
- Each zone → `dns_zone` asset:
  ```json
  {
    "name": "<zone_name>",
    "asset_type": "dns_zone",
    "asset_metadata": {
      "zone_id": "ocid1.dns-zone...",
      "zone_name": "<name>",
      "zone_type": "PRIMARY",
      "compartment_id": "...",
      "serial": 1
    },
    "tags": ["oci", "dns"]
  }
  ```
- Dedup key: `zone_id`

---

## Security List Executors

### `add_security_list_rule.py` (`oci_security_list_add_rule`)
**Parameters (all pre-populated):**
```
security_list_id   resolved from target firewall asset (tagged oci security-list)
direction          "INGRESS"
protocol           "6"    (TCP)
source             "0.0.0.0/0"
port_min           22
port_max           22
description        "nexplane-rule"
```
Fetches existing rules, appends new rule, calls `network.update_security_list()`.
Rollback: remove the added rule.

### `remove_security_list_rule.py` (`oci_security_list_remove_rule`)
- Identifies rule by direction + protocol + source/dest + port, removes from list
- Parameters: `security_list_id`, `direction`, `protocol`, `source`, `port_min`, `port_max` (all auto-populated where possible)
- Rollback: re-add the removed rule (stored in execution result)

---

## NSG Executors

### `create_nsg.py` (`oci_nsg_create`)
**Parameters (all pre-populated):**
```
compartment_id   resolved from target compartment
vcn_id           resolved from oci-vcn asset in compartment
display_name     "nexplane-nsg"
```
Returns `_auto_asset` (`firewall`, tagged `oci-nsg`). Rollback: `oci_nsg_delete`.

### `delete_nsg.py` (`oci_nsg_delete`)
- Calls `network.delete_network_security_group(nsg_id)`
- Parameters: `nsg_id` (auto-populated from target asset)
- Rollback: none (destructive)

### `add_nsg_rule.py` (`oci_nsg_rule_add`)
**Parameters (all pre-populated):**
```
nsg_id        resolved from target firewall asset (tagged oci-nsg)
direction     "INGRESS"
protocol      "6"
source        "0.0.0.0/0"
port_min      443
port_max      443
description   "nexplane-nsg-rule"
```
Rollback: remove added rule.

### `remove_nsg_rule.py` (`oci_nsg_rule_remove`)
- Resolves rule by matching parameters, calls `network.remove_network_security_group_security_rules()`
- Rollback: re-add rule

---

## Load Balancer Executors

### `create_load_balancer.py` (`oci_load_balancer_create`)
**Parameters (all pre-populated):**
```
compartment_id    resolved from target compartment
display_name      "nexplane-lb"
shape_name        "flexible"
shape_min_mbps    10
shape_max_mbps    100
subnet_ids        [resolved from oci-subnet asset in compartment]
is_private        false
```
Polls until lifecycle = ACTIVE (up to 15 min — OCI LB creation is slow).
Returns `_auto_asset` (`load_balancer`). Rollback: `oci_load_balancer_delete`.

### `delete_load_balancer.py` (`oci_load_balancer_delete`)
- Calls `loadbalancer.delete_load_balancer(lb_id)`, polls until DELETED
- Parameters: `load_balancer_id` (auto-populated)
- Rollback: none (destructive)

### `create_backend_set.py` (`oci_backend_set_create`)
**Parameters (all pre-populated):**
```
load_balancer_id   resolved from target load_balancer asset
name               "nexplane-backend-set"
policy             "ROUND_ROBIN"
health_checker     {"protocol": "HTTP", "port": 80, "url_path": "/health", "return_code": 200, "interval_ms": 10000, "timeout_in_millis": 3000, "retries": 3}
```
Rollback: delete backend set.

### `create_listener.py` (`oci_listener_create`)
**Parameters (all pre-populated):**
```
load_balancer_id      resolved from target load_balancer asset
name                  "nexplane-listener"
default_backend_set   "nexplane-backend-set"
port                  80
protocol              "HTTP"
```
Rollback: delete listener.

---

## DNS Executors

### `create_dns_zone.py` (`oci_dns_zone_create`)
**Parameters (all pre-populated):**
```
compartment_id   resolved from target compartment
name             "nexplane-test.example.com"
zone_type        "PRIMARY"
```
Returns `_auto_asset` (`dns_zone`). Rollback: delete zone.

### `upsert_dns_record.py` (`oci_dns_record_upsert`)
**Parameters (all pre-populated):**
```
zone_name_or_id   resolved from target dns_zone asset
domain            "www.nexplane-test.example.com"
rtype             "A"
ttl               300
rdata             "10.0.0.1"
```
Rollback: delete record (or restore previous value stored in execution result).

---

## Frontend Wiring

### `api.ts`
12 new `ChangeType` values.

### `CreateChangeRequest.tsx`
"Oracle Cloud" category extended with 12 new entries. All outcome templates pre-populated with sensible defaults.

### `AssetDetail.tsx`
- `firewall` assets tagged `oci security-list`: quick actions add/remove rule
- `firewall` assets tagged `oci-nsg`: quick actions add/remove rule, delete
- `load_balancer` assets: quick actions create backend set, listener, delete
- `dns_zone` assets: quick action upsert record

---

## Smoke Test Phases

### OCI_H — Security + NSG
1. Fire `oci_security_list_add_rule` (add port 8080 inbound) → OCI SDK verify rule present
2. Fire `oci_security_list_remove_rule` rollback → verify rule removed
3. Fire `oci_nsg_create` → verify `firewall` asset in inventory
4. Fire `oci_nsg_rule_add` → OCI SDK verify rule on NSG
5. Fire `oci_nsg_delete` rollback → verify NSG deleted

### OCI_I — Load Balancer
1. Fire `oci_load_balancer_create` → poll until ACTIVE, verify `load_balancer` asset in inventory
2. Fire `oci_backend_set_create` → OCI SDK verify backend set exists
3. Fire `oci_listener_create` → OCI SDK verify listener exists
4. Fire `oci_load_balancer_delete` rollback → verify DELETED

### OCI_J — DNS
1. Fire `oci_dns_zone_create` → OCI SDK verify zone exists, verify `dns_zone` asset in inventory
2. Fire `oci_dns_record_upsert` → OCI SDK verify record present
3. Fire rollback → verify zone deleted

---

## DB Migration (042)

```sql
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_add_rule';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_security_list_remove_rule';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_add';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_nsg_rule_remove';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_load_balancer_delete';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_backend_set_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_listener_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_zone_create';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'oci_dns_record_upsert';
```
