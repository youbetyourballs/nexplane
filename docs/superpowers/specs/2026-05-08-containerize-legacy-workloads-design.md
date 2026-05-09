# Containerize Legacy Workloads — Design Spec

## Goal

Enable Nexplane to migrate applications from legacy OS hosts (EOL Ubuntu, CentOS 7, etc.) into containers running on managed Kubernetes clusters (EKS, GKE, AKS) or local Docker environments. The workflow covers stateless and stateful workloads, is orchestrated through a 5-step project wizard, and is validated through an explicit smoke test step before the legacy host is retired.

---

## Background

The `agent_linuxupgrade` change type and Go agent already have a `containerize` path defined, but all executors are stubs returning mock responses. The inplace upgrade path is fully implemented. This design builds on the existing stub scaffold.

---

## Architecture

Four linked Change Requests form a migration project, surfaced through a 5-step wizard (the smoke test is a distinct wizard step, not buried in the retire CR):

```
CR 1: agent_appdiscovery       → discovers apps, writes asset_metadata.applications[]
CR 2: agent_containerize_build → generates Dockerfile + k8s manifests, builds, pushes to registry
CR 3: k8s_workload_deploy      → provisions PVCs, migrates data (stateful), applies manifests
       [Wizard Step 4: live smoke test visible to operator before proceeding]
CR 4: agent_containerize_retire → retires legacy service; auto-rollbacks if smoke test failed
```

CR 3 contains an intentionally open field (`target_cluster: null`) that the operator fills in during the wizard. CRs 2–4 are blocked from execution until CR 1 completes. CR 4 is blocked until the wizard smoke test step passes.

---

## New Connector Type: `kubernetes`

A Kubernetes cluster is a first-class Nexplane connector, storing:
- `endpoint`: cluster API server URL
- `kubeconfig`: base64-encoded kubeconfig (or service account token + CA cert)
- `default_namespace`: target namespace for deployed workloads
- `registry_credentials`: auth for pushing/pulling images (ECR, GCR, ACR, or generic)

When registered, the cluster appears in inventory as a `kubernetes_cluster` asset. Works for:
- **EKS / GKE / AKS** — kubeconfig generated from cloud connector or provided manually
- **Bare metal k8s** — manually provided kubeconfig
- **Local Docker Desktop / kind / minikube** — kubeconfig pointing at localhost

---

## New Asset Types

| Asset Type | Description |
|---|---|
| `kubernetes_cluster` | A registered Kubernetes cluster (created when kubernetes connector registered) |
| `container_image` | A pushed container image in a registry (created by CR 2) |

---

## Data Model: `asset_metadata.applications[]`

Populated by CR 1, persists permanently on the asset. Each discovered application:

```json
{
  "id": "uuid",
  "name": "myapp",
  "binary": "/usr/local/bin/myapp",
  "systemd_unit": "myapp.service",
  "listening_ports": [{"port": 8080, "protocol": "tcp"}],
  "config_files": ["/etc/myapp/config.yaml"],
  "data_directories": ["/var/lib/myapp/data"],
  "estimated_data_size_gb": 4.2,
  "stateful": true,
  "external_data_stores": ["postgresql://prod-db-01:5432/myapp"],
  "process_user": "myapp",
  "env_vars": ["DATABASE_URL", "APP_SECRET"],
  "dependencies": ["libssl", "libpq"],
  "containerization_status": "not_started",
  "image_digest": null,
  "pvc_spec": null,
  "k8s_manifest": null
}
```

`containerization_status` progresses: `not_started` → `dockerfile_generated` → `image_pushed` → `deployed` → `verified` → `retired`

---

## Four New Change Types

### `agent_appdiscovery`

Runs on the legacy host via Nexplane agent. Scans:
- `systemctl list-units --type=service --state=running`
- `ps aux` for non-systemd processes
- `lsof -i` for listening ports → bound process mapping
- `dpkg -l` / `rpm -qa` for package-managed software
- `/opt`, `/usr/local/bin`, `/usr/local/sbin`, `/home/*/bin` for non-package apps
- Environment files (`/etc/environment`, `/etc/default/*`, systemd unit `EnvironmentFile=`)
- Data directories inferred from open file handles and config file parsing

Writes structured `applications[]` JSON to `asset_metadata` via the control plane API.

**Rollback:** No side effects — read-only scan.

---

### `agent_containerize_build`

Runs on the legacy host via Nexplane agent. For each selected application:

1. Reads `asset_metadata.applications[id]`
2. Generates Dockerfile: base image from detected OS family, copies binary + config, sets `USER`, `EXPOSE`, `CMD`
3. For stateful apps: generates PVC spec (storage class, access mode, size from `estimated_data_size_gb` + 20% headroom) and rsync migration script
4. Generates Kubernetes manifests: `Deployment`, `Service`, `PersistentVolumeClaim` (stateful only), `ConfigMap` for env vars
5. Runs `docker build` on the legacy host
6. Pushes to registry using connector credentials (ECR via AWS SDK, GCR/ACR via connector auth)
7. Stores image digest and manifests back on `asset_metadata.applications[id]`

**Rollback:** Delete pushed image from registry.

---

### `k8s_workload_deploy`

Runs from the backend using the `kubernetes` connector kubeconfig. Open field: `target_cluster` (must be filled by operator in wizard).

Steps:
1. **Pre-flight:** verify cluster reachable, namespace exists, registry credentials accessible from cluster (ImagePullSecret)
2. **Stateful pre-deploy:** provision PVC, mount it to a temporary migration pod, rsync data from legacy host into PV using the generated rsync script, verify checksums
3. **Apply:** `kubectl apply` Deployment + Service + ConfigMap (+ PVC if stateful)
4. **Wait:** poll until all pods are `Running` + `Ready` (configurable timeout, default 10 min)
5. **Register:** create `kubernetes_workload` asset in Nexplane inventory linked to the `kubernetes_cluster` asset

**Rollback:** `kubectl delete` all applied manifests; for stateful, PVC is retained (not deleted) for safety — operator must manually clean up.

---

### `agent_containerize_retire`

Runs after operator approves the wizard smoke test step. Two sub-phases:

**Verify (automated, shown in wizard):**
- HTTP probe: compare response from legacy service port vs new container port
- Port reachability check: old host port → new service IP
- For stateful: spot-check key files/records accessible via PV
- Configurable soak period (default: 30 minutes of clean checks before proceeding)

**Retire (triggered by operator after smoke test approval):**
- `systemctl stop <unit>` + `systemctl disable <unit>` on legacy host
- Snapshot the legacy host (EC2 snapshot, GCE disk snapshot, Azure disk snapshot) before any destructive action
- Mark legacy asset `status=retired` in Nexplane inventory
- Update `containerization_status` → `retired` on all migrated applications

**Auto-rollback trigger:** If any health check fails during the soak period, automatically: `systemctl start <unit>` on legacy host, delete the Kubernetes workload, alert operator.

---

## Migration Wizard — 5 Steps

Opened from the asset detail "Applications" tab or the vulnerability remediation "Containerize & Migrate" option.

### Step 1 — Discover
- Select which applications to migrate (checkbox list; defaults to all non-system services)
- "Run Discovery" fires CR 1
- Results populate the Applications tab; stateful apps flagged with data size

### Step 2 — Build & Push
- Review generated Dockerfile per app (expandable preview)
- Configure registry: pick from registered connectors (ECR/GCR/ACR) or enter custom
- "Build & Push" fires CR 2
- Shows build progress streamed from agent logs

### Step 3 — Deploy
- **Target cluster required:** dropdown of all registered `kubernetes_cluster` assets. Blocked until filled.
- Review generated k8s manifests (expandable preview)
- For stateful: shows data migration plan (size, estimated time)
- Configure namespace, resource limits (optional)
- "Deploy" fires CR 3; shows pod status live

### Step 4 — Smoke Test *(wizard-visible)*
- Live health check results table: check name, status (✅/⏳/❌), last checked
- HTTP probe result: response code, latency comparison (old vs new)
- Port reachability: old service vs new service
- Stateful: data integrity spot-check results
- Soak period countdown timer (operator can reduce/waive for non-prod)
- "Approve & Retire" button only enabled when all checks green

### Step 5 — Retire
- Summary: what will be stopped, what snapshot will be taken
- "Retire Legacy Host" fires CR 4
- Shows: snapshot created → service stopped → asset marked retired

---

## UI Changes

### Modified: `frontend/src/pages/AssetDetail.tsx`
New "Applications" tab showing:
- Discovered applications list with stateful/stateless badge, port, status
- "Re-scan" button (fires CR 1 solo)
- "Containerize selected" button (opens migration wizard for selected apps)
- Empty state: "No applications discovered — run a scan to detect running services"

### Modified: `frontend/src/pages/VulnerabilityRemediation.tsx`
In the CVE blast radius view, when affected assets run EOL OS (detected from `asset_metadata.os`):
- Show "Containerize & Migrate" card alongside "Generate Patch Campaign"
- Card shows: N apps discovered (if already scanned), link to open wizard

### New: `frontend/src/components/ContainerizationWizard.tsx`
5-step wizard modal. Uses `useMutation` for each CR fire. Streams agent logs in Steps 2–3. Live polling for smoke test results in Step 4.

---

## Backend Changes

### New Files
| File | Purpose |
|---|---|
| `backend/app/connectors/executors/nexplane_agent/app_discovery.py` | Executor stub for `agent_appdiscovery` |
| `backend/app/connectors/executors/nexplane_agent/containerize_build.py` | Executor stub for `agent_containerize_build` |
| `backend/app/connectors/executors/kubernetes/workload_deploy.py` | Executor for `k8s_workload_deploy` (kubectl via kubeconfig) |
| `backend/app/connectors/executors/nexplane_agent/containerize_retire.py` | Executor stub for `agent_containerize_retire` |
| `backend/app/connectors/change_type_definitions/agent_appdiscovery.json` | Change type definition |
| `backend/app/connectors/change_type_definitions/agent_containerize_build.json` | Change type definition |
| `backend/app/connectors/change_type_definitions/k8s_workload_deploy.json` | Change type definition |
| `backend/app/connectors/change_type_definitions/agent_containerize_retire.json` | Change type definition |

### Modified Files
| File | Change |
|---|---|
| `backend/app/models/change_request.py` | Add 4 new `ChangeType` enum values |
| `backend/app/models/asset.py` | Add `kubernetes_cluster`, `container_image` to `AssetType` enum |
| `backend/app/routers/connectors.py` | Register `kubernetes` connector type |
| `backend/app/services/safety_engine.py` | Add new change types to `_IMPLICIT_ROLLBACK_TYPES` |

---

## Agent (Go) Changes

### New command: `appdiscovery`
- `agent/commands/appdiscovery/appdiscovery.go` — orchestrates all scans
- Runs: systemctl, ps, lsof, dpkg/rpm, filesystem walk for non-package binaries
- Calls control plane API to write `asset_metadata.applications[]`
- Returns structured JSON with discovered applications

### Extend: `linuxupgrade` `containerize` path
- `agent/commands/linuxupgrade/containerize.go` — new file implementing the real containerize path
- Reads app profile from control plane
- Generates Dockerfile using template engine
- Runs `docker build` + `docker push` with registry auth injected via agent job parameters
- Generates PVC spec and rsync script for stateful apps
- Reports progress back to control plane step-by-step

---

## Smoke Tests

New AWS smoke test phase (e.g., Phase X) in `backend/tests/smoke/test_aws_live.py`:

**Setup:** Launch EC2 instance with:
- A simple stateless HTTP service (`python3 -m http.server 8080`) as a systemd unit
- A stateful file-writing worker (writes timestamped files to `/var/lib/testworker/data/`)

**Register** a local kind/k3s cluster (or Docker Desktop k8s) as the test kubernetes connector; ECR as the registry.

**Phase X steps:**
1. Fire `agent_appdiscovery` CR → assert `asset_metadata.applications` has both services, stateful flag correct on worker
2. Fire `agent_containerize_build` CR → assert image pushed to ECR, manifest artifacts stored on asset
3. Fire `k8s_workload_deploy` CR targeting test cluster → assert pods Running, PVC bound, data migrated
4. Run wizard smoke test checks (HTTP probe, port check, data integrity) → assert all pass
5. Fire `agent_containerize_retire` CR → assert legacy systemd units stopped, assets marked retired

**Rollback test:** inject a health check failure in step 4 → assert legacy service automatically restarted, k8s workload deleted, asset status reverted

---

## What Is Not In Scope (v1)

- Windows workloads (Linux only)
- Multi-container applications (one service → one container; compose/helm charts are future work)
- Kubernetes operators or StatefulSets (standard Deployments + PVCs only)
- Automatic target cluster selection by the AI (AI can suggest; human confirms)
- Autonomous mode (that is a separate future design — see backlog)

---

## Future Work (Backlog)

- Autonomous containerization CR (AI-directed, no human approval gates) — see backlog
- Windows container support
- Helm chart generation for multi-service applications
- StatefulSet support for databases
- AI-assisted target cluster recommendation based on workload profile and cluster capacity
