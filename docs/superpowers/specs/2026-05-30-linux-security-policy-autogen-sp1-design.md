# Linux Security Policy Auto-Generation — SP1: Seccomp (Generic Policy Foundation)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a generic soak-session pipeline that observes workload behavior, synthesizes a security policy profile, diffs against a stored baseline, and proposes a change request — delivered first for seccomp profiles, with the infrastructure designed to absorb AppArmor (SP2), SELinux, and eBPF network policy (SP3) without a rewrite.

**Architecture:** A `SecurityPolicySoakSession` model scoped to a CR project accumulates syscall observations from `seccomp_learn` executor runs across all project assets. On stop, a synthesizer converts observations to a seccomp JSON allowlist profile. If no prior baseline exists the CR is auto-proposed; if a baseline exists a diff is computed and gated on operator approval before the CR is created. The `configure_seccomp` executor applies the profile to bare-metal (file write + service restart) or container targets (runtime spec update), with rollback restoring the prior state.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL (JSONB), existing CR lifecycle, existing `seccomp_learn` executor, existing `configure_seccomp` CR skeleton (to be wired), React frontend for diff review.

**Sub-project context:** SP1 of 4. SP2 (AppArmor), SP3 (eBPF/network policy), SP4 (harden-on-deploy pipeline) all depend on the generic session/baseline/diff infrastructure built here.

---

## Generalization Principle

The soak → observe → synthesize → diff → accept → CR pipeline is identical across all Linux security policy types. What varies per type:

| Layer | seccomp (SP1) | AppArmor (SP2) | eBPF/network (SP3) |
|---|---|---|---|
| Observation executor | `seccomp_learn` | `apparmor_learn` | `network_soak` (Cilium/Tetragon) |
| Synthesis | syscall set → seccomp JSON | file/cap/net access → AA profile | flow log → NetworkPolicy YAML |
| Apply CR type | `configure_seccomp` | `configure_apparmor` | `configure_network_policy` |

All share: `security_policy_soak_sessions`, `security_policy_baselines`, and the full session API. SP1 ships with `policy_type = "seccomp"` as the only implemented value; later SPs add theirs.

---

## Data Model

### Migration: `security_policy_soak_sessions`

```sql
CREATE TABLE security_policy_soak_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    policy_type VARCHAR(32) NOT NULL CHECK (policy_type IN ('seccomp', 'apparmor', 'selinux', 'network_policy')),
    status VARCHAR(16) NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'stopped', 'synthesized', 'cr_proposed')),
    window_seconds INTEGER NOT NULL DEFAULT 600,
    asset_ids JSONB NOT NULL DEFAULT '[]',
    raw_observations JSONB NOT NULL DEFAULT '{}',  -- {asset_id: [syscall, ...]}
    synthesized_profile JSONB,                      -- null until stop+synthesize
    baseline_delta JSONB,                           -- {added: [...], removed: [...]} or null if no prior
    partial BOOLEAN NOT NULL DEFAULT FALSE,         -- true if any asset observation failed
    cr_id UUID REFERENCES change_requests(id),      -- set when CR is proposed
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_soak_sessions_project ON security_policy_soak_sessions(project_id);
CREATE INDEX idx_soak_sessions_status ON security_policy_soak_sessions(status);
```

### Migration: `security_policy_baselines`

```sql
CREATE TABLE security_policy_baselines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    policy_type VARCHAR(32) NOT NULL,
    profile JSONB NOT NULL,
    cr_id UUID REFERENCES change_requests(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, policy_type)
);
```

### `configure_seccomp` CR parameters schema

```json
{
  "session_id": "<uuid>",
  "profile": { "defaultAction": "SCMP_ACT_ERRNO", "syscalls": [...] },
  "target_type": "bare_metal | container",
  "profile_path": "/etc/seccomp/profiles/<project>.json",
  "service_name": "nginx",
  "runtime_spec_path": "/etc/docker/daemon.json"
}
```

Rollback state in `artifact_refs` on the CR: `{"prior_profile": <prior JSON or null>}`. If null (first apply), rollback removes the profile file and restarts without seccomp constraint.

---

## API

```
POST   /security-policy/soak-sessions
       Body: {project_id, policy_type, asset_ids, window_seconds}
       Response: {id, status, started_at}

GET    /security-policy/soak-sessions/{id}
       Response: {id, status, policy_type, asset_ids, elapsed_seconds,
                  observation_counts: {asset_id: count}, partial, synthesized_profile, baseline_delta, cr_id}

POST   /security-policy/soak-sessions/{id}/stop
       Triggers synthesis. Auto-proposes CR if no prior baseline.
       Response: {id, status, synthesized_profile, baseline_delta, cr_id (if auto-proposed)}

GET    /security-policy/soak-sessions/{id}/diff
       Returns baseline_delta. 404 if no prior baseline. 409 if session not stopped.

POST   /security-policy/soak-sessions/{id}/accept
       Operator approves diff → creates configure_seccomp CR.
       Response: {cr_id}

GET    /security-policy/baselines/{project_id}?policy_type=seccomp
       Returns current stored baseline. 404 if none.
```

---

## Flows

### First-time hardening (no baseline)
1. `POST /soak-sessions` → session `running`
2. Platform dispatches `seccomp_learn` executor to each asset in scope; observations stream into `raw_observations`
3. Window elapses (or operator calls `stop`)
4. `POST .../stop` → synthesize syscall union → build seccomp JSON → no baseline found → create `configure_seccomp` CR → store profile as new baseline → session status `cr_proposed`
5. Stop response includes `cr_id`; operator approves CR via normal CR lifecycle

### Re-hardening with delta review (baseline exists)
1. `POST /soak-sessions` → session `running`
2. Observation collection as above
3. `POST .../stop` → synthesize → compare against baseline → compute `{added, removed}` → session status `synthesized`; stop response includes `baseline_delta`
4. `GET .../diff` → operator reviews added/removed syscalls
5. `POST .../accept` → create `configure_seccomp` CR → update baseline record → session status `cr_proposed`

### Containerization hook
During `agent_containerize_build` executor:
- At build stage start: call `POST /soak-sessions` with `policy_type=seccomp`, project assets, `window_seconds` = estimated build duration
- At build stage end: call `POST .../stop`
- If CR auto-proposed: include `cr_id` in build stage output; CR appears in project alongside containerization CRs

---

## `configure_seccomp` Executor

**File:** `backend/app/connectors/executors/linux/configure_seccomp.py`

```python
async def execute(parameters, asset_ids, connector):
    profile = parameters["profile"]
    target_type = parameters.get("target_type", "bare_metal")
    prior = None

    if target_type == "bare_metal":
        path = parameters["profile_path"]
        service = parameters.get("service_name")
        # read prior for rollback
        prior = await _read_file_via_connector(connector, path)
        await _write_file_via_connector(connector, path, json.dumps(profile, indent=2))
        if service:
            await _restart_service_via_connector(connector, service)
    else:
        # container: update runtime spec
        spec_path = parameters.get("runtime_spec_path", "/etc/docker/daemon.json")
        prior = await _read_file_via_connector(connector, spec_path)
        await _patch_runtime_spec(connector, spec_path, profile)

    return {"applied": True, "target_type": target_type, "prior_profile": prior}

async def rollback(parameters, execution_result, connector):
    prior = execution_result.get("prior_profile")
    target_type = parameters.get("target_type", "bare_metal")

    if target_type == "bare_metal":
        path = parameters["profile_path"]
        service = parameters.get("service_name")
        if prior:
            await _write_file_via_connector(connector, path, prior)
        else:
            await _delete_file_via_connector(connector, path)
        if service:
            await _restart_service_via_connector(connector, service)
    else:
        spec_path = parameters.get("runtime_spec_path", "/etc/docker/daemon.json")
        if prior:
            await _write_file_via_connector(connector, spec_path, prior)
        await _restart_container_runtime(connector)

    return {"rolled_back": True}
```

---

## Profile Synthesis

**File:** `backend/app/services/security_policy/synthesizer.py`

Seccomp synthesizer takes `raw_observations: dict[str, list[str]]` (asset_id → syscall list), returns seccomp JSON:

```python
def synthesize_seccomp(raw_observations: dict) -> dict:
    all_syscalls = set()
    for syscalls in raw_observations.values():
        all_syscalls.update(syscalls)
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [{"names": sorted(all_syscalls), "action": "SCMP_ACT_ALLOW"}]
    }

def compute_delta(prior: dict, current: dict) -> dict:
    prior_set = set(prior["syscalls"][0]["names"])
    current_set = set(current["syscalls"][0]["names"])
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
```

---

## Asset Observation — Error Handling

- Asset unreachable at session start: logged in session status; `partial=True`; soak continues with remaining assets
- `seccomp_learn` executor fails mid-session: partial observations retained; `partial=True` flagged
- Operator warned via `partial` field in stop response; CR is still created but marked with `partial=True` in parameters
- Zero observations (all assets failed): stop returns error; no CR created

---

## Frontend

Two UI additions:

**1. Soak Session panel** on the Project detail page:
- "Start Security Policy Soak" button → drawer: policy type selector (seccomp only in SP1), asset list checkboxes, window duration slider (2 min – 24 hr)
- Active session shows elapsed time, observation counts per asset, stop button
- On stop: if no baseline → toast "Profile synthesized — CR proposed" with link; if baseline → diff panel appears

**2. Diff review panel** (appears after stop when baseline exists):
- Two columns: "Added syscalls" (green), "Removed syscalls" (red)
- "Accept & Propose CR" button → calls `POST .../accept`
- "Discard" button → dismisses without creating CR

---

## Smoke Test Phase: `SECCOMP_AUTOGEN`

Runs on an existing Linux asset (reuse any passing smoke AMI with `seccomp_learn` available):

1. Start soak session for project scope (30s window for smoke)
2. Verify session status = `running`, observation collection active
3. Stop session → verify `synthesized_profile` present, `cr_id` returned (no prior baseline)
4. Verify `configure_seccomp` CR created and in `pending_approval` state
5. Approve and execute CR → verify profile file written to target path
6. Rollback CR → verify prior state restored (file removed or reverted)
7. Run second soak session (30s) → verify `baseline_delta` returned, no auto-CR
8. Call `accept` → verify second `configure_seccomp` CR created, baseline updated

---

## Out of Scope (SP1)

- AppArmor profile synthesis — SP2
- eBPF / network policy — SP3
- Harden-on-deploy integrated pipeline — SP4
- SELinux type enforcement policy (future; infrastructure supports it via `policy_type` enum)
- UI for viewing baseline history across multiple soak runs
