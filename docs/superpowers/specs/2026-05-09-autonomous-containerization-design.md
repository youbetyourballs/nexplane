# Autonomous Containerization CR Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A single `agent_containerize_auto` CR that runs preflight discovery, AI-powered migration planning, build, deploy, soak verification, and auto-spawns a retirement CR -- requiring human approval only at stateful classification confirmation and at the irreversible retirement step.

**Architecture:** New CR type orchestrating 7 execution stages within one CR. AI analysis runs as an execution stage (not at planning time) feeding a structured migration plan to the build stage. A new `deep_discover` Go agent command handles host-local data collection; fleet cross-reference is backend-side. The agent binary is rebuilt and uploaded to S3 as part of this work.

**Tech Stack:** Go agent (new `deepdiscover` package, Linux + Windows), Python executor (`agent_containerize_auto.py`), FastAPI CR lifecycle, existing `ai_service.py`, React frontend (CR detail AI badge, asset detail "Migrate" button), PostgreSQL (asset_metadata JSON extensions).

---

## Prerequisite Check

The manual pipeline (appdiscovery, containerize_build, k8s_workload_deploy, containerize_retire) is fully implemented and smoke-tested (phases X, Y, Z pass). This design builds on top of it.

---

## 1. New Agent Command: `deep_discover`

### Package layout

```
agent/commands/deepdiscover/
  deepdiscover.go          # shared types, Execute() dispatcher, registered as "deep_discover"
  deepdiscover_linux.go    # Linux implementation
  deepdiscover_windows.go  # Windows implementation
```

### Shared types (`deepdiscover.go`)

```go
type RuntimeType string

const (
    RuntimeSystemd     RuntimeType = "systemd"
    RuntimeDocker      RuntimeType = "docker"
    RuntimeContainerd  RuntimeType = "containerd"
    RuntimePodman      RuntimeType = "podman"
    RuntimeKubePod     RuntimeType = "kubernetes_pod"
    RuntimeProcessOnly RuntimeType = "process_only"
)

type DiscoveredWorkload struct {
    Name            string      `json:"name"`
    RuntimeType     RuntimeType `json:"runtime_type"`
    Pid             int         `json:"pid,omitempty"`
    SystemdUnit     string      `json:"systemd_unit,omitempty"`
    ContainerID     string      `json:"container_id,omitempty"`
    ImageName       string      `json:"image_name,omitempty"`
    PodName         string      `json:"pod_name,omitempty"`
    ListeningPorts  []PortEntry `json:"listening_ports"`
    OutboundConns   []ConnEdge  `json:"outbound_connections"`
    InboundConns    []ConnEdge  `json:"inbound_connections"`
    IPCSockets      []string    `json:"ipc_sockets"`
    DataDirectories []DirInfo   `json:"data_directories"`
    EnvVars         []string    `json:"env_vars"`
    Dependencies    []string    `json:"dependencies"` // systemd or container links
}

type PortEntry struct {
    Port     int    `json:"port"`
    Protocol string `json:"protocol"`
    Address  string `json:"address"`
}

type ConnEdge struct {
    LocalAddr  string `json:"local_addr"`
    RemoteAddr string `json:"remote_addr"`
    RemoteHost string `json:"remote_host,omitempty"` // reverse-DNS best-effort
    State      string `json:"state"`
    Protocol   string `json:"protocol"`
}

type DirInfo struct {
    Path        string `json:"path"`
    SizeBytes   int64  `json:"size_bytes"`
    FileCount   int    `json:"file_count"`
}

type DeepDiscoveryResult struct {
    Workloads       []DiscoveredWorkload `json:"workloads"`
    HybridEdges     []HybridEdge         `json:"hybrid_edges"` // cross-runtime connections
    CollectedAt     string               `json:"collected_at"`
    OS              string               `json:"os"`
}

type HybridEdge struct {
    SourceName    string `json:"source_name"`
    SourceRuntime string `json:"source_runtime"`
    TargetName    string `json:"target_name"`
    TargetRuntime string `json:"target_runtime"`
    Port          int    `json:"port"`
    Protocol      string `json:"protocol"`
}
```

### Linux implementation (`deepdiscover_linux.go`)

Data sources in collection order:

1. **Listening ports:** `ss -tulnp` -- maps port to process name and PID
2. **Established connections:** `ss -tunap state established` -- produces ConnEdge list
3. **Container runtimes (if present):**
   - `docker ps --format '{{json .}}'` (Docker)
   - `crictl ps -o json` (containerd via CRI)
   - `podman ps --format json` (Podman)
4. **Kubernetes pods on node:** `kubectl get pods --all-namespaces -o json` if kubelet socket present at `/var/run/kubelet.sock` or `/run/containerd/containerd.sock`
5. **Systemd units:** `systemctl list-units --type=service --state=running --output=json`
6. **Systemd dependencies per service:** `systemctl list-dependencies {unit} --plain`
7. **IPC sockets:** `lsof -U` for Unix domain sockets
8. **Data directories:** walk `DataDirectories` from existing appdiscovery output; compute size with `du -sb`
9. **Hybrid edges:** cross-reference listening ports of container workloads against established connections of systemd workloads and vice versa

Reverse-DNS lookup on remote addresses is best-effort with a 500ms timeout per address; failure is non-fatal.

### Windows implementation (`deepdiscover_windows.go`)

Data sources:

1. **Listening ports + connections:** `Get-NetTCPConnection | ConvertTo-Json` and `Get-NetUDPEndpoint | ConvertTo-Json`
2. **Process mapping:** `Get-Process | Select-Object Id,Name,Path | ConvertTo-Json`
3. **Windows services:** `Get-Service | Where-Object {$_.Status -eq 'Running'} | ConvertTo-Json`
4. **Container runtimes (if present):** `docker ps --format '{{json .}}'` if Docker Desktop installed
5. **Service dependencies:** `(Get-Service {name}).DependentServices | ConvertTo-Json`
6. **Data directories:** from appdiscovery output, `(Get-Item {path}).Length` for each

### Registration

In `agent/executor/executor.go`:
```go
import "nexplane-agent/commands/deepdiscover"

// In commands map:
"deep_discover": deepdiscover.Execute,
```

---

## 2. New Backend Executor: `agent_containerize_auto`

### File: `backend/app/connectors/executors/nexplane_agent/containerize_auto.py`

### Execution pipeline

The executor runs 7 stages sequentially, storing results in `step_results` under named keys. Each stage is retryable independently.

```
Stage 1: preflight_discovery    -> calls deep_discover agent job
Stage 2: fleet_cross_reference  -> backend-side graph assembly
Stage 3: ai_analysis            -> ai_service.chat() structured call
Stage 4: [stateful_gate]        -> pauses if any unit is stateful; resumes on approval
Stage 5: build                  -> per migration unit: containerize_build agent job
Stage 6: deploy                 -> per migration unit: k8s_workload_deploy
Stage 7: soak_verify            -> health probe loop with configurable soak window
```

On soak failure: all deployed units are rolled back via `k8s_workload_deploy` rollback, CR status set to `failed` with structured error report.

On soak success: a new `agent_containerize_retire` CR is auto-spawned targeting the source asset, linked via `source_cr_id`, status `awaiting_approval`.

### Stage 1: preflight_discovery

Dispatches `deep_discover` agent job to the source asset's registered agent. Returns `DeepDiscoveryResult` JSON.

### Stage 2: fleet_cross_reference

Backend-only -- no agent call. Walks `GET /assets` for the org, matches remote addresses in `OutboundConns` and `InboundConns` against known asset IP addresses. Produces a graph of:
- `nodes`: all discovered workloads (source host + matched Nexplane assets)
- `edges`: directed dependency edges with port/protocol
- `hybrid_edges`: cross-runtime connections (systemd service -> docker container etc.)

### Stage 3: ai_analysis

Calls `ai_service.chat()` with a structured prompt. Input: serialized dependency graph. Output: validated JSON parsed from response.

**System prompt excerpt:**
```
You are analyzing a dependency graph of workloads running on a host to determine
the optimal containerization strategy. Return ONLY valid JSON matching the schema
provided. Do not include explanation text outside the JSON.
```

**Output schema:**
```json
{
  "migration_units": [
    {
      "id": "unit-1",
      "name": "nginx-flask-api",
      "apps": ["nginx", "flask-api"],
      "pattern": "modular",
      "stateful": false,
      "data_risk": "none",
      "soak_seconds_recommended": 120,
      "reasoning": "Two loosely coupled services communicating over localhost:80..."
    }
  ],
  "migration_order": ["unit-1", "unit-2"],
  "warnings": []
}
```

**Stateless criteria (autonomous):** no data directories with files, no persistent socket connections to external databases, no stateful runtime type.

**Stateful criteria (confirmation gate):** any of -- data directories with content, active connections to databases (port 5432/3306/1433/6379), PVC mounts on existing containers, SQLite/LevelDB file handles.

### Stage 4: stateful_gate

If any migration unit has `"stateful": true`, the CR enters a `pending_stateful_confirmation` sub-state. The CR is not failed or paused in the traditional sense -- it polls for a `stateful_approval` flag on the CR's `desired_outcome` (set by the operator via the UI). Once set, execution resumes from Stage 5.

Timeout: 24 hours. After timeout the CR fails with `stateful_gate_expired`.

### Stage 5: build

For each migration unit in `migration_order`: dispatches `containerize_build` agent job with the app profiles from discovery. Stores image tag and manifests in `step_results["build"][unit_id]`.

### Stage 6: deploy

For each migration unit: dispatches `k8s_workload_deploy` backend executor. Stores pod names and service URLs in `step_results["deploy"][unit_id]`.

### Stage 7: soak_verify

Runs a probe loop for `soak_seconds` (default 120, recommended value from AI analysis, max 600).

Every 10 seconds:
- HTTP GET to each deployed service's health endpoint (or root URL)
- Check that each known downstream caller (from fleet_cross_reference) can still reach its dependencies -- verified by probing the original service URLs

If all probes pass for the full soak window: success.
If any probe fails and the window expires: rollback all deployed units, fail the CR.

Probe results stored in `step_results["soak_verify"]` per unit.

### Retirement CR auto-spawn

On soak success, the executor calls `POST /change-requests` internally to create:
```json
{
  "change_type": "agent_containerize_retire",
  "target_asset_ids": [source_asset_id],
  "desired_outcome": {
    "source_cr_id": "<parent_cr_id>",
    "migration_units": [...],
    "auto_spawned": true
  }
}
```
The retirement CR is immediately submitted for approval and surfaced in the UI.

---

## 3. Change Type Definition

### `backend/app/connectors/change_type_definitions/agent_containerize_auto.json`

```json
{
  "change_type": "agent_containerize_auto",
  "display_name": "Autonomous Containerization",
  "description": "AI-directed end-to-end migration from legacy services to Kubernetes. Discovers workloads, maps dependencies, builds and deploys containers, verifies with a soak window, and queues retirement for human approval.",
  "uses_ai": true,
  "rollback_supported": true,
  "preflight_checks": ["asset_exists", "agent_registered", "ai_provider_configured", "kubernetes_connector_configured"],
  "steps": [
    {"action": "preflight_discovery",   "purpose": "execute", "required": true},
    {"action": "fleet_cross_reference", "purpose": "execute", "required": true},
    {"action": "ai_analysis",           "purpose": "execute", "required": true},
    {"action": "build",                 "purpose": "execute", "required": true},
    {"action": "deploy",                "purpose": "execute", "required": true},
    {"action": "soak_verify",           "purpose": "verify",  "required": true}
  ],
  "parameters": {
    "registry":           {"type": "string",  "required": true},
    "target_cluster_id":  {"type": "string",  "required": true},
    "namespace":          {"type": "string",  "required": false, "default": "nexplane-migrations"},
    "soak_seconds":       {"type": "integer", "required": false, "default": 120},
    "dry_run":            {"type": "boolean", "required": false, "default": false}
  }
}
```

### Model registration

In `backend/app/models/change_request.py`, add to the `ChangeType` enum:
```python
agent_containerize_auto = "agent_containerize_auto"
```

---

## 4. Asset Metadata Extensions

On the source server asset after `preflight_discovery` completes:
```json
{
  "containerization_status": "discovering | ai_planning | pending_stateful_confirmation | building | deploying | soaking | pending_retirement | retired",
  "migration_units": [
    {
      "id": "unit-1",
      "name": "nginx-flask-api",
      "apps": ["nginx", "flask-api"],
      "pattern": "modular",
      "stateful": false,
      "image_tag": "registry.example.com/nginx-flask-api:sha256-abc123",
      "k8s_service_url": "http://nginx-flask-api.nexplane-migrations.svc.cluster.local"
    }
  ],
  "dependency_graph": {
    "nodes": [],
    "edges": [],
    "hybrid_edges": []
  }
}
```

Status is updated by the executor at each stage transition.

---

## 5. UI Changes

### `CreateChangeRequest.tsx`

- `agent_containerize_auto` entry in `CHANGE_TYPE_META` with description referencing AI requirement
- Change type definition reader checks `uses_ai: true` and renders an `AI-powered` badge inline with the change type label
- This badge pattern is reusable: any future CR type declaring `"uses_ai": true` gets the badge automatically

### `ChangeRequestDetail.tsx`

- Stage result renderer for `ai_analysis` stage: expandable panel showing migration units table (name, pattern, stateful flag, soak recommendation, reasoning text)
- Stateful confirmation gate: when `containerization_status === "pending_stateful_confirmation"`, renders an inline approval card with the AI reasoning and a "Confirm classification and proceed" button. Button calls `POST /change-requests/{id}/confirm-stateful` (new endpoint). The executor polls this flag every 10s during the stateful gate stage.
- `ai_analysis` stage indicator gets an AI badge (small sparkle icon) alongside the existing stage status icon

### `AssetDetail.tsx`

- Server and endpoint assets with `containerization_status` in `asset_metadata` show a "Containerization Status" row in the Network section
- "Migrate to Kubernetes" quick action button pre-fills `CreateChangeRequest` with `changeType=agent_containerize_auto` and `assetId` set

### `types/api.ts`

- Add `"agent_containerize_auto"` to `ChangeType` union
- Add `"deep_discover"` to agent command types

---

## 6. Smoke Test: Phase AUTO

### Test apps

**Stateless (Linux): nginx + Flask sidecar**

Installed via SSM on the smoke EC2 instance:
```bash
yum install -y nginx python3-pip
pip3 install flask
# Flask app at :5000 proxies requests to nginx at :80
# Write-up as nexplane-smoke-flask.service and use nginx systemd unit
```
Expected AI classification: `stateless`, `modular` (2 containers), autonomous proceed.

**Stateful (Linux): PostgreSQL + writer service**

```bash
yum install -y postgresql15-server python3
postgresql-setup --initdb
systemctl start postgresql
# Python writer service connects to PostgreSQL, inserts a row every 5 seconds
# Registered as nexplane-smoke-writer.service
```
Expected AI classification: `stateful`, confirmation gate fires.

**Stateless (Windows): IIS + Python HTTP sidecar**

```powershell
Install-WindowsFeature -Name Web-Server
pip install flask
# Flask at :5000 calls IIS at :80
# Registered as NSSM services
```
Expected AI classification: `stateless`, `modular`, autonomous proceed.

**Stateful (Windows): SQL Server Express + writer**

```powershell
# Install SQL Server Express via Chocolatey or direct MSI
# Python writer service with pyodbc connecting to localhost:1433
```
Expected AI classification: `stateful`, confirmation gate fires.

### Phase AUTO test sequence (Linux, in `test_aws_live.py`)

1. Install nginx + Flask + PostgreSQL + writer via SSM
2. Register all four as systemd services, start them
3. Fire `agent_containerize_auto` CR targeting the agent server asset
4. Assert Stage 1 (`preflight_discovery`) completes: workloads list contains all 4 services, hybrid_edges present, `runtime_type` values correct
5. Assert Stage 2 (`fleet_cross_reference`) completes: dependency_graph nodes include the EC2 asset
6. Assert Stage 3 (`ai_analysis`) completes: migration_units contains 2 units (nginx+Flask, PostgreSQL+writer); stateful flags match
7. Assert `containerization_status` transitions to `pending_stateful_confirmation`
8. Auto-approve the stateful gate (smoke test has operator role): `POST /change-requests/{id}/confirm-stateful`
9. Assert Stage 5 (`build`) completes for both units (dry_run=True in smoke -- no Docker daemon needed)
10. Assert Stage 6 (`deploy`) completes (dry_run=True)
11. Assert Stage 7 (`soak_verify`) completes: all probes pass
12. Assert retirement CR auto-spawned, status `awaiting_approval`
13. Auto-approve retirement CR
14. Assert `containerization_status === "retired"` on asset metadata
15. Cleanup: rollback all CRs, stop and remove test services

### Windows phase AUTO (in `test_agent_live.py`, new `containerize` phase)

Mirrors Linux sequence with IIS + Flask (stateless) and SQL Server Express + writer (stateful), using PowerShell SSM for service installation and verification.

### Phase registration

Add `AUTO` to `test_aws_live.py` `--phases` help text and dispatch block. Add to `smoke_tests.py` slow_phases for the AWS suite (Windows track adds ~20 min).

---

## 7. Backlog Item: Traffic Tuple Storage

The `deep_discover` command will produce potentially thousands of connection tuples per host (src_ip, src_port, dst_ip, dst_port, protocol, state). Storing raw tuples in `asset_metadata` JSON is suitable for single-host discovery but will not scale to fleet-wide continuous collection.

Future design questions:
- Should connection tuples be stored in a dedicated `traffic_observations` table (time-series style) rather than asset_metadata JSON?
- What aggregation reduces storage while preserving actionable signal (e.g., unique (src_asset, dst_asset, port) edges rather than per-connection tuples)?
- How long should observations be retained? Are they useful beyond the migration window?
- Could this data feed other use cases: anomaly detection, blast-radius analysis for firewall changes, microsegmentation policy generation?

This requires its own brainstorming session before implementation.

---

## 8. Error Handling

| Failure point | Behavior |
|---------------|----------|
| deep_discover times out | CR fails at Stage 1 with `discovery_timeout`; no build started |
| AI provider not configured | Preflight check `ai_provider_configured` blocks plan generation; UI shows inline guidance |
| AI returns unparseable JSON | Stage 3 retried once; on second failure CR fails with `ai_analysis_error` and raw response attached |
| Build fails for one unit | Other units not started; CR fails with per-unit error report; no deploy attempted |
| Deploy fails for one unit | Deployed units rolled back; CR fails |
| Soak probe fails within window | Probes continue until window expires; if still failing, all units rolled back |
| Stateful gate expires (24h) | CR fails with `stateful_gate_expired`; no build started |
| Retirement CR rejected | Source CR marked `retirement_rejected`; containerized services continue running; operator must manually decide |

---

## 9. Testing

- Unit tests for `deepdiscover_linux.go` and `deepdiscover_windows.go` using mock exec functions (pattern from existing `changip` tests)
- Unit tests for `containerize_auto.py` executor mocking agent job dispatch and AI service
- `test_database_admin.py` pattern: verify change type definition JSON is valid, preflight checks registered, rollback type registered
- Smoke test Phase AUTO (Linux): as described in Section 6
- Smoke test Windows containerize phase (in `test_agent_live.py`): as described in Section 6
