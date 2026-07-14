# Reference Scan and Update — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give operators a single workflow that finds every hardcoded reference to a migrating resource — connection strings, hostnames, IPs, DSNs, CNAMEs, ARNs, secret names — across all connected infrastructure surfaces, registers discovered consumers in the asset graph, and proposes rollbackable update CRs for each one. Covers any migration where a resource moves, is renamed, or has its access credentials changed. Not limited to database migrations.

**Architecture:** Scan CRs fan out search terms to connector-specific catalog actions (AWS, K8s, Nexplane agent for brownfield/bare metal); results pass through an identity resolution layer that deduplicates against the existing asset graph and auto-registers new consumers; an AI triage layer splits results into confident update CRs and an exception bucket; exceptions are surfaced as findings and resolved through three explicit paths (operator-guided update, AI re-attempt, dismiss). Every update executor uses the reconstitution rollback pattern. All workflow steps are available via MCP tools with feature parity to the UI.

**Tech Stack:** Python (FastAPI backend), SQLAlchemy async, existing connector catalog pattern, FastMCP, Anthropic SDK (triage), existing `AssetDependency` model, existing `Finding` model.

## Global Constraints

- AI proposes CRs; a human approves — the approval gate is mandatory for every update CR without exception.
- Every update executor must save the old value before writing the new one. Rollback restores the saved value (reconstitution pattern).
- Exception bucket is mandatory output — zero exceptions dropped silently. Every unresolved exception at execution time becomes a `reference_not_updated` finding attached to the consumer asset that holds the unresolved reference.
- Secret values are never stored, logged, or returned. Secrets Manager and Vault scanning matches on metadata (name, ARN, path) only.
- MCP tools must cover every workflow step — scan initiation, result retrieval, confident CR approval, exception resolution (all three paths). UI and MCP are feature-identical.
- AWS is Phase 1. OCI, Azure, and GCP are Phase 2 and must conform to the identical scan interface contract defined here — Phase 2 is a mechanical port, not a redesign.
- The nexplane agent is the universal fallback for any surface not covered by a cloud connector (brownfield, bare metal, on-prem, K8s via kubeconfig on a monitored host).
- FILO rollback order applies when multiple update CRs are grouped in a project — the platform enforces reverse-order unwind.

---

## 1. Scan Interface Contract

All connector scan actions — regardless of surface — emit a unified result schema. The identity resolution and AI triage layers are surface-agnostic.

**Scan input (parameters passed to every scan catalog action):**
```json
{
  "search_terms": ["old-postgres.internal", "10.0.1.45", "postgres://user:pass@old-host:5432/db"],
  "scope": "all" | {"regions": ["us-east-1"], "namespaces": ["prod"]},
  "migrating_asset_id": "<uuid of the asset being migrated>"
}
```

`search_terms` is a list of any identifier form — hostname, IP, DSN pattern, CNAME, ARN, secret name, DNS address. The scanner pattern-matches each term independently and returns all hits.

**Scan output (per hit, all surfaces):**
```json
{
  "surface": "lambda" | "ecs" | "ssm" | "k8s_configmap" | "k8s_secret" | "k8s_deployment_env" | "k8s_ingress" | "host_file",
  "location": "<ARN, namespace/kind/name/key, or filepath:line>",
  "matched_term": "<which search term matched>",
  "snippet": "<surrounding context, redacted if secret>",
  "consumer_identity": {
    "stable_id": "<ARN, instance-id, namespace/kind/name, or null>",
    "hostname": "<if resolvable>",
    "surface_metadata": {}
  }
}
```

---

## 2. New ChangeTypes

Add to `app/models/change_request.py` ChangeType enum:

- `scan_for_references` — initiates the multi-surface scan; produces triage output
- `update_reference` — updates a single reference at a single location; one CR per consumer per location (the existing `update_connection_strings` stub is retired in favour of this more general name)

`update_reference` parameters shape:
```json
{
  "connector_type": "aws" | "kubernetes" | "nexplane_agent",
  "surface": "lambda" | "ecs" | "k8s_configmap" | "host_file" | ...,
  "location": "<ARN or path>",
  "old_value": "<value being replaced — stored for rollback>",
  "new_value": "<replacement value>",
  "rollback_strategy": "reconstitution"
}
```

Add to `app/services/manifest_builder.py`:
- `scan_for_references`: domain `migration`, touches `[asset_graph, connected_surfaces]`, rollback `none` (read-only)
- `update_reference`: domain `migration`, touches `[consumer_config]`, rollback `reconstitution`

---

## 3. AWS Connector — Scan and Update Catalog Actions (Phase 1)

Add to `app/connectors/catalog/aws.json`:

**Scan actions** (action_type: `"ingest"`):
- `scan_lambda_references` — enumerate all Lambda functions, check env vars against search_terms
- `scan_ecs_references` — enumerate active ECS task definition revisions, check env vars
- `scan_ssm_references` — enumerate SSM Parameter Store values (String and StringList types; SecureString metadata only)
- `scan_secrets_manager_references` — enumerate Secrets Manager secret names and descriptions; never decrypt values
- `scan_cloudformation_references` — enumerate CloudFormation stack template bodies
- `scan_codebuild_references` — enumerate CodeBuild project environment variable names and values

**Update actions** (action_type: `"change"`):
- `update_lambda_env_var` — `get_function_configuration` → save env → `update_function_configuration`; rollback restores saved env map
- `update_ecs_task_env` — register new task definition revision with updated env; rollback registers a revision restoring old env
- `update_ssm_parameter` — `get_parameter` → save value → `put_parameter`; rollback `put_parameter` with saved value
- `update_secrets_manager_metadata` — update secret name or description only (not secret value); rollback restores prior name/description

Each update action's parameter schema:
```json
{
  "location": "<ARN>",
  "key": "<env var name or parameter name>",
  "old_value": "<saved for rollback>",
  "new_value": "<replacement>"
}
```

Executor file: `app/connectors/executors/aws/reference_scan.py` (scan) and `app/connectors/executors/aws/reference_update.py` (update).

---

## 4. Kubernetes Connector — Scan and Update Catalog Actions (Phase 1)

Add to `app/connectors/catalog/kubernetes.json`:

**Scan actions** (action_type: `"ingest"`):
- `scan_k8s_configmap_references` — enumerate all ConfigMaps across namespaces (or scoped), check values
- `scan_k8s_secret_references` — enumerate Secrets, base64-decode values, check against search_terms; snippet redacted in output
- `scan_k8s_deployment_env_references` — enumerate Deployment/StatefulSet/DaemonSet env var specs
- `scan_k8s_ingress_references` — enumerate Ingress rules (host fields, backend service names)

**Update actions** (action_type: `"change"`):
- `update_k8s_configmap` — `get` ConfigMap → save old data → `patch`; rollback patches back
- `update_k8s_secret` — `get` Secret → save old data (base64) → `patch`; rollback patches back
- `update_k8s_deployment_env` — `get` Deployment → save spec → `patch` env var; rollback patches spec back

Executor file: `app/connectors/executors/kubernetes/reference_scan.py` and `reference_update.py`.

---

## 5. Nexplane Agent — Scan and Update Catalog Actions (Phase 1, brownfield universal fallback)

Add to `app/connectors/catalog/nexplane_agent.json`:

**Scan action** (action_type: `"ingest"`):
- `scan_host_references` — recursive grep across config file paths for each search term

  Parameters:
  ```json
  {
    "search_terms": ["..."],
    "paths": ["/etc", "/opt", "/home", "/var/app"],
    "extensions": [".conf", ".ini", ".yaml", ".yml", ".json", ".env", ".sh", ".toml"],
    "exclude_paths": ["/etc/shadow", "/proc", "/sys"]
  }
  ```

  Also scans: docker-compose files, systemd unit files, kubeconfig files (enables K8s surface scanning on hosts with cluster access but no registered K8s connector).

**Update action** (action_type: `"change"`):
- `update_config_file_reference` — read file → save backup → sed-equivalent replacement → write

  Parameters:
  ```json
  {
    "filepath": "/etc/app/config.yml",
    "line": 42,
    "old_value": "old-postgres.internal",
    "new_value": "new-postgres.internal"
  }
  ```

  Rollback: restore backup copy. Backup stored as `<filepath>.nexplane-pre-<cr_id>`.

Go agent additions: `agent/commands/reference/scan.go` and `agent/commands/reference/update.go`.

---

## 6. OCI, Azure, GCP — Phase 2

Each cloud gets the same scan and update action set as AWS, scoped to its equivalent primitives:

| AWS | OCI | Azure | GCP |
|-----|-----|-------|-----|
| Lambda | OCI Functions | Azure Functions | Cloud Functions / Cloud Run |
| ECS | OCI Container Instances | Azure Container Instances / ACI | Cloud Run / GKE workloads |
| SSM Parameter Store | OCI Vault secrets (metadata) | Azure App Configuration | Secret Manager (metadata) |
| Secrets Manager | OCI Vault (metadata) | Azure Key Vault (metadata) | Secret Manager (metadata) |
| CloudFormation | Resource Manager stacks | ARM templates | Deployment Manager |
| CodeBuild | OCI DevOps | Azure Pipelines | Cloud Build |

Phase 2 catalog files: `oci.json`, `azure.json`, `gcp.json` (extend existing). Executor files follow the same naming pattern as AWS.

Phase 2 is out of scope for the initial implementation plan but must conform to the scan interface contract in Section 1 without modification.

---

## 7. Identity Resolution Service

New service: `app/services/identity_resolution.py`

**Input:** a raw scan hit with `consumer_identity` fields.
**Output:** one of — matched existing `Asset.id`, proposed merge with confidence score, or `None` (new asset).

**Resolution tiers (evaluated in order):**

1. **Stable cloud ID** — match `consumer_identity.stable_id` against `Asset.external_id` and `Asset.metadata["arn"]`. Exact match = same asset.

2. **Hostname / DNS** — match `consumer_identity.hostname` against `Asset.hostname` and `Asset.metadata["aliases"]`. Also resolve CNAMEs: if CNAME resolves to the same A record as a known asset's hostname, treat as same asset.

3. **Composite fingerprint** — for bare metal / DHCP environments: match on MAC address + OS fingerprint + primary interface from `Asset.metadata`. Requires ≥ 2 signals to match. If exactly 2 signals match, score = 0.7 (propose merge). If all 3 match, score = 1.0 (auto-merge).

4. **Unresolvable** — score below threshold → new asset. Type inferred from surface:
   - `lambda` / `ecs` / cloud function → `AssetType.application`
   - `k8s_deployment` / `k8s_statefulset` → `AssetType.kubernetes_workload`
   - `host_file` → `AssetType.server`
   - K8s ConfigMap / Secret → `AssetType.application` (config artifact)

**Merge conflicts (composite score 0.5–0.8)** surface as exceptions in the triage output — same exception bucket as AI triage, with message: "Discovered consumer may already be registered as asset X (confidence: 72%) — confirm or register separately."

---

## 8. Auto-Registration

Runs after identity resolution, within the `scan_for_references` CR executor.

For each scan hit:
- **Matched existing asset**: add or update `AssetDependency` edge: `dependent_asset_id = consumer`, `dependency_asset_id = migrating_asset`, `dependency_type = "references"`, `dep_metadata = {surface, location, matched_term}`, `source = "reference_scan"`.
- **New asset**: create `Asset` record with inferred type, `tags = ["source:reference_scan"]`, `external_id = stable_id if present`. Then add dependency edge as above.
- **Merge conflict**: add to exception bucket; do not create duplicate asset record.

Asset creation is committed before triage runs — consumers are in the graph regardless of whether their update CRs are approved.

---

## 9. AI Triage Service

New service: `app/services/reference_triage.py`

**Input:** list of resolved scan hits (each with asset_id, location, matched_term, snippet, surface). Plus: the migrating asset's current registered identifiers (new hostname, new ARN, new DSN — whatever the platform knows as the resource's post-migration identity).

**Triage call:** single Anthropic SDK call with all hits batched. Prompt provides: the old search terms, the new identifiers for the migrating asset, and each hit's snippet with surface context.

**Output schema:**
```json
{
  "confident": [
    {
      "hit_id": "...",
      "old_value": "old-postgres.internal:5432",
      "new_value": "new-postgres.internal:5432",
      "rationale": "Direct hostname replacement in DATABASE_URL env var"
    }
  ],
  "exceptions": [
    {
      "hit_id": "...",
      "reason": "Reference appears inside a Jinja2 template — substitution source unknown",
      "suggested_action": "Verify template rendering context before updating",
      "confidence": 0.3
    }
  ]
}
```

**Exception triggers** (AI instructed to classify as exception when):
- Reference is in a comment or documentation string
- Reference is inside a template expression where the substitution source cannot be determined
- Multiple candidate new values exist (ambiguous migration target)
- Hit is in a read-only or compiled artifact
- Secret reference resolves to multiple downstream consumers with different migration targets
- Confidence in the correct new value is below 0.6

---

## 10. Exception Resolution

Three resolution paths, all persisted as `ScanException` records (`app/models/scan_exception.py`):

**Schema:**
```python
class ScanException(Base):
    id: UUID
    scan_cr_id: UUID          # parent scan CR
    hit_id: str               # identifies the specific scan hit
    consumer_asset_id: UUID
    location: str
    matched_term: str
    snippet: str
    reason: str               # AI explanation
    suggested_action: str
    status: Literal["open", "resolved", "dismissed", "reattempting"]
    resolution_cr_id: UUID | None   # set when resolved via update CR
    dismissed_reason: str | None
    resolved_at: datetime | None
```

**Path 1 — Operator-guided update:** operator provides `old_value` and `new_value`. Platform creates a standard `update_reference` CR with `source = "exception_resolution"`. Exception status → `resolved`, `resolution_cr_id` set. CR goes through normal approve/execute/rollback lifecycle.

**Path 2 — AI re-attempt:** operator provides additional context string. Platform re-runs triage for this single hit with the extra context appended. If AI reclassifies as confident, draft update CR as in Path 1. If still exception, update `reason` and `suggested_action` with new AI output, status remains `open`.

**Path 3 — Dismiss:** operator provides mandatory reason. Status → `dismissed`, `dismissed_reason` set. Retained permanently in audit trail.

**Unresolved exceptions at execution time:** any `ScanException` with `status = "open"` when the update CR batch is approved creates a `Finding` of type `reference_not_updated`, severity `medium` if the old endpoint is being decommissioned / `low` otherwise, attached to `consumer_asset_id`. Finding body includes location, matched_term, and AI reason.

---

## 11. MCP Tools

Add to `app/mcp_tools/` — new file `app/mcp_tools/reference_scan.py`. Import in `app/mcp_server.py`.

```python
scan_for_references(
    token: str,
    migrating_asset_id: str,
    search_terms: list[str],
    scope: dict = None,          # optional region/namespace scoping
    connector_ids: list[str] = None,  # optional — default: all active connectors
) -> dict   # returns draft scan CR id

get_scan_results(
    token: str,
    scan_cr_id: str,
) -> dict   # confident bucket, exception bucket, registered consumers

resolve_reference_exception(
    token: str,
    exception_id: str,
    old_value: str,
    new_value: str,
) -> dict   # drafted update CR id

reattempt_reference_triage(
    token: str,
    exception_id: str,
    context: str,
) -> dict   # updated exception or drafted update CR id

dismiss_reference_exception(
    token: str,
    exception_id: str,
    reason: str,
) -> dict   # confirmed dismissed status

list_reference_exceptions(
    token: str,
    scan_cr_id: str,
    status: str = "open",
) -> list[dict]
```

Update `app/mcp_tools/server_instructions.py` (`NEXPLANE_SERVER_INSTRUCTIONS`) to document the reference scan workflow pattern:

> Reference scan (hardcoded refs / connection strings / hostnames) — `scan_for_references(migrating_asset_id, search_terms)` fans out to all connected surfaces and registers consumers; `get_scan_results(scan_cr_id)` returns confident update CRs (approve to execute) and exceptions (resolve via `resolve_reference_exception`, `reattempt_reference_triage`, or `dismiss_reference_exception`).

---

## 12. Findings Integration

Finding type: `reference_not_updated` (add to Finding type enum / discriminator).

Finding body fields:
- `scan_cr_id` — links back to the originating scan
- `exception_id` — links to the ScanException record
- `consumer_asset_id` — the asset with the unresolved reference
- `location` — exact file path, ARN, or K8s resource path
- `matched_term` — the search term that was found
- `ai_reason` — the triage explanation for why it was an exception

Severity:
- `high` — old endpoint is being decommissioned and consumer is in production
- `medium` — old endpoint is being decommissioned; consumer environment unknown
- `low` — old endpoint remains reachable post-migration (reference is stale but not broken)

Finding resolution: dismissing or resolving the parent `ScanException` auto-resolves the linked finding.

---

## 13. Future: Identity Resolution Extensions

Deferred — capture for backlog, not in scope for this implementation plan:

- **Application profiling correlation** — cross-reference scan results against `application_profile` assets; if a discovered consumer has an existing profile, use the profile's observed outbound connections to confirm the reference is live traffic vs. dead config
- **CI/CD pipeline target tracking** — match discovered consumers against GitHub Actions deployment targets, build artifact provenance, and pipeline environment variable sets to establish build-time vs. runtime reference distinction
- **RunZero integration** — use RunZero network discovery scan data as an additional fingerprinting signal for identity resolution (MAC address, OS fingerprint, open ports, service banners)
- **Crashoverride SLASH** — ELF format wrapping technology; as assets deploy through environments, the SLASH wrapper embeds a stable identity token that survives hostname and IP changes; use this token as a Tier 1 identity signal when present

---

## 14. Testing Requirements

Every scan and update executor requires a live smoke phase per the smoke-test-driven-development principle. Mocks are not acceptable.

**Smoke phases required:**

- `REF_SCAN_AWS` — spin up a Lambda function with a hardcoded search term in its env var; run scan CR; verify hit returned; verify consumer auto-registered as asset with dependency edge
- `REF_SCAN_K8S` — deploy a ConfigMap with a hardcoded search term; run scan CR; verify hit returned
- `REF_SCAN_AGENT` — write a config file with a hardcoded search term to an agent-monitored host; run scan CR; verify hit returned
- `REF_UPDATE_LAMBDA` — run update CR on Lambda hit; verify env var changed; verify rollback restores old value
- `REF_UPDATE_K8S_CONFIGMAP` — run update CR on ConfigMap hit; verify value changed; verify rollback restores old value
- `REF_UPDATE_HOST_FILE` — run update CR on agent host file hit; verify file changed; verify rollback restores backup
- `REF_TRIAGE` — verify AI triage splits a mixed result set into confident and exception buckets
- `REF_EXCEPTION_RESOLVE` — verify each resolution path produces correct ScanException status and (for Path 1) a draftable update CR
- `REF_FINDING` — verify unresolved exceptions at execution time produce `reference_not_updated` findings
- `REF_MCP_PARITY` — drive the complete workflow (scan → triage → resolve exceptions → approve update CRs → verify) exclusively via MCP tools
