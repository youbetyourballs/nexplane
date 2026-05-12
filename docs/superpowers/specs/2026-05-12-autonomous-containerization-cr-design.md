# Autonomous Containerization CR — Design Spec

**Date:** 2026-05-12

## Overview

The `agent_containerize_auto` CR and its 7-stage backend orchestration are already implemented. This spec covers the three remaining gaps needed to make the feature fully operational:

1. **`deep_discover` Go agent command** — self-contained host enrichment (outbound connections, env vars, open FDs, runtime deps, config-file intelligence, cross-platform)
2. **Backend wiring** — `deep_discover` dispatch executor + confirm-stateful endpoint
3. **UI** — "Migrate" button on asset detail, 7-stage stepper in CR detail, stateful gate confirmation banner

---

## Background: What Already Exists

The backend executor `backend/app/connectors/executors/nexplane_agent/containerize_auto.py` implements the full autonomous pipeline:

1. `preflight_discovery` — calls `deep_discover` agent command
2. `fleet_cross_reference` — maps outbound IPs to Nexplane asset inventory
3. `ai_analysis` — Anthropic/OpenAI classifies workloads (stateless/stateful/monolith/modular)
4. `stateful_gate` — polls `change_request.stateful_approved_at` every 10s (24h timeout)
5. `build` — calls `containerize_build` executor per migration unit
6. `deploy` — calls `k8s_workload_deploy` executor per migration unit
7. `soak_verify` — HTTP health-probes all deployed services, auto-rollbacks on failure; on success auto-spawns a `agent_containerize_retire` CR (status `draft`, `RiskLevel.high`)

The `agent_containerize_auto` change type, JSON definition, unit tests, and Phase AUTO smoke test all exist. No changes to the executor itself are needed.

---

## Task 1: `deep_discover` Agent Command

### Design

A new Go command at `agent/commands/deepdiscover/` with a platform split:

- `deepdiscover.go` — output types + config-file parsing (shared)
- `deepdiscover_linux.go` — Linux data collection
- `deepdiscover_windows.go` — Windows data collection

The command is self-contained: it re-runs service enumeration (does not depend on a prior `discover_applications` run) and enriches each service with the following additional data:

### Per-Service Enrichment Fields

| Field | Linux source | Windows source |
|---|---|---|
| `outbound_connections` | `ss -tnp state established`, mapped by PID | `netstat -ano`, mapped by PID |
| `env_vars` | `/proc/{pid}/environ` (names only, no values) | `Get-Process` env block (names only) |
| `open_files` | `/proc/{pid}/fd` symlinks → regular files only | WMI `CIM_DataFile` handle associations |
| `runtime_deps` | `ldd /path/to/binary` → `.so` paths | `Get-Process.Modules` → DLL paths |
| `process_user` | `/proc/{pid}/status` → Uid, resolved via `/etc/passwd` | Process owner from WMI |
| `pid_found` | bool — false if service has no running process | same |
| `config_intelligence` | Parsed from config files (see below) | same |

### Config-File Intelligence

For each config file discovered, `deep_discover` does a lightweight parse:

- **nginx / Apache** — extracts `server_name`/`ServerName` (vhosts), `proxy_pass`/`ProxyPass` (upstreams), `root`/`DocumentRoot`
- **IIS** — site bindings and app pool names from `applicationHost.config`
- **Generic `.conf` / `.ini` / `.env`** — key names only (no values, to avoid capturing secrets)

This reveals service identity and routing dependencies (e.g. a vhost `payments.internal` pointing upstream to `10.0.1.45:8080`) that don't appear in process or port data alone.

### Output Schema

```go
type Workload struct {
    // From discover_applications (re-collected)
    ID              string        `json:"id"`
    Name            string        `json:"name"`
    Binary          string        `json:"binary"`
    SystemdUnit     string        `json:"systemd_unit"`
    ListeningPorts  []PortBinding `json:"listening_ports"`
    ConfigFiles     []string      `json:"config_files"`
    DataDirectories []string      `json:"data_directories"`
    EstimatedDataGB float64       `json:"estimated_data_size_gb"`
    Stateful        bool          `json:"stateful"`
    ProcessUser     string        `json:"process_user"`

    // Deep enrichment
    PIDFound            bool                  `json:"pid_found"`
    OutboundConnections []Connection          `json:"outbound_connections"`
    EnvVarNames         []string              `json:"env_var_names"`
    OpenFiles           []string              `json:"open_files"`
    RuntimeDeps         []string              `json:"runtime_deps"`
    ConfigIntelligence  []ConfigEntry         `json:"config_intelligence"`
}

type Connection struct {
    RemoteIP   string `json:"remote_ip"`
    RemotePort int    `json:"remote_port"`
    Proto      string `json:"proto"`
}

type ConfigEntry struct {
    File   string            `json:"file"`
    Type   string            `json:"type"`   // "nginx", "apache", "iis", "generic"
    Fields map[string]string `json:"fields"` // key → value (vhosts, upstreams, etc.)
}
```

### Registration

Add `"deep_discover": deepdiscover.Execute` to the executor dispatch map in `agent/executor/executor.go`.

---

## Task 2: Backend `deep_discover` Executor

**File:** `backend/app/connectors/executors/nexplane_agent/deep_discover.py`

Thin dispatch shim — same pattern as `app_discovery.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="deep_discover",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
```

No rollback needed — discovery is read-only.

Register the executor in the connector service executor map for the `agent_containerize_auto` change type (the auto executor calls it directly via `dispatch_agent_job`, not through the CR system, so no change_type_definitions entry is needed).

---

## Task 3: Confirm-Stateful Endpoint

### Model

Add to `ChangeRequest` in `backend/app/models/change_request.py`:

```python
stateful_approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

Add a migration for this column.

### Endpoint

```
POST /change-requests/{cr_id}/confirm-stateful
```

- **Auth:** `admin` or `approver` role
- **Validates:** CR exists, belongs to caller's org, `change_type == agent_containerize_auto`, CR status is `executing`
- **Action:** Sets `stateful_approved_at = now()`
- **Returns:** `{"id": cr_id, "stateful_approved_at": iso_timestamp}`
- **Errors:** 404 if not found, 400 if wrong type or not executing

The auto executor's `stateful_gate` stage polls this field and unblocks when it's set.

---

## Task 4: UI — Migrate Button (Asset Detail)

**File:** `frontend/src/components/AssetDetail.tsx` (modify) + `frontend/src/components/MigrateDrawer.tsx` (create)

**Entry point:** Server assets (`asset_type === "server"`) with the `nexplane-agent` tag get a "Migrate" button in their action bar.

**MigrateDrawer fields:**

| Field | Type | Default |
|---|---|---|
| Registry URL | text input | — |
| Target K8s cluster | dropdown (kubernetes_cluster assets) | — |
| Namespace | text input | `nexplane-migrations` |
| Soak duration | slider 10–600s | 120s |
| Dry run | toggle | off |

On submit: `POST /change-requests` with `change_type: "agent_containerize_auto"`, then immediately `POST /change-requests/{id}/execute`. Navigates to the CR detail page on success.

---

## Task 5: UI — Stage Stepper in CR Detail

**File:** `frontend/src/components/ChangeRequestDetail.tsx` (modify) or `frontend/src/components/AutoMigrationProgress.tsx` (create, conditionally rendered)

For CRs with `change_type === "agent_containerize_auto"`, replace the generic execution panel with a 7-stage vertical stepper:

```
● preflight_discovery     ✓ completed
● fleet_cross_reference   ✓ completed
● ai_analysis             ✓ completed  [collapse → migration units list]
● stateful_gate           ⏸ waiting for confirmation   ← BANNER shown here
● build                   ○ pending
● deploy                  ○ pending
● soak_verify             ○ pending
```

Each stage is expandable to show its raw output. The `ai_analysis` stage collapses to a human-readable migration unit summary (app names, pattern, stateful flag, reasoning). Polls every 5 seconds while `status === "executing"`.

Stage status is derived from `execution_runs[0].result.step_results` keyed by stage name.

---

## Task 6: UI — Stateful Gate Confirmation Banner

Rendered inline in the CR detail page when the stepper detects `step_results.stateful_gate.pending === true`.

**Banner content:**

```
⚠ Stateful workloads detected — human review required

The AI identified N workload(s) with persistent state:
  • payments-api  [data_risk: medium]  "Has open /var/lib/payments/state.db and outbound 5432"
  • postgres      [data_risk: high]    "Active database with 2.3 GB data directory"

Containerizing stateful workloads requires careful data migration planning.
Review the workloads above, then confirm to proceed.

[Confirm — proceed with migration]  [Abort]
```

"Confirm" calls `POST /change-requests/{id}/confirm-stateful`. "Abort" calls `POST /change-requests/{id}/rollback`. Both buttons disable while the request is in-flight.

---

## Data Flow Summary

```
User clicks "Migrate" on asset detail
    → MigrateDrawer (registry, cluster, namespace, soak, dry_run)
    → POST /change-requests (agent_containerize_auto)
    → POST /change-requests/{id}/execute
    → Navigate to CR detail

CR detail page (polling every 5s):
    Stage 1: deep_discover    → agent runs full host enrichment
    Stage 2: fleet_cross_ref  → backend maps outbound IPs to asset inventory
    Stage 3: ai_analysis      → AI classifies workloads
    Stage 4: stateful_gate    → if stateful units: banner + block
        User confirms → POST /change-requests/{id}/confirm-stateful → unblocks
    Stage 5: build            → agent generates Dockerfile + manifests + builds image
    Stage 6: deploy           → K8s workload deployed
    Stage 7: soak_verify      → HTTP health probes for soak_seconds
        Success → auto-spawns agent_containerize_retire CR (draft, risk: high)
        Failure → auto-rollback stages 5-7
```

---

## Error Handling

| Failure point | Behavior |
|---|---|
| `deep_discover` agent job times out (120s) | CR fails immediately with step error |
| AI returns invalid JSON | Retry once; on second failure CR fails with `ai_analysis_failed` |
| Build fails (Dockerfile gen or image push) | CR fails; stages 1-4 are read-only, no rollback needed |
| Deploy fails (K8s apply) | Auto-rollback: delete K8s resources (retain PVCs) |
| Soak probe fails | Auto-rollback: delete K8s resources (retain PVCs); original service untouched |
| Stateful gate times out (24h) | CR fails with `stateful_gate_timeout`; no rollback needed (build not started) |

---

## Testing

- **Go unit tests:** `deep_discover` command with mocked `ss`/`netstat` output; config-file parser for nginx/Apache/IIS/generic; Windows stub returns empty enrichment on Linux CI
- **Backend unit test:** Confirm-stateful endpoint — 200 on valid executing auto CR, 400 on wrong type/status, 404 on missing
- **Frontend:** No automated tests (UI verification)
- **Smoke test:** Phase AUTO already covers the full flow with `dry_run=True`; extend to fire `POST /confirm-stateful` when stateful gate fires and verify the CR continues past stage 4
