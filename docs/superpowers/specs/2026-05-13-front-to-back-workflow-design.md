# Front-to-Back Workflow Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the friction that makes simple security and infrastructure operations complex in practice — from the first signal to final verification — while maintaining safety guardrails for both security operators who lack infrastructure expertise and infrastructure operators who need efficiency tools.

**Architecture:** Phase 0 (executor wire-up) plus four themed improvement phases (Foundation → Scale → Identity → Hardening) and a Future phase for identity connectors, built on top of the existing CR workflow engine, agent command library, and connector framework. Each phase ships independently and each feature includes a smoke test phase that exercises it end-to-end against real infrastructure.

**Tech Stack:** FastAPI backend, React/React Query frontend, Nexplane Go agent (30+ commands), AWS/GCP/Azure/OCI connectors, PostgreSQL, Tailscale mesh networking.

---

## Context: The Two Personas

### Security Operator
Arrives from a *risk signal* — CVE alert, scanner finding, compliance failure, threat intelligence. Wants fast remediation but may not know the safe execution path. Needs AI assistance to fill the expertise gap and guardrails (snapshots, rollbacks, approval gates) to avoid introducing new risk while fixing existing risk.

### Infrastructure Operator
Arrives from a *change cycle* — patch Tuesday, planned upgrade, capacity event, hardening initiative. Knows the safe execution path and values efficiency over hand-holding. Needs the platform to get out of their way for routine work while providing a complete, auditable trail.

### The Handoff
Security creates urgency, infrastructure owns expertise. The platform must make the transfer of context between them seamless — the approver needs to understand *why* a change is needed, and the requester needs confidence that the change will be executed safely.

---

## Phase 0 — Executor Wire-Up

*Prerequisite sprint. The Go agent fully implements every advertised OS hardening capability, but all 14 Python backend executors are stubs returning hardcoded mock data. CRs appear to succeed while nothing happens on the host. This must be fixed before any operator-facing phase has meaning.*

### 0.1 Background

An audit of `backend/app/connectors/executors/nexplane_agent/` revealed a systematic pattern: every OS hardening executor was created as a UI/API placeholder but was never updated to call `dispatch_agent_job()`. The correct pattern exists in `change_ip.py` and `isolate_host.py` — these stubs need to follow it.

The Go agent in `agent/commands/ossecurity/` is fully functional for all 14 capabilities, tested via `test_agent_live.py`. The fix is purely mechanical — update each Python file to dispatch to the agent with the correct command name and parameters.

### 0.2 Executors to Wire (all 14)

| File | Agent command | Real action | Notes |
|------|--------------|-------------|-------|
| `configure_seccomp.py` | `seccomp` | Writes systemd drop-in with seccomp profile; reloads service | Add `mode: "learn"\|"enforce"` parameter |
| `configure_apparmor.py` | `apparmor` | `aa-enforce`/`aa-complain`/`aa-disable`; writes profile to `/etc/apparmor.d/` | Mode parameter maps to aa- command |
| `configure_selinux.py` | `selinux` | `setenforce`; updates `/etc/selinux/config`; `semodule` for custom policies | Already has `selinux_setmode` in ossecurity |
| `apply_sysctl_hardening.py` | `sysctl` | Writes `/etc/sysctl.d/99-nexplane-hardening.conf`; runs `sysctl --system` | 13-point CIS baseline built into agent |
| `configure_host_firewall.py` | `firewall` | Detects iptables/nftables/firewalld; captures snapshot; applies rules | add_rule/remove_rule/flush actions |
| `blacklist_kernel_modules.py` | `modules` | Writes `/etc/modprobe.d/nexplane-blacklist.conf`; `update-initramfs` | Requires reboot to fully take effect |
| `harden_mount_options.py` | `mount` | Parses `/etc/fstab`; adds noexec/nosuid/nodev; remounts | Targets /tmp, /dev/shm, /var/tmp |
| `deploy_auditd_rules.py` | `auditd` | CIS L1/L2 profiles or custom rules; writes to `/etc/audit/rules.d/`; `augenrules` | Profile parameter: `cis_level1`, `cis_level2`, `custom` |
| `setup_file_integrity_monitoring.py` | `fim` | AIDE init (build baseline) or check (compare to baseline); Tripwire fallback | Action parameter: `init`, `check` |
| `audit_os_security_posture.py` | `posture` | Queries sestatus, aa-status, auditctl -s, ausearch for denials | Read-only — returns real state |
| `deploy_ebpf_policy.py` | `ebpf` | `bpftool` to load eBPF program; pins to `/sys/fs/bpf/`; attaches to target | Requires kernel ≥ 5.7 |
| `configure_ebpf_security_policy.py` | `ebpf` | Writes Falco/Cilium policy YAML; validates; reloads framework | Framework parameter: `falco`, `cilium` |
| `audit_ebpf_posture.py` | `ebpf` | `bpftool prog list`; checks for unexpected programs in `/sys/fs/bpf` | Read-only |
| `harden_ssh.py` | `ssh` | Writes `/etc/ssh/sshd_config.d/99-nexplane-hardening.conf`; `sshd -t`; reloads | 12 CIS defaults built into agent |
| `configure_pam.py` | `pam` | Detects RHEL vs Debian PAM variant; pwquality/faillock; CIS L1/L2 profiles | min_length, max_failures, lockout_duration params |

### 0.3 Windows Executors to Wire (all 13)

The Windows problem is identical to Linux: `winharden_windows.go` and `winpatch_windows.go` are fully implemented with real registry modifications, PowerShell COM API calls, `auditpol`, `netsh`, and snapshot-based rollback. All 13 Python executors are stubs returning hardcoded mock data.

| File | Agent command | Real action | Notes |
|------|--------------|-------------|-------|
| `configure_windows_audit_policy.py` | `winharden` | `auditpol` + registry for process command-line auditing | Returns hardcoded `categories_count: 9` today |
| `configure_windows_firewall.py` | `winharden` | `netsh advfirewall` rule add/remove/default-policy | Real Go impl: add_rule/remove_rule/set_default actions |
| `harden_rdp.py` | `winharden` | Registry: NLA enforcement, SecurityLayer=2, idle timeout, port | Returns echoed params today |
| `harden_registry.py` | `winharden` | 7 baseline settings: autorun, LM hash, NTLMv1, WDigest, DLL search, UAC, print spooler | Extend to 50+ enterprise baseline keys |
| `harden_smb.py` | `winharden` | Disable SMBv1, require signing (server+client), block guest | Returns mock SMB version today |
| `harden_tls_protocols.py` | `winharden` | SCHANNEL registry: disable SSL 2.0/3.0, TLS 1.0/1.1 | Returns mock protocol list today |
| `deploy_applocker_policy.py` | `winharden` | XML policy deployment; enforcement toggle; `AppIDSvc` auto-start | Returns mock policy today |
| `enable_bitlocker.py` | `winharden` | Protector config (TPM/TPM+PIN/recovery key); encryption; recovery key export | Returns fake recovery key today |
| `enable_credential_guard.py` | `winharden` | Registry-based DeviceGuard (VBS, UEFI lock, HECI) | Returns mock response today |
| `configure_laps.py` | `winharden` | Enable LAPS; password age/length config via registry | Returns mock response today |
| `enforce_powershell_clm.py` | `winharden` | Registry `__PSLockdownPolicy` enforcement + snapshot | Returns mock response today |
| `apply_windows_patches.py` | `winpatch` | Windows Update COM API; filter by KB/CVE/security-only; install; schedule reboot | Returns `packages_updated: 0` today |
| `audit_windows_patch_status.py` | `winpatch` | `Get-HotFix` inventory; pending updates search; reboot-pending state | Returns `0 updates available` today |

**Note on `audit_scheduled_tasks.py`:** This is also a stub for both Linux and Windows (the Go agent's `winharden` `scheduled_tasks` command is real on Windows; Linux uses `appdiscovery` cron enumeration). Add to Phase 0 wire-up for both platforms.

### 0.4 Pattern

Every executor follows the same structure as `change_ip.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="<agent_command_name>",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
```

Each executor also needs a `rollback()` function that dispatches the agent's rollback/restore command (most ossecurity commands already capture pre-change state and support restore).

### 0.5 Add `dry_run` to All Hardening Executors

The Go agent's ossecurity commands accept a `dry_run` parameter (returns what would change without applying). Wire this through so operators can preview the effect of any hardening change before committing. The CR creation form shows a "Preview changes" button when `dry_run` is available.

### Smoke Test Phase: OSSEC_WIRE

**Purpose:** Verify all 14 hardening executors actually dispatch to the agent and produce real side effects.

**Setup:** EC2 with Amazon Linux 2 (default state — no hardening applied).

**Test steps (one per executor, executed in sequence with verification):**

1. `apply_sysctl_hardening` → SSM: `sysctl net.ipv4.tcp_syncookies` → expect `1`
2. `harden_ssh` → SSM: `grep PermitRootLogin /etc/ssh/sshd_config.d/99-*` → expect `no`
3. `configure_pam` (cis_level1) → SSM: `grep minlen /etc/security/pwquality.conf` → expect `14`
4. `deploy_auditd_rules` (cis_level1) → SSM: `auditctl -l | wc -l` → expect > 0
5. `harden_mount_options` → SSM: `mount | grep /tmp` → expect `noexec`
6. `blacklist_kernel_modules` (modules: `["usb-storage","cramfs"]`) → SSM: `cat /etc/modprobe.d/nexplane-blacklist.conf` → contains both
7. `configure_host_firewall` (add rule blocking port 9999) → SSM: `iptables -L | grep 9999` → present
8. `configure_apparmor` (mode: complain, service: nginx) → SSM: `aa-status | grep nginx` → complain mode
9. `configure_seccomp` (mode: enforce, service: nginx, profile: minimal) → SSM: `systemctl cat nginx | grep Seccomp` → profile loaded
10. `setup_file_integrity_monitoring` (action: init) → SSM: `ls /var/lib/aide/aide.db*` → exists
11. `audit_os_security_posture` → assert result contains real `selinux_status`, `auditd_enabled` fields (not hardcoded)
12. `audit_ebpf_posture` → assert result contains `loaded_programs` array (may be empty, but not hardcoded)

**All CRs must use `dry_run: false`. Rollback each after verification.**

**New agent capability required:** None — all Go commands exist. The wire-up is purely Python.

**Assertions:**
- All 12 CRs complete with status `completed` (not mock-completed)
- SSM verification commands confirm real host state changed
- Rollback CRs restore pre-change state

---

## Phase 1 — Foundation

*Eliminate the most painful daily friction. These unblock all other phases.*

### 1.1 Notification System

**What it does:** Pushes alerts for actionable events rather than requiring operators to poll the platform.

**Events to notify:**
- CR awaiting approval (to approvers)
- CR execution started / completed / failed (to requester)
- Campaign CR failed (batch failure to campaign owner)
- SLA breach imminent (to finding owner, 20% of SLA remaining)
- SLA breached (to finding owner + escalation chain)
- Discovery completed (to operator who triggered it)
- Maintenance window starting in 30 minutes (to operators with scheduled CRs)
- Policy drift detected (Phase 4)

**Delivery channels:**
- In-app notification center (bell icon, badge count, dismissible list)
- Slack webhook (configurable per org in Settings)
- Email (configurable per notification type)
- PagerDuty (for emergency-priority CRs and SLA breaches)

**API additions:**
- `GET /notifications` — paginated list, unread count
- `POST /notifications/:id/read`
- `POST /notifications/read-all`
- `GET /settings/notifications` / `PUT /settings/notifications` — channel config per event type

**Frontend:** Bell icon in sidebar header with badge. Notification panel slides out. Each notification has: event type, affected resource link, timestamp, dismiss button. Settings page gains "Notifications" section.

**Smoke test phase:** `NOTIFY` — Creates a CR as one user, polls as the approver user for a notification to appear within 30 seconds of CR creation. Verifies the notification links to the correct CR. Approves the CR and verifies a completion notification appears for the requester.

---

### 1.2 CVE Context in Approval Queue

**What it does:** Infra approvers see the security context that motivated the change request — without having to ask.

**Current gap:** CR card in approval queue shows title, change type, risk badge, requester. No link to the vulnerability finding that triggered it.

**Changes:**
- CR creation gains an optional `finding_ids: []` field — populated automatically when a CR is created from a campaign or finding action
- Approval queue card expands to show: linked CVE IDs with CVSS scores, affected asset count, campaign name if batch, "View finding" link
- Finding detail page shows linked CRs in a "Remediation" section

**API additions:**
- `finding_ids` field on `ChangeRequest` model and CR create/read schemas
- `GET /change-requests/:id/findings` — findings linked to this CR
- `GET /findings/:id/change-requests` — CRs addressing this finding

**Smoke test phase:** Part of `FINDING_LIFECYCLE` (see 1.5).

---

### 1.3 Maintenance Window Awareness in CR Creation

**What it does:** Operators see the target asset's maintenance window when creating a CR and understand enforcement semantics.

**Current gap:** Maintenance windows exist at `/maintenance-windows` but are invisible during CR creation.

**Changes:**
- CR creation form: when assets are selected, fetch and display each asset's applicable maintenance window (matched by tags). Show: window name, schedule (human-readable cron), next occurrence.
- If the CR's intended execution falls outside the window, show a yellow warning with the next valid window time.
- Maintenance window model gains `enforcement: "advisory" | "hard"` field.
  - `advisory`: shows warning, operator can proceed — warning is logged in CR audit trail.
  - `hard`: blocks execution outside window. CR status shows `blocked_by_maintenance_window`. Emergency override requires a reason text field (logged in audit trail, triggers notification to approver).
- Emergency flag on CR bypasses `hard` enforcement (reason required).

**API additions:**
- `enforcement` field on `MaintenanceWindow` model
- `GET /assets/:id/maintenance-windows` — windows applicable to this asset
- Override audit event: `execution.maintenance_window_override` with reason and actor

**Smoke test phase:** `MAINT_WINDOW` — Creates a maintenance window with `hard` enforcement for a specific asset tag, configured for a time 2 hours from now. Creates a patch CR for a tagged asset. Verifies execution is blocked. Sets the emergency flag with reason. Verifies execution proceeds and the override is in the audit trail.

---

### 1.4 Automatic Pre-Change Snapshot

**What it does:** Destructive CRs automatically snapshot the target asset before executing.

**Current gap:** `snapshot_asset` and `create_ebs_snapshot` are separate CRs with no guaranteed ordering relative to the operation they protect.

**Changes:**
- CR types that are destructive or reversibility-sensitive gain a `snapshot_before: bool` field (default `false`, default `true` for OS upgrade and kernel patch).
- When `snapshot_before: true`, the execution engine inserts a `create_ebs_snapshot` step before the first execution step. The snapshot step must reach `completed` state before proceeding. If the snapshot fails, the CR fails without attempting the main operation.
- The snapshot is tagged: `nexplane-pre-change`, `cr-id: <id>`, `change-type: <type>`.
- CR detail shows the pre-change snapshot ID with a "Restore from this snapshot" quick action.
- Rollback action on the parent CR uses the pre-change snapshot if one exists.

**Affected CR types:** `agent_linux_patch`, `agent_linuxupgrade`, `update_security_group`, `configure_apparmor`, `configure_seccomp`, `configure_selinux`, any `agent_` type marked `destructive: true` in its definition.

**API additions:**
- `snapshot_before` field on `ChangeRequest`
- `pre_change_snapshot_id` on CR result metadata
- Rollback path: if `pre_change_snapshot_id` exists, rollback CR fires `restore_ebs_snapshot` rather than attempting software undo

**Smoke test phase:** `SNAPSHOT_BEFORE` — Creates a patch CR with `snapshot_before: true`. Verifies the snapshot CR fires first and completes. Verifies the patch CR executes only after snapshot is complete. Checks that the snapshot is tagged correctly and appears in the CR detail. Verifies the rollback option uses the snapshot ID.

---

### 1.5 Finding Auto-Closure on CR Completion

**What it does:** When a remediation CR completes successfully, linked vulnerability findings automatically move to `remediated` state.

**Current gap:** Security team's open finding count doesn't decrease when infra patches the vulnerability.

**Changes:**
- `Finding` model gains states: `open`, `mitigated`, `remediated`, `false_positive`, `accepted_risk`, `reopened`.
- On CR completion (status → `completed`): for each `finding_id` in the CR's `finding_ids`, set finding state to `remediated`, record `remediated_at`, `remediated_by_cr_id`.
- Scanner sync can override `remediated` back to `open` if the vulnerability is re-detected (regression).
- Finding list shows `remediated` findings in a separate "Closed" tab, not the default open queue.
- Finding detail shows remediation timeline: found → triaged → CR created → CR executed → remediated.

**API additions:**
- `state` field replaces `status` on `Finding` model (migration required)
- `remediated_at`, `remediated_by_cr_id` fields
- `POST /findings/:id/reopen` — scanner sync calls this on re-detection
- Event: `finding.remediated` (triggers notification to finding owner)

**Smoke test phase:** `FINDING_LIFECYCLE` — Inserts a test finding record linked to the smoke test EC2 asset. Creates a patch CR with the finding's ID in `finding_ids`. Executes the CR. Verifies the finding state transitions to `remediated`. Triggers a mock scanner re-sync that re-reports the same CVE. Verifies the finding reopens. Verifies approval queue shows CVSS score and finding link for the CR.

---

### 1.6 Cross-Asset Timeline

**What it does:** Asset detail shows everything that ever happened to it — CRs, discoveries, findings, compliance checks — in one chronological view.

**Current gap:** Asset detail shows metadata and quick actions. Historical changes require searching the CR list.

**Changes:**
- Asset detail page gains an "Activity" tab (alongside existing tabs).
- Timeline fetches events from: `change_requests` (where asset is a target), `audit_events` (where `asset_id` matches), `agent_jobs` (where `asset_id` matches), `findings` (where `asset_id` matches).
- Each event shows: timestamp, event type (icon), description, actor, outcome, duration.
- Filterable by: event type, date range, outcome (success/failure).
- CR events link to the CR detail. Finding events link to the finding.

**API additions:**
- `GET /assets/:id/timeline?from=&to=&types=&outcome=` — unified event stream, paginated, sorted descending by time

**Smoke test phase:** `ASSET_TIMELINE` — Runs discovery, a patch CR, and a compliance check against the same asset. Fetches the asset timeline. Verifies all three event types appear in chronological order with correct actors and outcomes.

---

### 1.7 Application-Aware Post-Change Verification

**What it does:** CR types support configurable health checks that verify application-level correctness after execution, not just host reachability.

**Current gap:** Verification is SSH connectivity and basic OS responsiveness. A crashed nginx after kernel patch passes verification because SSH is up.

**Changes:**
- CR create/edit form gains a "Verification checks" section (collapsed by default, expandable).
- Supported check types:
  - `http`: URL, expected status code, optional body contains string
  - `service`: systemd service name, expected state (`active`, `inactive`)
  - `port`: host:port, expected open/closed
  - `command`: shell command, expected exit code, optional stdout contains
  - `kernel_version`: minimum kernel version (for upgrade CRs)
  - `package_version`: package name + minimum version (for patch CRs)
- Checks run via SSM/agent after CR execution and before the CR is marked `completed`. If any check fails, CR status becomes `verification_failed` and rollback is triggered.
- CR definition JSON (change type metadata) can specify default verification checks that are pre-populated in the form.

**Default checks by CR type:**
- `agent_linux_patch`: package version check for patched packages, service check for all `active` services that were active before patching
- `agent_linuxupgrade`: kernel version check, service checks
- `configure_seccomp` / `configure_apparmor`: service check for all target services + HTTP check if the service exposes one
- `update_security_group`: port check (allowed ports open, denied ports closed)

**API additions:**
- `verification_checks: []` field on `ChangeRequest`
- Verification run result: stored in `execution_runs.result.verification.checks[]` with per-check outcome

**Smoke test phase:** `APP_VERIFICATION` — Creates a patch CR on an EC2 running nginx with custom verification checks: `http` (localhost:80 → 200), `service` (nginx → active), `package_version` (kernel ≥ current). Executes. Verifies all checks pass. Then creates a second CR with a check guaranteed to fail (`http` expecting 404 but getting 200). Verifies `verification_failed` status and rollback fires.

---

### 1.8 Maintenance Window Hard Enforcement

*Covered in 1.3. Separate smoke test:* `MAINT_WINDOW_HARD` — Configures a hard maintenance window for the asset tag used in all smoke tests. Creates a patch CR with `emergency: false`. Verifies the `/execute` endpoint returns `blocked_by_maintenance_window`. Sets `emergency: true` with a reason. Verifies execution proceeds and the override event appears in the audit trail.

---

## Phase 2 — Scale and Handoff

*Enable teams to operate at fleet scale and hand off context cleanly.*

### 2.1 Bulk CR Creation from Asset Selection

**What it does:** Select N assets in the asset inventory and create a CR for each with shared parameters and per-asset overrides.

**Changes:**
- Asset list gains a "Create CR for selected" button in the bulk actions bar (appears on multi-select).
- Opens a drawer: choose change type, fill shared parameters, optionally override per-asset. Preview shows N CRs that will be created.
- CRs are created as a linked group: `batch_id` UUID, shared `batch_label`. Batch ID is shown in CR list and detail.
- Optional: automatically create a Project to collect all batch CRs.

**API additions:**
- `POST /change-requests/batch` — accepts array of CR definitions, returns array of created CR IDs with `batch_id`
- `batch_id` field on `ChangeRequest`
- `GET /change-requests?batch_id=X` — all CRs in a batch

**Smoke test phase:** `BULK_PATCH` — Launches 3 EC2 instances with the smoke test profile. Uses the batch API to create 3 patch CRs (one per instance) with a shared `security_only: true` parameter. Verifies all 3 CRs appear with the same `batch_id`. Bulk-approves. Executes in rolling batches of 1. Verifies all 3 patch successfully and the finding auto-closes on all 3 if linked.

---

### 2.2 Bulk Approval

**What it does:** Approve or reject all CRs matching a filter in a single action.

**Changes:**
- Approvals queue gains "Approve all" and "Reject all" buttons when CRs are filtered or a batch is selected.
- Bulk approve: all selected CRs transition to `approved`. One audit event per CR with the approver.
- Bulk approve requires: all selected CRs are in `awaiting_approval`, approver has the role, no CR in the selection is above a configured "bulk approval risk threshold" (default: cannot bulk-approve `critical` risk).

**API additions:**
- `POST /change-requests/bulk-approve` — body: `{cr_ids: [], decision: "approved"|"rejected", comment: ""}`

**Smoke test phase:** Part of `BULK_PATCH`.

---

### 2.3 Campaign Failure Policy

**What it does:** Per-campaign configuration for what happens when a batch CR fails.

**Current gap:** If 3 of 12 patch CRs fail, the campaign has no configured response. Behavior is implicit.

**Changes:**
- `PatchCampaign` gains `failure_policy: "continue" | "pause" | "rollback_all"`.
  - `continue`: log failure, proceed with remaining CRs.
  - `pause`: halt the campaign, surface for operator review, resume or cancel manually.
  - `rollback_all`: trigger rollback for all completed CRs in the campaign, cancel pending ones.
- Campaign detail shows: succeeded / failed / pending counts with progress bar. Failed CRs shown in a "Needs attention" section with error summaries.
- Campaign status: `running`, `paused` (waiting for operator), `completed`, `partially_failed`, `rolled_back`.

**API additions:**
- `failure_policy` field on `PatchCampaign`
- `POST /patch-campaigns/:id/resume` — resume a paused campaign
- `GET /patch-campaigns/:id/summary` — per-CR outcome counts

**Smoke test phase:** `CAMPAIGN_FAILURE` — Creates 3 EC2s. Creates a campaign with `failure_policy: "pause"`. Intentionally causes one CR to fail (targeting a nonexistent package). Verifies campaign status becomes `paused`. Verifies the operator can resume (skip the failed CR) or cancel. Verifies the remaining CRs complete successfully after resume.

---

### 2.4 Emergency Priority + Escalation Path

**What it does:** Time-sensitive CRs surface immediately to on-call approvers and auto-escalate if not acted on.

**Changes:**
- CR creation gains `priority: "normal" | "emergency"` toggle. Emergency requires a reason.
- Emergency CRs:
  - Pin to top of approval queue with a red `EMERGENCY` badge.
  - Immediately notify all users with approval role for this change type (not just the default approver).
  - Start an escalation timer: if not approved within N minutes (configurable, default 30), notify the escalation chain.
  - Bypass `hard` maintenance window enforcement (reason is logged).
  - Can execute in parallel regardless of campaign batch size settings.
- Settings → `Emergency response` section: configure escalation chain (ordered list of user/role), escalation timeout, notification channels for emergency events.

**API additions:**
- `priority` field on `ChangeRequest`
- `emergency_reason` text field
- `OrganizationSettings` gains `escalation_chain: [{user_id, delay_minutes}]`
- Background job: check pending emergency CRs every minute, fire escalation notification if timeout exceeded

**Smoke test phase:** `EMERGENCY_CR` — Creates an EC2. Creates a patch CR with `priority: "emergency"` and a reason. Verifies the CR notification reaches the approver account immediately (within 30 seconds). Configures a 1-minute escalation timeout. Waits 90 seconds without approving. Verifies escalation notification fires. Approves. Verifies CR executes immediately despite a hard maintenance window being active.

---

### 2.5 Finding "Mitigated" State

**What it does:** Compensating controls receive their own finding lifecycle state, separate from full remediation.

**Changes (extends Finding from 1.5):**
- `mitigated` state: finding is addressed by a compensating control, not a patch. SLA clock pauses. Distinct from `remediated` (which means the root cause is fixed).
- Finding → Mitigate action creates a mitigation CR (WAF rule, security group change, AppArmor policy, feature flag) and links it.
- When the mitigation CR completes, finding moves to `mitigated`.
- Platform monitors for patch availability (via scanner sync). When a patch is detected for a mitigated finding, the finding surfaces in a "Patch now available" queue with the mitigation CR still visible for context.
- Mitigated findings show: mitigation applied, mitigation CR link, time since mitigation, patch availability status.

**API additions:**
- `mitigated_at`, `mitigated_by_cr_id`, `patch_available_at` on `Finding`
- `POST /findings/:id/mark-mitigated` — for manual attestation when mitigation is external
- Scanner sync: when CVE is re-reported as having a patch, set `patch_available_at`

**Smoke test phase:** `FINDING_MITIGATED` — Inserts a test finding. Creates a security group update CR as the mitigation (blocks the vulnerable port). Links CR to finding with mitigation intent. Executes CR. Verifies finding state is `mitigated` and SLA clock is paused. Simulates scanner reporting a patch available. Verifies finding moves to "Patch now available" queue. Creates patch CR. Executes. Verifies finding moves to `remediated`.

---

### 2.6 Access Review → Remediation CR

**What it does:** Revoking access in an access review automatically generates scoped remediation CRs targeting only the systems that account has access to.

**Current gap:** Review decision and remediation CR are completely disconnected workflows.

**Changes:**
- "Revoke" decision in access review triggers a modal: "Create offboard CR for this user?" — pre-populated with the user's name and showing the systems they have access to (derived from asset inventory connector data).
- For each connected identity system (AD, AWS IAM, Okta), a targeted revocation step is generated.
- Resulting CR: `agent_user_offboard` or `disable_iam_user` depending on system type, linked to the access review entry.
- Access review entry shows: review decision, linked remediation CR, CR status (pending approval / completed / failed).
- Access review campaign `completed` status requires: all "revoke" decisions have associated remediation CRs in `completed` or `rejected` state.

**API additions:**
- `remediation_cr_id` on `AccessReviewEntry`
- `POST /access-reviews/:campaign_id/entries/:entry_id/create-remediation` — creates scoped revocation CR
- `GET /access-reviews/:campaign_id/remediation-status` — overall remediation completion

**Smoke test phase:** `ACCESS_REVOKE` — Creates a test IAM user. Runs an access review campaign scoped to the test user. Marks the user for revocation. Verifies the "Create remediation CR" option appears. Creates the CR. Approves. Executes. Verifies the IAM user is disabled via boto3. Verifies the access review campaign status reflects the completed remediation.

---

### 2.7 Re-Scan Trigger on CR Completion

**What it does:** After a patch or remediation CR completes, platform triggers a targeted re-scan of affected assets in connected scanners.

**Changes:**
- `Connector` model for scanner types (CrowdStrike, Tenable, Qualys, Snyk, Wiz) gains a `supports_targeted_rescan: bool` capability flag.
- On CR completion with `finding_ids` populated: call the scanner's targeted scan API for the affected assets.
- Scan is async: the finding moves to `remediated_pending_verification`. When scanner reports back (next sync or webhook), finding moves to `remediated` or reopens.
- CR detail shows "Triggered re-scan via [Scanner]" in the verification section.

**API additions:**
- `POST /connectors/:id/rescan` — triggers targeted scan for given asset IDs
- Scanner-specific implementations in connector service
- `Finding` state: `remediated_pending_verification`

**Smoke test phase:** Integrated with `FINDING_LIFECYCLE` — after the patch CR completes, verifies that the scanner connector's rescan endpoint is called (via connector test mode/mock for the scanner).

---

### 2.8 Compliance "Fix All" and Attestation

**What it does:** Remediate all failing assets for a CIS check in one action; attest non-automatable controls with evidence.

**Changes:**
- Compliance control row gains a "Fix all failing assets" button.
- Clicking creates a batch of CRs (one per failing asset for automatable checks) using the same mechanism as 2.1. All CRs are linked to the compliance control.
- For non-automatable controls (e.g., "Establish vulnerability management policy"): "Attest" button opens a drawer: evidence type (document, link, description), reviewer (user), attestation expiry date. Saves an attestation record.
- Control score includes attestations as passing evidence. Attestations show as "Attested" with expiry badge (warns 30 days before expiry).
- "Re-check this control" button triggers a targeted compliance audit job for that specific check. Does not wait for the full scheduled audit.

**API additions:**
- `POST /compliance/controls/:id/remediate-all` — creates batch CRs for all failing assets for this check
- `POST /compliance/controls/:id/attest` — creates attestation record
- `GET /compliance/controls/:id/attestations`
- `POST /compliance/controls/:id/recheck` — triggers targeted audit

**Smoke test phase:** `COMPLIANCE_FIX` — Launches EC2 with SSH password authentication enabled (known CIS violation). Runs compliance audit. Identifies the failing check. Uses "Fix all" to create a patch CR that disables password auth via `harden_ssh`. Executes. Triggers re-check. Verifies the control score improves and the check now passes.

---

### 2.9 Staging → Production Promotion

**What it does:** Project template that enforces a staging-first rollout with a configurable soak period before production CRs become executable.

**Changes:**
- Project creation gains a `template` option: "Staged rollout" pre-creates two phases: "Staging" (CRs targeting staging-tagged assets) and "Production" (CRs targeting prod-tagged assets).
- Production phase CRs have a `prerequisite_phase_id` and `soak_hours: 72` (configurable). The production CRs are locked (`locked_by_soak`) until:
  1. All staging CRs complete successfully.
  2. `soak_hours` have elapsed since the last staging CR completed.
- Project detail shows a countdown timer for the soak period. At soak completion, production CRs unlock and operators are notified.
- Soak can be cancelled (with reason) to promote immediately in emergencies.

**API additions:**
- `Project` model gains `template: "staged_rollout" | null`
- `ProjectPhase` model (ordered set of CRs within a project)
- `soak_hours`, `prerequisite_phase_id`, `soak_started_at`, `soak_completed_at` on `ProjectPhase`
- Background job: check soak timers every hour, unlock production CRs and notify when soak completes

**Smoke test phase:** `STAGED_ROLLOUT` — Launches two EC2s (one tagged `staging`, one tagged `prod`). Creates a staged rollout project. Creates OS upgrade CRs for staging and production phases. Executes the staging CR. Verifies the production CR is locked with a soak timer. Advances time (or sets `soak_hours: 0` for test). Verifies production CR unlocks. Executes production CR. Verifies both assets are upgraded.

---

### 2.10 External Ticket Closure

**What it does:** Completing a CR optionally closes the linked Jira issue or resolves the linked PagerDuty alert.

**Changes:**
- CR creation gains optional `external_links: [{type: "jira" | "pagerduty" | "servicenow", id: string}]`.
- On CR completion: for each external link, call the integration's close/resolve API.
- Supported actions: Jira → transition to "Done", PagerDuty → resolve alert, ServiceNow → close incident.
- Failure to close external ticket is non-blocking (logged as warning, not CR failure).

**Smoke test phase:** Part of integration test suite, not the EC2-based smoke tests (requires external system mocks).

---

## Phase 3 — Identity Security

*Users are currently subjects of reviews but not entities with managed access states.*

### 3.1 Emergency User Lockout IR Action

**What it does:** Locks a user account across all connected identity systems simultaneously in a single IR action.

**New CR type:** `emergency_user_lockout`

**Execution steps (ordered, in parallel where possible):**
1. Disable AD account (via Active Directory connector)
2. Attach IAM deny-all policy to user (via AWS connector) — non-destructive, reversible
3. Suspend Okta user (via Okta connector)
4. Revoke all active sessions (see 3.3)
5. Rotate API keys (mark old keys inactive, generate new ones in escrow)
6. Log forensic snapshot of user's last 24 hours of API activity (if CloudTrail connected)

**Reversal:** A paired `user_lockout_reversal` CR removes the deny policy, re-enables the AD account, re-activates Okta user. Does not restore the rotated API keys (they must be manually re-issued).

**IR playbook integration:** A new built-in "Isolate User" playbook fires `emergency_user_lockout` with the target user as context. Added alongside the existing "Isolate Host" playbook in the IR tab.

**Smoke test phase:** `USER_ISOLATE` — Creates a test IAM user with `s3:ListBuckets` permission. Verifies the user can list S3 buckets. Fires `emergency_user_lockout` targeting the test user. Verifies the user cannot list S3 buckets (deny-all policy applied). Verifies the lockout event appears in the asset timeline. Fires `user_lockout_reversal`. Verifies access is restored. Cleans up test IAM user.

---

### 3.2 Temporary User Suspension

**What it does:** Suspends a user account pending investigation without permanently revoking access.

**New CR type:** `user_suspension`

**Parameters:**
- `user_identifier`: email or username
- `duration_hours`: auto-reinstatement after this period (0 = manual reinstatement only)
- `reason`: required text field (audit trail)
- `preserve_sessions`: bool (default false — terminate sessions)

**Execution:** Disables account in all connected identity systems. Schedules a `user_suspension_reversal` CR for `duration_hours` from now if non-zero.

**Smoke test phase:** Part of `USER_ISOLATE` — tests suspension with auto-reinstatement.

---

### 3.3 Session Termination

**What it does:** Forces expiry of all active sessions for a user across identity systems.

**New CR type:** `terminate_user_sessions`

**Supported systems:**
- AWS: `iam:DeleteAccessKey` for access keys, STS session invalidation via `aws iam create-virtual-mfa-device` / policy attachment
- Okta: `POST /api/v1/users/{userId}/sessions` (clear all sessions)
- Active Directory: `Reset-ADAccountExpiry` / Kerberos ticket invalidation

**Smoke test phase:** Integrated with `USER_ISOLATE`.

---

### 3.4 Scope Reduction

**What it does:** Reduces a user's effective permissions without full revocation — temporarily demotes admin to read-only, restricts to specific IP ranges, or enforces MFA before write operations.

**New CR type:** `user_scope_reduction`

**Reduction modes:**
- `demote_to_readonly`: removes write/admin policies, attaches read-only equivalent
- `ip_restriction`: adds IAM condition limiting access to specific CIDR
- `mfa_required`: attaches IAM policy requiring MFA for all operations
- `service_restriction`: limits access to specific AWS services only

**Reversal:** Paired `user_scope_restoration` CR removes the scope reduction policies.

**Smoke test phase:** `USER_SCOPE` — Creates test IAM user with S3 full access. Applies `demote_to_readonly` scope reduction. Verifies user can list but not create S3 buckets. Applies `ip_restriction` with a non-matching CIDR. Verifies user cannot access at all. Restores scope. Verifies full access returns.

---

### 3.5 Time-Bound Access Grants

**What it does:** Access grants automatically expire after a configured duration with a rollback CR that fires at expiry.

**Changes:**
- CR creation (for any access-granting CR type: `attach_iam_policy`, `assign_ad_group`, `create_k8s_rolebinding`) gains optional `access_expiry_hours: N` field.
- When set, the platform schedules a paired rollback CR to execute at `created_at + expiry_hours`.
- The scheduled rollback CR appears in the project/CR list as "Scheduled: revoke access (auto)" with a countdown timer.
- Operators can extend expiry (creates a new rollback schedule) or cancel the rollback (access becomes permanent).
- All breakglass, contractor, and incident-response access grants should use this field.

**API additions:**
- `access_expiry_hours` on applicable CR types
- `scheduled_rollback_cr_id` on `ChangeRequest`
- Scheduled CR executor respects `execute_at` timestamp

**Smoke test phase:** `TIME_BOUND_ACCESS` — Creates test IAM user. Attaches a policy with `access_expiry_hours: 0.02` (approximately 1 minute). Verifies user has access. Waits 90 seconds. Verifies rollback CR has executed and user no longer has access. Verifies the rollback CR appears in the CR list as auto-generated.

---

### 3.6 MFA Enforcement as Remediation

**What it does:** Access review finding "no MFA" generates a CR that enforces MFA at the IdP level, not just as a recommendation.

**New CR type:** `enforce_mfa`

**Execution:** Attaches an IAM policy that denies all actions when MFA is not present (standard AWS IAM MFA enforcement pattern). For Okta: sets MFA required on the user's factor enrollment policy.

**Post-enforcement:** User can still authenticate but all operations require MFA. They are prompted to enroll on next login. After enrollment, the deny policy becomes a no-op (MFA is present).

**Smoke test phase:** `MFA_ENFORCE` — Creates test IAM user without MFA. Access review finds the user. Creates `enforce_mfa` CR. Executes. Attempts an S3 operation as the user without MFA — verifies it's denied. Sets up MFA on the account. Verifies operations succeed.

---

## Phase 4 — Proactive Hardening

*Shift from purely reactive to security engineering initiatives.*

### 4.1 Hardening Project Templates

**What it does:** Pre-built project structures for common security engineering initiatives.

**Templates:**
1. **Seccomp Rollout** — Stages: discover (appdiscovery), learn (seccomp audit mode, 7 days), review policy (human), apply staging, soak (72h), apply production.
2. **Microsegmentation** — Stages: map traffic (deep_discover on all services), define policies (AI-assisted), pilot (subset of assets), validate, full rollout.
3. **AppArmor Rollout** — Stages: discover apps, apply complain mode, collect violations, generate enforce profile, apply staging, soak, apply production.
4. **Least-Privilege IAM** — Stages: audit current permissions (CloudTrail analysis), generate minimal policy set (AI from CloudTrail), human review, apply to staging, soak, apply production.
5. **Zero-Trust Network** — Stages: map service dependencies (deep_discover), define trust boundaries, apply default-deny security groups, add explicit allow rules per dependency, verify connectivity.

**Implementation:** Templates are project archetypes stored in the backend. Creating a project from a template pre-populates phases, CR stubs, and verification check configurations. The AI panel is pre-seeded with the template's goal context.

**Smoke test phase:** Part of `SECCOMP_PIPELINE` and `MICROSEG` (see below).

---

### 4.2 Generalized Learn → Audit → Enforce Pipeline

**The premise:** Every whitelist-based security policy requires understanding a known-good baseline before enforcement. Applying a seccomp profile, AppArmor policy, iptables ruleset, or auditd rule set without first observing normal behavior guarantees either false positives (blocking legitimate operations) or false negatives (policy too permissive to provide protection). The platform must make the observe-before-enforce pattern the default path, not an afterthought.

This pipeline applies to all policy-based hardening controls on both Linux and Windows. The mechanism differs by platform but the stages are identical.

**Linux controls:**

| Control | Learn mechanism | Enforce mechanism | Drift signal |
|---------|----------------|-------------------|--------------|
| **Seccomp** | `SCMP_ACT_LOG` profile (kernel logs syscalls, doesn't block) | `SCMP_ACT_ERRNO` profile loaded into systemd unit | New syscall not in baseline |
| **AppArmor** | `aa-complain` mode (logs denials without blocking) | `aa-enforce` mode | New denial in kern.log |
| **SELinux** | Permissive mode + `audit2allow` (generates policy from audit log) | `setenforce 1` + loaded module | New AVC denial |
| **iptables** | `--log` target (logs matching traffic without dropping) | `--drop` target on same rules | New logged connection that would be dropped |
| **Auditd** | Short baseline run (collect what events fire under normal load) | Deploy full rule set; alert on rule violations | New event type not in baseline |
| **FIM (AIDE)** | `aide --init` (build baseline database of file hashes) | `aide --check` (compare to baseline; any diff = alert) | File changed since last baseline |
| **eBPF/Falco** | Falco in `output_only` mode (log rule matches without acting) | Falco in enforcement mode; Cilium network policy | New rule match not seen in learn period |

**Windows controls (equivalent pipeline, different mechanisms):**

| Linux control | Windows equivalent | Learn mechanism | Enforce mechanism | Drift signal | Go agent status |
|---|---|---|---|---|---|
| **AppArmor / SELinux** | **AppLocker** | Audit mode rules (event log 8003/8006 — would-have-blocked) | Enforce mode rules (event 8004 = blocked) | New audit event type not in baseline | ✅ Real (`winharden`) |
| **AppArmor / SELinux (modern)** | **WDAC (Windows Defender Application Control)** | Audit mode policy (event 3076 — would-have-blocked) | Enforce mode policy (event 3077 = blocked) | New event 3076 not in baseline | ❌ Missing — needs new Go command |
| **Seccomp** | **Attack Surface Reduction (ASR) rules** | Audit mode per rule (event 1122 — would-have-blocked) | Block mode per rule (event 1121 = blocked) | New audit event not in baseline | ❌ Missing — needs new Go command |
| **Seccomp (kernel calls)** | **Win32k lockdown via Job Objects** | N/A (binary flag, no learn mode) | Enable `PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY` | Service crash indicates misconfiguration | ❌ Missing |
| **iptables** | **Windows Firewall with Advanced Security** | Log enabled (logging dropped + successful connections) | Add blocking rules; existing log entries define allowlist | New logged connection not in allowlist | ✅ Real (`winharden`) |
| **Kernel module blacklisting** | **WDAC Driver Blocklist** | Audit: check loaded drivers vs. recommended block list | WDAC kernel-mode policy blocking unsigned/vulnerable drivers | New driver loaded that should be blocked | ❌ Missing |
| **Mount hardening** | **AppLocker path rules + NTFS ACLs** | AppLocker audit mode on execution paths | AppLocker enforce + `icacls` to restrict write-then-execute paths | New audit event from path that should be restricted | ✅ Partial (AppLocker real, ACL automation missing) |
| **Auditd** | **Windows Security Audit Policy** | Run with minimal audit policy; capture events for baseline period | Full `auditpol` rule set derived from baseline + CIS profile | New event subcategory not in baseline | ✅ Real (`winharden`) |
| **FIM (AIDE)** | **Sysmon file integrity + SACL auditing** | Deploy Sysmon with FileCreate/FileModify events; collect baseline | SACL on sensitive paths; alert on events not in baseline | New FileCreate/Modify event on monitored path | ❌ Missing — Sysmon deployment not implemented |
| **eBPF / ETW** | **ETW + Sysmon process/network events** | Sysmon in log-only mode; collect process creation + network events | Sysmon rules; WFP for network enforcement | New event type/source not in baseline | ❌ Missing — Sysmon/ETW monitoring not implemented |
| **SSH hardening** | **RDP hardening + OpenSSH (Windows)** | Audit current config via `netsh`, `reg query`, `auditpol` | Apply CIS RDP hardening; enforce NLA, TLS 1.2+, idle timeout | Config drift detected by weekly posture audit | ✅ Real (RDP: `winharden`, SSH: crossplatform) |
| **PAM** | **Local Security Policy / FGPP** | Audit current `net accounts` / `secedit` export | Apply via `secedit /configure` or `net accounts` with CIS values | Config drift detected by weekly posture audit | ✅ Real (registry-based subset via `winharden`) |

**Windows-specific hardening with no direct Linux equivalent:**

| Control | What it does | Learn mode | Enforce | Go status |
|---------|-------------|-----------|---------|-----------|
| **LAPS** | Randomizes local admin password, stores in AD | N/A (enable/disable) | Enable + configure password age/length | ✅ Real |
| **Credential Guard** | Protects NTLM hashes + Kerberos tickets via VBS | N/A (enable/disable) | Enable DeviceGuard + VBS + UEFI lock | ✅ Real |
| **BitLocker** | Full-disk encryption | N/A (enable/disable) | Enable with TPM/PIN + export recovery key | ✅ Real |
| **PowerShell CLM** | Restricts PowerShell to safe API surface | Audit mode: log CLM violations | Set `__PSLockdownPolicy = 4` | ✅ Real |
| **SMB hardening** | Disable SMBv1, require signing | Audit: check SMB version via registry | Disable SMBv1 + require signing | ✅ Real |
| **SCHANNEL/TLS** | Disable old TLS/SSL versions | Audit: query current SCHANNEL registry | Disable SSL 2.0/3.0, TLS 1.0/1.1 via registry | ✅ Real |
| **Windows Defender ASR** | Block 16 attack surface patterns (macros, credential stealing, etc.) | Audit mode per rule | Block mode per rule | ❌ Missing |
| **HVCI** | Hypervisor-protected code integrity; blocks unsigned kernel code | N/A (enable/disable, requires reboot) | Enable via registry + UEFI | ❌ Missing |
| **Windows Defender settings** | AV config, exclusions, real-time protection, cloud protection | Audit current state | Configure via PowerShell `Set-MpPreference` | ❌ Missing |

**Common pipeline stages (all control types):**

**Stage 1 — Observe:** CR type `<control>_learn` applies the audit/log mode for `duration_seconds` (default 300). During this window, the operator should exercise all normal application workflows — startup, steady state, routine maintenance operations. The CR result contains the raw observation: syscall list, AppArmor denials, auditd events, network connections, file change log.

**Stage 2 — Baseline store:** The observation is persisted as a `PolicyBaseline` record. The CR detail displays the observation in human-readable form: "47 unique syscalls observed", "3 network connections to external hosts", "12 files modified in /var/log/ during baseline period".

**Stage 3 — AI-assisted policy generation:** "Generate enforce policy" button in the CR detail sends the baseline to the AI analysis endpoint. AI generates a minimal policy allowing only what was observed, annotated with reasoning per decision. For seccomp: JSON syscall allowlist. For AppArmor: `/etc/apparmor.d/` profile. For iptables: allow rules. For AIDE: baseline database is the policy itself.

**Stage 4 — Human review:** The generated policy is displayed in a diff view. Operator can accept as-is, edit individual rules, or reject and re-run the learn phase with a longer window. The review is recorded with reviewer identity and timestamp.

**Stage 5 — Staged enforce:** CR `<control>_enforce` applies the policy to staging first (if staged rollout project), then production. Application-aware verification checks run immediately after. If any check fails, the policy is automatically rolled back and the failure is annotated on the PolicyBaseline ("enforcement failed: nginx returned 502 after seccomp apply").

**Stage 6 — Drift detection:** Weekly scheduled `<control>_drift_check` compares a fresh short-duration observation against the stored baseline. New behaviors that would be blocked by the current policy surface as drift alerts: "nginx now uses syscall `mprotect` which is not in the applied seccomp profile." Drift is not automatically remediated — it surfaces for human judgment (application legitimately changed? or compromise?).

**New agent commands required (extending Phase 4.1 scope):**

| Command | Package | Description |
|---------|---------|-------------|
| `seccomp_learn` | `ossecurity` | `SCMP_ACT_LOG` profile on target service; collect syscalls for `duration_seconds` |
| `selinux_permissive_baseline` | `ossecurity` | Switch to permissive, run `ausearch -m avc` for duration, return AVC denials; `audit2allow` output included |
| `iptables_log_baseline` | `ossecurity` | Add `--log` rules mirroring the intended drop rules; collect logged connections for duration |
| `auditd_baseline` | `ossecurity` | Run without alerting rules for duration; collect all event types and frequencies |
| `fim_init` | `ossecurity` | `aide --init`; stores baseline DB; returns file count and hash algorithm |
| `fim_check` | `ossecurity` | `aide --check`; returns diff from baseline (modified, added, removed files) |
| `falco_output_only` | `ebpf` | Start Falco with rules in `output` mode (log, don't act); collect rule matches for duration |

**Note:** `apparmor_complain` is already supported by the existing `apparmor_linux.go` implementation (complain mode exists). `fim_init` and `fim_check` map to the existing `fim_linux.go` `init`/`check` actions. The new commands above fill the gaps for the other control types.

**Smoke test phase:** `SECCOMP_PIPELINE` covers seccomp end-to-end. Individual learn phases for AppArmor, iptables, and FIM are tested as part of the `OSSEC_WIRE` phase (which verifies the executors dispatch correctly). Full pipeline tests (learn → generate → enforce → drift) for each control type are in the `HARDENING_PIPELINE` smoke phase below.

---

### 4.3 AI Policy Generation from Discovery

**What it does:** AI generates minimal security policies from discovered application behavior data.

**Inputs used:**
- `deep_discover` result: open files, outbound connections, env var names, runtime deps (`.so` libraries), syscall patterns (from `seccomp_learn`).
- Application type (nginx, postgres, node, python) detected from runtime deps and config intelligence.

**Outputs:**
- Seccomp profile: JSON list of allowed syscalls for this application's observed behavior, with rationale per syscall group.
- AppArmor profile: `/etc/apparmor.d/` format, scoped to observed file access paths and network operations.
- Security group rules: allow list derived from observed outbound connections and listening ports.

**Integration:** "Generate policy" button appears in:
- `seccomp_learn` CR result (generates seccomp profile)
- `apparmor_complain` CR result (generates AppArmor enforce profile)
- `deep_discover` CR result (generates security group rules)
- Microsegmentation project wizard (generates network policies for all discovered services)

**Smoke test phase:** Part of `SECCOMP_PIPELINE` and `MICROSEG`.

---

### 4.4 Policy Drift Detection

**What it does:** Detects when application behavior has changed in ways that would be blocked by an applied policy.

**Mechanism:**
- When a `configure_seccomp` or `configure_apparmor` CR completes, the platform records the policy baseline: which syscalls are allowed, which file paths are permitted, which network connections are allowed.
- A scheduled `drift_check` agent job (weekly by default, configurable) runs `seccomp_learn`/`apparmor_complain` for a short duration (60 seconds) and compares to the baseline.
- If new syscalls or file accesses are observed that are not in the current allow-list, a drift alert is created:
  - Finding-like entry in the Compliance page with: asset, policy type, new observed behaviors, "These would be blocked by the current policy"
  - Notification to the security operator who applied the policy
  - Recommended action: review and update the policy, or investigate the new behavior as potential compromise

**API additions:**
- `PolicyBaseline` model: `cr_id`, `asset_id`, `policy_type`, `allow_list`, `created_at`
- `DriftAlert` model: new observed behaviors, detected_at, status
- Background scheduler: weekly drift check per active policy baseline

**Smoke test phase:** `POLICY_DRIFT` — Applies a seccomp profile to nginx (generated from learn mode). Verifies baseline is recorded. Installs an additional service that causes nginx to use an additional syscall (simulated by modifying nginx config to run a script). Triggers an immediate drift check. Verifies a drift alert is created with the new syscall listed.

---

### 4.5 Threat Model Linkage

**What it does:** Projects and CRs can reference a threat model or risk context, closing the gap between security architecture and execution.

**Changes:**
- `Project` gains `risk_context: {type: "threat_model" | "pentest_finding" | "compliance_requirement" | "risk_assessment", description: string, reference_url: string}`.
- CR creation inherits the parent project's risk context (shown read-only, can be overridden per CR).
- Compliance controls and findings can reference the same risk context.
- Asset detail "Activity" timeline shows risk context for CRs that have it.

**Smoke test phase:** Tested as part of `SECCOMP_PIPELINE`.

---

## Smoke Test Phase Catalog

The following phases are added to `backend/tests/smoke/test_aws_live.py` and run via `run_on_ec2.py`. Each requires a live EC2 runner and real AWS infrastructure. All phases clean up after themselves.

### Infrastructure Setup Shared Across Phases

Many phases require specific software installed on the target EC2. The smoke test infrastructure introduces a `_INSTALL_<PROFILE>_LINUX` pattern (extending the existing `_INSTALL_AUTO_APPS_LINUX`):

- `_INSTALL_NGINX_LINUX` — installs nginx 1.18, enables service, starts serving on port 80
- `_INSTALL_OLD_KERNEL_LINUX` — not applicable (kernel version is AMI-dependent; use Amazon Linux 2 AMI which ships with kernel 5.10 for upgrade testing)
- `_INSTALL_FLASK_APP_LINUX` — installs Python 3.9, Flask app on port 5000, connects to postgres
- `_INSTALL_POSTGRES_LINUX` — installs PostgreSQL 14, creates test DB `smokedb`, creates test user `smokeuser`
- `_INSTALL_INSECURE_SSH_LINUX` — enables `PasswordAuthentication yes` in sshd_config (for compliance test)

### Phase: KERN_PATCH

**Purpose:** Verify that `agent_linux_patch` can apply security patches to a real running system and correctly verify the outcome.

**Setup:**
- EC2: Amazon Linux 2 with `yum-cron` installed (ensures packages to update exist)
- Pre-state: capture installed kernel version via SSM

**Test steps:**
1. Fire `agent_linux_patch` CR with `patch_mode: "security_only"`
2. Add application-aware verification check: `service` (sshd → active), `command` (`uname -r` output recorded)
3. Execute CR
4. Verify CR completes with status `completed`
5. Verify pre-snapshot was taken (if `snapshot_before: true`)
6. SSM: compare `yum list updates --security` before and after — count should decrease
7. Verify all verification checks pass

**New agent capability required:** None — `linuxpatch` command exists. `snapshot_before` chaining is Phase 1.4.

**Assertions:**
- CR status = `completed`
- Security update count reduced
- All application-aware verification checks pass
- Pre-change snapshot exists and is tagged correctly

---

### Phase: OS_UPGRADE

**Purpose:** Verify full major OS version upgrade lifecycle including staging→prod promotion and post-upgrade verification.

**Setup:**
- EC2 A: tagged `env:staging`, running Amazon Linux 2 (kernel ~5.10)
- EC2 B: tagged `env:prod`, running Amazon Linux 2

**Test steps:**
1. Create a "Staged Rollout" project via template
2. Create `agent_linuxupgrade` CR for EC2 A (staging phase) with:
   - `target_version: "amazon_linux_2023"`
   - `snapshot_before: true`
   - Verification checks: `kernel_version >= 6.1` (AL2023 kernel), `service` (sshd → active)
3. Execute staging CR
4. Verify staging EC2 is running AL2023 (SSM: `cat /etc/os-release`)
5. Verify soak timer is active for production phase
6. Set `soak_hours: 0` (test mode) — production CRs unlock
7. Create `agent_linuxupgrade` CR for EC2 B (prod phase)
8. Execute production CR
9. Verify production EC2 is running AL2023
10. Verify pre-change snapshots exist for both

**New agent capability required:** `linuxupgrade` command exists. Staged rollout project is Phase 2.9.

**Assertions:**
- Both CRs complete successfully
- Both ECs report AL2023 kernel
- Staged rollout project shows both phases completed
- Pre-change snapshots exist, tagged with CR ID

---

### Phase: SECCOMP_PIPELINE

**Purpose:** Verify the full seccomp hardening lifecycle: discover → learn → generate policy → apply → verify → detect drift.

**Setup:**
- EC2: nginx installed and serving on port 80

**Test steps:**
1. **Discovery:** Fire `agent_appdiscovery` CR. Verify nginx is discovered as a workload.
2. **Learn:** Fire `configure_seccomp` CR in learn mode:
   - `mode: "learn"`, `target_service: "nginx"`, `duration_seconds: 60`
   - During the 60 seconds, SSM sends 5 HTTP requests to localhost:80 to generate syscall activity
3. Verify learn CR result contains a syscall list (at minimum: `read`, `write`, `accept4`, `epoll_wait`, `sendfile`)
4. **Generate policy:** Call AI policy generation endpoint with the learn result. Verify a seccomp profile JSON is returned.
5. **Apply staging:** Fire `configure_seccomp` CR in enforce mode with the generated profile.
   - Verification check: `http` (localhost:80 → 200)
6. Verify nginx still serves HTTP (profile not too restrictive)
7. SSM: attempt to run `strace nginx` from inside the host — verify it is blocked by seccomp (strace uses ptrace syscall)
8. **Drift detection:** Modify nginx config to enable proxy module (uses additional syscalls). Fire immediate drift check. Verify drift alert is created.
9. **Cleanup:** Disable seccomp profile on nginx.

**New agent capability required:**
- `seccomp_learn` mode in the `ossecurity` Go package — add `SCMP_ACT_LOG` profile application and syscall collection
- `configure_seccomp` executor Python file is currently a stub — needs real implementation dispatching to agent

**Assertions:**
- Learn CR returns non-empty syscall list
- AI generates valid seccomp profile JSON
- Enforce CR completes without breaking nginx
- nginx HTTP 200 after enforce mode applied
- `strace` (ptrace syscall) is blocked
- Drift alert created when new behavior detected

---

### Phase: MICROSEG

**Purpose:** Verify microsegmentation workflow: map service dependencies, apply default-deny with explicit allows, verify topology enforcement.

**Setup:**
- 3 EC2s:
  - `nexplane-smoke-nginx`: nginx reverse proxy, connects to flask on port 5000
  - `nexplane-smoke-flask`: Flask app on port 5000, connects to postgres on port 5432
  - `nexplane-smoke-postgres`: PostgreSQL on port 5432

**Pre-state verification:**
- SSM on nginx EC2: `curl http://flask_ip:5000` → 200 (baseline: nginx can reach flask)
- SSM on nginx EC2: `nc -z postgres_ip 5432` → success (baseline: nginx can also reach postgres — BAD)
- SSM on flask EC2: `nc -z postgres_ip 5432` → success (expected)

**Test steps:**
1. Run `deep_discover` on all 3 assets. Verify outbound connections are captured.
2. **Define microsegmentation project** using the template:
   - Allow: nginx → flask (port 5000)
   - Allow: flask → postgres (port 5432)
   - Deny: nginx → postgres (port 5432)
3. Create 3 `update_security_group` CRs:
   - Flask SG: allow inbound from nginx SG on 5000 only
   - Postgres SG: allow inbound from flask SG on 5432 only, remove any rule allowing nginx SG
4. Execute CRs
5. **Verify enforcement:**
   - SSM on nginx: `curl http://flask_ip:5000` → 200 ✓ (still works)
   - SSM on nginx: `nc -z -w3 postgres_ip 5432` → timeout ✓ (now blocked)
   - SSM on flask: `nc -z postgres_ip 5432` → success ✓ (still works)
6. Verify cross-asset timeline on postgres shows the security group change
7. Rollback: restore original security groups

**New agent capability required:** None — `update_security_group` exists. `deep_discover` exists.

**Assertions:**
- nginx → flask: allowed post-segmentation
- nginx → postgres: blocked post-segmentation (connection times out)
- flask → postgres: still allowed
- All CRs complete with `completed` status
- Rollback restores original connectivity

---

### Phase: BULK_PATCH

**Purpose:** Verify bulk CR creation, bulk approval, and rolling-batch execution across a fleet.

**Setup:**
- 3 EC2s with identical Amazon Linux 2 profile and yum security updates available

**Test steps:**
1. Select all 3 assets in the asset inventory (via API)
2. Call `POST /change-requests/batch` with:
   - `change_type: "agent_linux_patch"`, `patch_mode: "security_only"`, `snapshot_before: true`
   - `finding_ids: [test_finding_id]` (linked finding)
   - All 3 asset IDs
3. Verify 3 CRs created with the same `batch_id`
4. Call `POST /change-requests/bulk-approve` with all 3 CR IDs
5. Execute each CR (sequentially, 1-at-a-time rolling batch)
6. Verify all 3 CRs complete
7. Verify the linked finding auto-closes (state = `remediated`)
8. Verify 3 pre-change snapshots exist

**Assertions:**
- 3 CRs created with shared `batch_id`
- Bulk-approve accepts all 3 in one call
- All 3 CRs complete with `completed` status
- Finding state = `remediated`
- 3 EBS snapshots exist, tagged with batch CRs

---

### Phase: FINDING_LIFECYCLE

**Purpose:** Exercise the complete finding lifecycle: ingest → triage → remediate → verify → close → reopen.

**Setup:**
- EC2 with a known old package version (e.g., `openssl` 1.0.x)
- Test finding record inserted: CVE-2023-0286, affecting the EC2's asset ID

**Test steps:**
1. Verify finding appears in `/remediation/findings` with state `open`
2. Check blast radius: verify affected asset count = 1
3. Create patch CR from finding action, with `finding_ids: [test_finding_id]`
4. Verify approval queue shows CVSS score and finding link
5. Approve and execute
6. Verify finding state = `remediated_pending_verification`
7. Trigger re-scan via mock scanner connector
8. Simulate scanner reporting CVE resolved → verify finding state = `remediated`
9. Simulate scanner re-detecting CVE (regression) → verify finding state = `reopened`

**Assertions:**
- Finding lifecycle transitions: open → remediated_pending_verification → remediated → reopened
- Approval queue card shows CVSS score
- CR detail shows linked finding
- Re-scan is triggered on CR completion
- Regression detection works

---

### Phase: USER_ISOLATE

**Purpose:** Verify emergency user lockout, suspension, scope reduction, and time-bound access.

**Setup:**
- Test IAM user: `nexplane-smoke-test-user` with `s3:ListBuckets` permission
- AWS credentials for the test user captured for verification calls

**Test steps:**
1. Verify test user can list S3 buckets (baseline)
2. **Emergency lockout:** Fire `emergency_user_lockout` CR targeting the test user
3. Verify test user cannot list S3 buckets (deny policy applied)
4. Verify lockout appears in asset timeline for the test user's asset record
5. **Reversal:** Fire `user_lockout_reversal` CR
6. Verify test user can list S3 buckets again
7. **Scope reduction:** Fire `user_scope_reduction` CR with `mode: "demote_to_readonly"`
8. Verify test user can list but not create S3 buckets
9. **Restoration:** Fire `user_scope_restoration`
10. Verify full access restored
11. **Time-bound access:** Attach an additional policy with `access_expiry_hours: 0.02` (≈1 minute)
12. Wait 90 seconds
13. Verify rollback CR has auto-executed and policy removed
14. Cleanup: delete test IAM user

**Assertions:**
- Emergency lockout: all S3 operations denied
- Lockout reversal: S3 list succeeds
- Scope reduction: list succeeds, put/delete denied
- Scope restoration: full access
- Time-bound access: policy auto-removed after expiry

---

### Phase: MAINT_WINDOW

**Purpose:** Verify maintenance window enforcement with advisory and hard modes, and emergency override.

**Setup:**
- EC2 tagged `env:prod`
- Maintenance window: hard enforcement, Monday–Friday 11pm–3am (not currently active)

**Test steps:**
1. Create patch CR for the prod-tagged EC2
2. Attempt to execute: verify CR is blocked with `blocked_by_maintenance_window`
3. Create same CR with `priority: "emergency"` and reason "Zero-day vulnerability"
4. Verify emergency CR executes successfully
5. Verify audit trail contains the override record with reason
6. Change maintenance window to `advisory` mode
7. Create another patch CR
8. Attempt to execute: verify it succeeds with a warning in the audit trail (not blocked)

**Assertions:**
- Hard enforcement: execution blocked
- Emergency flag: bypasses hard enforcement
- Audit trail: override reason recorded
- Advisory mode: execution proceeds with warning

---

### Phase: COMPLIANCE_FIX

**Purpose:** Verify compliance "fix all" bulk remediation and re-check.

**Setup:**
- EC2 with `PasswordAuthentication yes` in `/etc/ssh/sshd_config` (CIS 5.2.8 violation)

**Test steps:**
1. Run compliance audit (CIS Level 1)
2. Verify CIS 5.2.8 (SSH password auth) appears as failing
3. Use "Fix all" to create a `harden_ssh` CR for the failing asset
4. Approve and execute
5. SSM: verify `PasswordAuthentication no` in sshd_config
6. Trigger targeted re-check of CIS 5.2.8
7. Verify control score for 5.2.8 improves to passing

**Assertions:**
- Compliance audit detects the violation
- `harden_ssh` CR applies the fix
- SSH password auth disabled in config
- Re-check shows control passing

---

### Phase: WIN_OSSEC_WIRE

**Purpose:** Verify all 13 Windows hardening executors dispatch to the Go agent and produce real side effects on a live Windows EC2.

**Setup:** EC2: Windows Server 2022 Base AMI, Nexplane agent installed (v0.3+), SSM enabled.

**Test steps (one per executor, verified via PowerShell over SSM):**

1. `configure_windows_audit_policy` → SSM: `auditpol /get /category:*` → at least one subcategory set to `Success and Failure`
2. `harden_rdp` → SSM: `reg query HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp /v UserAuthentication` → value = `1` (NLA required)
3. `harden_smb` → SSM: `Get-SmbServerConfiguration | Select EnableSMB1Protocol` → `False`
4. `harden_tls_protocols` → SSM: `reg query "HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.0\Server" /v Enabled` → `0`
5. `harden_registry` → SSM: `reg query HKLM\SYSTEM\CurrentControlSet\Control\Lsa /v NoLMHash` → `1`
6. `configure_laps` → SSM: `reg query HKLM\SOFTWARE\Policies\Microsoft Services\AdmPwd /v AdmPwdEnabled` → `1`
7. `deploy_applocker_policy` (audit mode) → SSM: `Get-AppLockerPolicy -Effective` → non-empty policy returned
8. `configure_windows_firewall` (block port 9999 inbound) → SSM: `netsh advfirewall firewall show rule name=all | findstr 9999` → rule present
9. `enforce_powershell_clm` → SSM: `$ExecutionContext.SessionState.LanguageMode` (in a new session) → `ConstrainedLanguage`
10. `enable_credential_guard` → SSM: `reg query HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard /v EnableVirtualizationBasedSecurity` → `1`
11. `audit_windows_patch_status` → assert result contains real `installed_patches` array (not hardcoded empty)
12. `audit_scheduled_tasks` → assert result contains real task inventory (not hardcoded `[]`)
13. `apply_windows_patches` (security_only, dry_run: true) → assert result shows available patches count (not always `0`)

**Note on BitLocker (enable_bitlocker):** Requires TPM-enabled instance. Skip on EC2 base AMI (no TPM); test on a vTPM-enabled instance or verify the Go agent's error handling returns a clear "TPM not available" message rather than a false success.

**New agent capability required:** None — all Windows Go commands exist in `winharden_windows.go` and `winpatch_windows.go`.

**Assertions:**
- All 12 CRs (excluding BitLocker) complete with `completed` status
- PowerShell SSM verification confirms real registry/system state changed
- Rollback CRs restore pre-change state via registry snapshots

---

### Phase: HARDENING_PIPELINE

**Purpose:** Verify the generalized learn → baseline → AI-generate → enforce → drift pipeline for AppArmor, iptables, and FIM. (Seccomp covered by SECCOMP_PIPELINE.)

**Setup:**
- EC2 A (AppArmor): Ubuntu 22.04 (AppArmor enabled by default), nginx installed and serving
- EC2 B (iptables): Amazon Linux 2, Flask app installed, making outbound connection to EC2 C on port 5432
- EC2 C (FIM): Amazon Linux 2, static web content in `/var/www/html/`

**AppArmor pipeline (EC2 A):**
1. `configure_apparmor` CR with `mode: "complain"`, `service: "nginx"`, `duration_seconds: 60`
2. SSM: send 10 HTTP requests to nginx during the 60s window to generate access patterns
3. Verify CR result contains observed file accesses and capabilities
4. Call AI generate endpoint with the result → receive AppArmor enforce profile
5. `configure_apparmor` CR with `mode: "enforce"`, `profile_content: <generated>`, verify checks: `http` (localhost:80 → 200)
6. Verify nginx still serves; verify a blocked file access attempt is denied

**iptables pipeline (EC2 B):**
1. `configure_host_firewall` CR with `action: "log_baseline"`, `duration_seconds: 60` on Flask app
2. SSM: trigger several outbound requests during window (Flask → postgres, Flask → external)
3. Verify CR result contains observed outbound connections
4. Call AI generate → receive iptables allow rules (flask → postgres, block all else outbound)
5. `configure_host_firewall` CR with `action: "apply_rules"`, generated rules
6. Verify flask → postgres still works; verify flask → unexpected external is blocked

**FIM pipeline (EC2 C):**
1. `setup_file_integrity_monitoring` CR with `action: "init"` — builds AIDE baseline
2. Verify baseline DB exists (SSM: `ls /var/lib/aide/aide.db`)
3. SSM: add a file to `/var/www/html/unexpected.php` (simulates web shell)
4. `setup_file_integrity_monitoring` CR with `action: "check"` — compare to baseline
5. Verify CR result contains the added file as a violation
6. Verify drift alert is created in the platform
7. Cleanup: remove unexpected.php, re-init baseline

**Assertions:**
- AppArmor: enforce mode applied, nginx still serves, profile loaded
- iptables: Flask → postgres allowed, Flask → external blocked
- FIM: unexpected file detected and surfaced as drift alert

---

### Phase: POLICY_DRIFT

**Purpose:** Verify drift detection catches when application behavior changes would be blocked by an applied seccomp policy.

**Setup:**
- EC2 with nginx installed
- Seccomp profile applied (from SECCOMP_PIPELINE or fresh apply)

**Test steps:**
1. Verify seccomp profile baseline is recorded
2. Trigger an immediate drift check (0 new behaviors) → verify no drift alert
3. SSM: add nginx stub module that uses an additional syscall (`mmap` variant not in baseline)
4. Reload nginx
5. Trigger immediate drift check
6. Verify drift alert created: shows the new syscall, which would be blocked
7. Notification check: verify the security operator who applied the policy is notified

**Assertions:**
- Initial drift check: no alerts
- After config change: drift alert created with correct syscall
- Notification sent to policy owner

---

## Phase 5 — Identity Connectors and Non-User Credential Lifecycle

*Future work — requires dedicated brainstorming session before implementation planning. Captured here to establish scope and smoke test requirements.*

### 5.1 Identity Connector Buildout

Phase 3 (Identity Security) implements user isolation, suspension, and time-bound access, but only against currently-connected identity systems. Most production environments have multiple identity planes that Nexplane doesn't yet manage:

**Connectors needed:**

| Connector | Capabilities needed |
|-----------|-------------------|
| **Active Directory (on-prem)** | User lifecycle (create/disable/delete), group management, GPO application, password policy, account lockout, Kerberos ticket invalidation |
| **Azure AD / Entra ID** | Cloud user lifecycle, conditional access policy, MFA state management, app role assignments, privileged identity management (PIM) |
| **Okta (extended)** | Current connector needs: factor enrollment enforcement, session policy management, lifecycle state machine (staged/active/suspended/deprovisioned), group push |
| **LDAP (generic)** | Read users/groups, modify attributes, bind for authentication testing |
| **GitHub / GitLab** | Organization member management, repo permission management, deploy key rotation |
| **Kubernetes RBAC** | RoleBinding/ClusterRoleBinding lifecycle, ServiceAccount management |

**Phase 3 CR types must be extended to cover these connectors.** `emergency_user_lockout` today only covers AWS IAM; it must fan out to AD, Azure AD, Okta, and GitHub in parallel, tolerating partial failures (if AD is unreachable, lock IAM and Okta and report AD as failed, don't abort).

### 5.2 Non-User Credential Lifecycle

Human accounts are one category of identity. The broader identity problem includes non-human credentials that have their own lifecycle, expiry, and rotation needs:

| Credential type | Lifecycle events | Current support |
|----------------|-----------------|-----------------|
| **API keys** (AWS IAM, GCP service accounts, Azure service principals) | Creation, rotation, expiry detection, revocation | `rotate_iam_key` exists for AWS; others missing |
| **JWT tokens** | Signing key rotation, expiry, algorithm migration | None |
| **SSH keys** | Authorized key management, key age enforcement, orphaned key detection, rotation | `credrotation` Go command exists; Python executor stub |
| **SSL/TLS certificates** | Expiry detection (30/14/7 day warnings), renewal (ACME/internal CA), deployment to services, pinning update | `manage_tls_certificates.py` exists but is a stub |
| **Database credentials** | Password rotation, connection string update in application config, session invalidation | `credrotation` Go command exists; Python executor stub |
| **Service account tokens** | Kubernetes service account token rotation, expiry enforcement | None |
| **Secrets in vaults** | HashiCorp Vault lease renewal, secret version rotation | None |

**Detection before rotation:** Before rotating any credential, the platform must know what systems consume it. Rotating an API key used by 12 microservices without updating them all breaks production. The credential lifecycle workflow:

1. **Discover:** Which services use this credential? (from `deep_discover` env var names, `agent_appdiscovery` config files, CloudTrail API call analysis)
2. **Plan:** Generate a rotation CR that updates the credential AND updates all consuming services' configs
3. **Stage:** Apply to one consuming service, verify it works
4. **Rollout:** Update remaining consumers in batches
5. **Revoke:** Revoke the old credential only after all consumers are confirmed updated

### 5.3 Expiry Monitoring

**New service:** Certificate and credential expiry monitor. Runs daily, checks:
- All TLS certificates on all known assets (from `manage_tls_certificates` records + direct TLS probes on known ports)
- All IAM access keys with `LastRotatedDate` > 90 days
- All SSH keys in `authorized_keys` with creation date > policy threshold
- All Kubernetes service account tokens approaching expiry
- All secrets in connected Vault instances approaching lease expiry

**Output:** Findings in the vulnerability/findings queue, same lifecycle as CVE findings. SLA clock starts at 30-day warning. Escalates at 14 days. Emergency-priority auto-generated CR at 7 days.

### Smoke Test Phase: IDENTITY_LIFECYCLE

**Purpose:** Exercise the full identity lifecycle for human users and non-user credentials against real Azure AD infrastructure.

**Prerequisites:** Azure AD tenant with Nexplane service principal, AD connector configured, test user group created.

**Setup (provisioned at test start, torn down after):**
- Azure AD test users: `smoke-user-1@domain`, `smoke-user-2@domain`, both in `nexplane-smoke-test` group
- Test IAM access key: `nexplane-smoke-api-key` with read-only S3 access
- Test SSH key: `nexplane-smoke-ssh-key` added to EC2 `authorized_keys`  
- Test TLS certificate: self-signed cert expiring in 5 days (for expiry detection test)
- Test database: PostgreSQL on EC2 with `smokeuser` credentials in app config file

**Human user lifecycle:**

1. **Onboard:** Create `smoke-user-3@domain` via Azure AD connector CR. Verify user exists in Azure AD. Assign to `nexplane-smoke-test` group.
2. **Access review:** Run access review campaign scoped to the smoke group. Mark `smoke-user-2` for revocation. Verify remediation CR is auto-created.
3. **Revoke:** Execute the revocation CR. Verify `smoke-user-2` is disabled in Azure AD. Verify the user cannot authenticate (attempt token acquisition).
4. **Emergency lockout:** Fire `emergency_user_lockout` for `smoke-user-1` (cross-system: Azure AD + AWS IAM if a test IAM user exists). Verify both are locked simultaneously. Verify reversal CR restores access.
5. **Time-bound re-access:** Grant `smoke-user-1` temporary access with `access_expiry_hours: 0.02` (~1 min). Verify auto-revoke fires.
6. **Full offboard:** Offboard `smoke-user-3`. Verify account deleted, group membership removed.

**Non-user credential lifecycle:**

7. **API key rotation:** Fire `rotate_iam_key` against `nexplane-smoke-api-key`. Verify old key is inactive. Verify new key is functional (S3 list call succeeds).
8. **SSH key rotation:** Fire `credrotation` targeting the EC2's `authorized_keys`. Verify old key cannot SSH in. Verify new key (returned in CR result) can SSH in.
9. **TLS certificate expiry detection:** Expiry monitor finds the 5-day cert. Verify a finding is created with urgency=`emergency` (≤7 days). Verify auto-generated renewal CR is created.
10. **Database credential rotation:** Fire `credrotation` for `smokeuser` on the PostgreSQL instance. Verify old password fails. Verify new password (from CR result) connects successfully.

**Assertions:**
- Azure AD operations reflect in the tenant within 30 seconds (account state queries)
- Cross-system lockout completes within 60 seconds for all systems
- Expiry monitor detects the 5-day cert
- All credential rotations: old credential denied, new credential works
- All lifecycle events appear in the asset timeline

---

## Feature Parity Gaps in Agent and Connectors

### Agent (Go) — New Commands Needed for Phase 0 (Windows — missing capabilities)

The following Windows hardening controls are not yet implemented in the Go agent. They have no Python executor stubs either — they are entirely absent.

| Command | Package | Description |
|---------|---------|-------------|
| `wdac_audit` | `winharden` | Deploy WDAC policy in audit mode (event 3076); collect would-have-blocked events for duration; return application and DLL list |
| `wdac_enforce` | `winharden` | Deploy WDAC policy in enforce mode (event 3077 = blocked); requires code-signing or allowlist |
| `wdac_driver_block` | `winharden` | Apply Microsoft recommended driver block list; extend with custom vulnerable driver list |
| `asr_audit` | `winharden` | Enable ASR rules in audit mode via PowerShell `Set-MpPreference -AttackSurfaceReductionRules_Ids ... -AttackSurfaceReductionRules_Actions AuditMode`; collect event 1122s |
| `asr_enforce` | `winharden` | Enable ASR rules in block mode (event 1121 = blocked); supports per-rule granularity |
| `sysmon_deploy` | `winharden` | Download Sysmon64.exe; install with Nexplane config template; start service; verify events flowing to Event Log |
| `sysmon_fim` | `winharden` | Query Sysmon Event ID 11 (FileCreate) and 2 (FileCreateTime) for monitored paths; return change list |
| `hvci_enable` | `winharden` | Enable HVCI (Hypervisor-Protected Code Integrity) via registry + UEFI; flags reboot required |
| `defender_configure` | `winharden` | `Set-MpPreference`: real-time protection, cloud protection level, ASR, PUA protection, controlled folder access |

### Agent (Go) — New Commands Needed for Phase 4

| Command | Package | Description |
|---------|---------|-------------|
| `seccomp_learn` | `ossecurity` | `SCMP_ACT_LOG` profile on target service; collect unique syscalls over `duration_seconds`; requires kernel ≥ 4.14 |
| `selinux_permissive_baseline` | `ossecurity` | Switch to permissive, run `ausearch -m avc` for duration, return AVC denials + `audit2allow` output |
| `iptables_log_baseline` | `ossecurity` | Add `--log` rules mirroring intended drop rules; collect logged connections for duration |
| `auditd_baseline` | `ossecurity` | Run without alerting rules for duration; collect all event types and frequencies |
| `fim_init` | `ossecurity` | `aide --init`; stores baseline DB; returns file count (maps to existing fim `init` action) |
| `fim_check` | `ossecurity` | `aide --check`; returns diff from baseline (maps to existing fim `check` action) |
| `falco_output_only` | `ebpf` | Start Falco with rules in `output` mode (log only); collect rule matches for duration |
| `drift_check` | `ossecurity` | Short-duration `seccomp_learn`/`apparmor_complain` compared to stored baseline; returns new behaviors |
| `emergency_user_lockout` (Linux) | `linuxauth` | `usermod -L`; `pkill -KILL -u <user>`; remove from sudo group |

### Agent Executors (Python) — All Stubs to Wire (Phase 0)

**Linux (14 stubs):**

| File | Agent command | Status |
|------|--------------|--------|
| `configure_seccomp.py` | `seccomp` | Stub → add `mode: learn\|enforce` parameter |
| `configure_apparmor.py` | `apparmor` | Stub → mode maps to aa-complain/enforce/disable |
| `configure_selinux.py` | `selinux` | Stub → selinux_setmode exists in ossecurity |
| `apply_sysctl_hardening.py` | `sysctl` | Stub → 13-point CIS baseline in agent |
| `configure_host_firewall.py` | `firewall` | Stub → add_rule/remove_rule/log_baseline actions |
| `blacklist_kernel_modules.py` | `modules` | Stub → modules list param |
| `harden_mount_options.py` | `mount` | Stub → targets /tmp, /dev/shm, /var/tmp |
| `deploy_auditd_rules.py` | `auditd` | Stub → profile: cis_level1, cis_level2, custom |
| `setup_file_integrity_monitoring.py` | `fim` | Stub → action: init, check |
| `audit_os_security_posture.py` | `posture` | Stub → read-only, returns real system state |
| `deploy_ebpf_policy.py` | `ebpf` | Stub → bpftool program load |
| `configure_ebpf_security_policy.py` | `ebpf` | Stub → Falco/Cilium policy apply |
| `audit_ebpf_posture.py` | `ebpf` | Stub → bpftool prog list, unexpected programs |
| `harden_ssh.py` | `ssh` | Stub → 12-point CIS baseline in agent |
| `configure_pam.py` | `pam` | Stub → RHEL/Debian variant detection in agent |
| `audit_scheduled_tasks.py` | `winharden`/`appdiscovery` | Stub → both Linux and Windows; hardcodes empty findings |

**Windows (13 stubs — all map to real Go `winharden` commands):**

| File | Agent command | Status |
|------|--------------|--------|
| `configure_windows_audit_policy.py` | `winharden` | Stub → hardcodes `categories_count: 9` |
| `configure_windows_firewall.py` | `winharden` | Stub → ignores all rule params |
| `harden_rdp.py` | `winharden` | Stub → echoes NLA param without applying |
| `harden_registry.py` | `winharden` | Stub → hardcodes 7 setting names, applies none |
| `harden_smb.py` | `winharden` | Stub → returns mock SMB version |
| `harden_tls_protocols.py` | `winharden` | Stub → returns mock protocol list |
| `deploy_applocker_policy.py` | `winharden` | Stub → returns mock policy; no AppIDSvc start |
| `enable_bitlocker.py` | `winharden` | Stub → returns fake recovery key |
| `enable_credential_guard.py` | `winharden` | Stub → returns mock response |
| `configure_laps.py` | `winharden` | Stub → returns mock response |
| `enforce_powershell_clm.py` | `winharden` | Stub → returns mock response |
| `apply_windows_patches.py` | `winpatch` | Stub → always returns `packages_updated: 0` |
| `audit_windows_patch_status.py` | `winpatch` | Stub → always returns `0 updates available` |

### Connector — New CR Types Needed (Phase 3 + Phase 5)

| CR Type | Connector | Action |
|---------|-----------|--------|
| `emergency_user_lockout` | AD, Azure AD, Okta, AWS IAM | Fan-out disable across all connected identity systems; tolerates partial failure |
| `user_suspension` | AD, Azure AD, Okta, AWS IAM | Temporary disable with scheduled reversal |
| `terminate_user_sessions` | Azure AD, Okta, AWS STS | Revoke all active sessions via IdP API |
| `user_scope_reduction` | AWS IAM, Azure AD | Attach deny/restrict policy; demote to read-only |
| `enforce_mfa` | AWS IAM, Okta, Azure AD | MFA-required condition policy or factor enrollment enforcement |
| `time_bound_access_grant` | AWS IAM, Azure AD | Auto-scheduled revocation CR at expiry |
| `ad_user_create` | Active Directory | Create AD user, set attributes, add to groups |
| `ad_user_disable` | Active Directory | Disable AD account, invalidate Kerberos tickets |
| `ad_user_delete` | Active Directory | Delete AD account, remove from all groups |
| `ad_group_manage` | Active Directory | Add/remove group members |
| `azure_ad_user_create` | Azure AD / Entra ID | Create Entra ID user via Graph API |
| `azure_ad_user_disable` | Azure AD / Entra ID | Disable Entra ID account |
| `azure_ad_conditional_access` | Azure AD / Entra ID | Apply/modify conditional access policy |
| `rotate_ssl_certificate` | Agent (all OS) + cloud connectors | ACME renewal or internal CA issuance; deploy to service; reload |
| `rotate_ssh_keys` | Agent (linuxauth) | Remove old authorized key, add new; verify login with new key |
| `rotate_database_credentials` | Agent (credrotation) | New password in DB + update app config files; verify connectivity |
| `rotate_api_key` | AWS IAM, GCP, Azure | Deactivate old key; create new; update consumers; verify |
| `rotate_jwt_signing_key` | Agent (credrotation) | Generate new signing key pair; update application config; invalidate old tokens |

### Backend Services Needed

| Service | Description |
|---------|-------------|
| Notification service | Event emission → channel routing → delivery (Slack, email, PagerDuty) |
| Escalation job | Background check for emergency CRs exceeding approval timeout |
| Soak timer job | Check `ProjectPhase.soak_completed_at`, unlock production CRs |
| Drift check scheduler | Weekly per-asset job; compare current behavior to stored PolicyBaseline |
| Scanner rescan integration | Post-CR hook to call scanner API for targeted rescan |
| External ticket closure | Post-CR hook to close Jira/PagerDuty/ServiceNow tickets |
| Credential expiry monitor | Daily job; probe TLS certs, check IAM key age, SSH key age, token expiry; create findings |
| Policy baseline store | Stores learn-mode observations per asset per control type; used by drift scheduler |

---

## Smoke Test Coverage Matrix

| Scenario | Smoke Phase | Ph 0 | Ph 1 | Ph 2 | Ph 3 | Ph 4 | Ph 5 |
|----------|-------------|------|------|------|------|------|------|
| Linux OS hardening executor wire-up | **OSSEC_WIRE** | 0.1–0.3, 0.5 | — | — | — | — | — |
| Windows OS hardening executor wire-up | **WIN_OSSEC_WIRE** | 0.3–0.5 | — | — | — | — | — |
| Connector onboarding | Existing A | — | 1.1 | — | — | — | — |
| CVE-driven patching | FINDING_LIFECYCLE | — | 1.2–1.5 | 2.1–2.3, 2.7 | — | — | — |
| Routine patch cycle | BULK_PATCH | — | 1.3, 1.4, 1.7 | 2.1, 2.2 | — | — | — |
| OS upgrade | OS_UPGRADE | — | 1.4, 1.7, 1.8 | 2.9 | — | — | — |
| Emergency zero-day | EMERGENCY_CR | — | 1.3, 1.8 | 2.4 | — | — | — |
| Vuln with no patch | FINDING_MITIGATED | — | 1.5 | 2.5 | — | — | — |
| Containerization | Existing AUTO_AI | — | 1.4, 1.7 | 2.9 | — | 4.3 | — |
| Compliance remediation | COMPLIANCE_FIX | — | 1.6 | 2.8 | — | — | — |
| Access review | ACCESS_REVOKE | — | 1.5 | 2.6 | 3.6 | — | — |
| Incident response (host) | Existing phases | — | 1.1, 1.6 | 2.4 | — | — | — |
| Incident response (user) | USER_ISOLATE | — | 1.6 | — | 3.1–3.3 | — | — |
| Proactive hardening (seccomp) | SECCOMP_PIPELINE | 0.1 | 1.4, 1.7 | 2.9 | — | 4.1–4.5 | — |
| Proactive hardening (AppArmor, iptables, FIM) | **HARDENING_PIPELINE** | 0.1 | 1.4, 1.7 | 2.9 | — | 4.2 | — |
| Microsegmentation | MICROSEG | — | 1.4, 1.7 | 2.9 | — | 4.1 | — |
| Maintenance windows | MAINT_WINDOW | — | 1.3, 1.8 | 2.4 | — | — | — |
| Kernel patching | KERN_PATCH | — | 1.4, 1.7 | — | — | — | — |
| Policy drift (seccomp) | POLICY_DRIFT | — | — | — | — | 4.4 | — |
| Policy drift (AppArmor, iptables, FIM) | HARDENING_PIPELINE | — | — | — | — | 4.4 | — |
| Time-bound access | TIME_BOUND_ACCESS | — | — | — | 3.5 | — | — |
| Human identity lifecycle (Azure AD) | **IDENTITY_LIFECYCLE** | — | — | — | 3.1–3.5 | — | 5.1 |
| Non-user credential rotation | **IDENTITY_LIFECYCLE** | — | — | — | — | — | 5.2, 5.3 |
| Credential expiry detection | **IDENTITY_LIFECYCLE** | — | — | — | — | — | 5.3 |
