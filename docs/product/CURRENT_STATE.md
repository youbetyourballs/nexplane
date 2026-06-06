# Current State

_Snapshot date: 2026-06-05. Derived from repo at `F:\Nexplane\nexplane` (branch `master`)._

## Executive Summary

**What Nexplane currently is.** Nexplane is a security execution control plane built as a FastAPI backend (`backend/app`), a React/Vite frontend (`frontend/src`), a cross-platform Go agent (`agent/`), and a Postgres-backed change-request lifecycle. It is operated as a multi-tenant single-org demo today (single seeded organization, JWT auth, RBAC roles defined in `backend/app/models/user.py`). The product wraps connector executors (cloud APIs, identity, EDR, IaC, MDM) and an in-host Go agent behind a uniform Change Request (CR) abstraction with planning, approval, execution, verification, and rollback stages.

**What major capabilities already exist.**

- **Change Request engine** with 200+ change types defined in `backend/app/models/change_request.py` (the `ChangeType` enum) and `backend/app/connectors/change_type_definitions/` (396 per-CR JSON definitions).
- **Connector catalog and executor framework** — 75 connector catalog JSONs in `backend/app/connectors/catalog/`, with matching executor packages in `backend/app/connectors/executors/` (~80 directories including `aws`, `azure`, `gcp`, `oci`, `active_directory`, `entra_id`, `okta`, `freeipa`, `keycloak`, `crowdstrike`, `sentinelone`, `defender_endpoint`, `tenable`, `qualys`, `nessus`, `openvas`, `wiz`, `runzero`, `zscaler`, `paloalto`, `opnsense`, `cloudflare`, `tailscale`, `terraform[_local]`, `ansible[_local]`, `helm`, `pulumi`, `bicep`, `cloudformation`, `checkov`, `chef_inspec`, `saltstack`, `kubernetes`, `github`, `gitlab`, `gitea`, `jfrog`, `snyk`, `jamf`, `micromdm`, `intune`, `sccm`, `wufb`, `laps`, `winrm`, `ssh`, `bind_dns`, `step_ca`, `vault`, `hashicorp_vault`, `infisical`, `postgres`, `mongodb`, `redis`, `splunk`, `elastic`, `datadog`, `falco`, `wazuh`, `santa_sync_server`, `nexplane_agent`, etc.).
- **Nexplane Go agent** (`agent/`) with commands for Linux, Windows, and macOS (build tags split across files; macOS arm64 build added). Command packages: `changip`, `linuxpatch`, `winpatch`, `isolation`, `forensics`, `compliance`, `credrotation`, `iac`, `fleet`, `backup`, `reboot`, `dbadmin`, `ossecurity`, `linuxauth`, `winharden`, `crossplatform`, `linuxupgrade`, `containerizebuild`, `containerizeretire`, `appdiscovery`, `configsyslog`, `deepdiscover`, `ebpf`, `estimatesize`, `listpkgs`, `macos`, `uploadimage`, `virtualize`.
- **AI Planning** (`backend/app/services/ai_service.py`, `planning_engine.py`) — multi-provider (Anthropic, OpenAI), dynamic CR manifest injected into the system prompt (`manifest_builder.py`), used from the Projects/AI chat panel and from the planner that pre-populates draft CRs.
- **CR Manifest** (`/cr-manifest` endpoint, MCP `get_cr_manifest` tool) — programmatically generated planning vocabulary derived from CR definitions, filterable by domain / action_class / touches / rollback_type.
- **MCP server** (`backend/app/mcp_server.py`, `mcp_tools/`) mounted at `/mcp`, with agent-token-based authentication.
- **Composable runbooks** (`backend/app/services/runbook_executor.py`, `runbook_service.py`, `runbook_cr_bridge.py`) with step types `change`, `condition`, `human_checkpoint`, `parallel_group`; tick-driven by APScheduler every 30s.
- **Projects + project-level rollback** (`projects.py` router, `project_rollback_service.py`) — orchestrated reverse-traversal rollback with startup-resume of interrupted rollbacks.
- **Vulnerability remediation pipeline** with auto-CR generation, configurable SLA tiers, escalation worker, CVE blast-radius UI.
- **Identity lifecycle** — onboard/offboard CRs across AD, Okta, Entra ID, Google Workspace, GitHub, Slack with rollback; access review campaigns; identity graph.
- **Incident response playbooks** — host isolation, account lockdown, evidence preservation, phishing response.
- **Maintenance windows + change freeze enforcement** (returns 423 Locked; bypass requires `ir_responder` role + justification).
- **Live smoke test suite** (`backend/tests/smoke/`) covering AWS, Azure, GCP, OCI, agent, runbooks, secret stores, platform, plus the `run_on_ec2.py` runner that provisions a t3.small EC2 to host the test run. AMI cache pattern for slow infra (LDAP, Vault, etc.).
- **eBPF policy auto-generation** (network + LSM) — new in late May/early June 2026; soak observation → audit-mode synthesis → enforce promotion with rollback.
- **Linux security policy auto-generation** for SELinux, AppArmor, and seccomp via the `security_policy/` service tree.
- **CR manifest-driven AI proposals** — verified by the `AI_MANIFEST_PLAN` smoke phase.
- **Containerization wizard / autonomous containerization** — multi-step UI under `ContainerizationWizard.tsx` plus `containerizebuild` agent commands.

**What appears production-ready.**

- Core CR lifecycle, approval gating, RBAC, and audit log.
- AWS, GCP, Azure VM / storage / IAM / DNS executors (broad live smoke coverage).
- Linux agent (patch, hardening, forensics, fleet, backup, change-IP, compliance).
- The Anthropic Claude provider path in `ai_service.py`.
- Tailscale-based agent reachability for managed hosts.

**What appears incomplete.**

- **macOS agent** — actively in flight. Recent commits add Darwin os_type, arm64 build, eBPF darwin shims, defaults_write/santa, but several MDM (Jamf/MicroMDM) executors are catalog-only without full executor coverage, and `MAC_AGENT_BOOTSTRAP` / `SANTA_SYNC` smoke phases still require mac2.metal capacity (see `project_mac_smoke_pending`).
- **Windows agent IP phases** (`IP_WIN_A`, `IP_WIN_D`) — flagged as slow / opt-in; reliability not yet at parity with Linux.
- **MDM connectors** (`jamf`, `micromdm`) — catalogs landed (`c64b863`, `d690208`); executors and end-to-end MDM enrollment are in plan stage (`2026-06-03-mdm-sp1-infrastructure-core.md`).
- **OCI connector** — five sub-projects of design + plan in `docs/superpowers/`; executor tree exists (`backend/app/connectors/executors/oci/`) but live smoke maturity behind AWS/GCP/Azure.
- **Visual DAG / project graph** — `ProjectGraph` component present; full visual DAG plan from 2026-05-01 may not be complete end-to-end.
- **Defender for Endpoint, SCCM, WUfB, Intune, LAPS, WinRM** — catalogs and executor stubs present; smoke coverage is partial.
- **Temporal workflow runtime** — `workflows/runner.py` and `execute_change_workflow.py` are asyncio-based MVPs explicitly labelled "Temporal-ready"; not on Temporal yet.
- **Multi-tenant / multi-org** — code uses `organization_id` consistently but the seed creates one org; no org-management UI.

## Active Workstreams

### Backend

- **Current Status:** Active. Master branch sees multi-commits-per-day in May/June 2026.
- **Completed Work:** Core CR engine, planning engine, safety engine, secrets service (Fernet AES-256 with versioning), audit service, identity sync, runbook executor tick loop, project rollback service + resume-on-startup, CR manifest builder, AI service refactor to consume manifest, MCP server, agent HMAC + agent tokens, scheduled CR worker, drift check worker, credential expiry worker, soak timer worker, escalation worker, eBPF policy synthesizers + executors, SELinux/AppArmor/seccomp learning plugins.
- **Remaining Work:** Real Temporal integration, multi-tenant management surface, finishing MDM SP1, completing OCI smoke parity, hardening eBPF LSM darwin path.
- **Known Issues:** Orphaned CR scrub at startup (`_scrub_orphaned_crs` in `main.py`) is reactive — root cause is asyncio workflow runner not durable across restarts. SECRET_KEY default in docker-compose is a dev key (`dev-secret-key-change-in-production-32chars`).
- **Known Risks:** Single Postgres instance, no read replica or backup automation defined in compose; `ENVIRONMENT=development` is the only mode wired in.

### Frontend

- **Current Status:** Active.
- **Completed Work:** 23+ pages under `frontend/src/pages` covering Dashboard, Projects, ChangeRequests, Connectors, Assets/AssetDetail, Approvals, Compliance, IR (via dedicated components), VulnerabilityRemediation, Runbooks/RunbookEditor/RunbookExecution, ScheduledOperations, BackupRecovery, AccessReviews, MaintenanceWindows, SmokeTests, Notifications, Settings, Login. Components include `ContainerizationWizard`, `IPMigrationWizard`, `IRPlaybookLauncher`, `ProjectGraph`, `SecurityPolicySoakPanel`, `RemediationPolicyEditor`, `SLAWidget`, `FreezeAlert`, `AIPanel`, `AddConnectorModal`.
- **Remaining Work:** UI plans 1–5 (`2026-05-27-ui-plan-*`) — navigation/dashboard/connector-status/asset-vuln-view/projects-chat overhaul; degree of completion not fully verifiable from file listing alone.
- **Known Issues:** Vite HMR unreliable inside Docker on Windows — documented workaround is `docker compose stop frontend && docker compose up frontend -d`.
- **Known Risks:** No production frontend image is the default; `Dockerfile.prod` exists (`nginx-spa.conf`) but compose uses dev image.

### Agent

- **Current Status:** Active. Recent feature work focused on macOS parity and eBPF darwin shims.
- **Completed Work:** Linux agent fully featured. Windows agent supports patch, isolation, fleet, backup, hardening (LAPS, CredGuard, AppLocker, SMB, BitLocker, RDP, audit policy). macOS commands: defaults_write/delete, profiles_install, santa_install, lock_local_user, patch audit/apply, eBPF network (via pf) + LSM (via BSM audit_control) shims, promote_ebpf_policy via pf.
- **Remaining Work:** macOS MDM-enrollment commands, Windows IP migration robustness, Windows agent eBPF placeholder (no equivalent on Win).
- **Known Issues:** Linux build tags split — historical `linux||darwin` extraction work (`12c14ba`) shows ongoing refactor. macOS launchctl bootstrap requires retries for early-boot I/O errors.
- **Known Risks:** Binaries are publicly downloadable from S3 (`nexplane-agent-downloads`, us-east-1). Compromise of the bucket would let an attacker serve a malicious binary; SHA256 sidecar mitigates only if consumers verify (the agent does verify before self-update).

### macOS

- **Current Status:** Active, blocked on infra.
- **Completed Work:** Darwin OsType enum value (`73776f7`), arm64 build (`7f4233d`), defaults_write rollback + defaults_delete executor (`9d6e10f`), santa_install as proper CR with rollback (`b603f17`), macOS migration consolidation (`93b4c2e`, `i840c7d9e0f1` merge migration).
- **Remaining Work:** `MAC_AGENT_BOOTSTRAP` and `SANTA_SYNC` smoke phases pending mac2.metal Dedicated Host quota grant (see `project_mac_smoke_pending` in user memory). MDM enrollment orchestration design (`2026-06-03-mdm-enrollment-orchestration-design.md`) not yet implemented.
- **Known Issues:** mac2.metal scrub cycle is ~5h — necessitates 18000s poll timeout in `run_on_ec2.py`. SSH key for mac2.metal persisted in SSM to survive runner restarts.
- **Known Risks:** macOS smoke depends on a single Dedicated Host; failure modes are slow to detect.

### Connectors

- **Current Status:** Catalogs broad; executor depth varies sharply.
- **Completed Work:** 75 catalog JSONs; AWS / Azure / GCP / Tailscale / SSH / Active Directory / Entra ID / Okta / Google Workspace / GitHub / Slack / Helm / Kubernetes / Terraform[_local] / Ansible[_local] / CrowdStrike / Tenable / Vault / Hashicorp Vault have meaningful executor coverage; recent additions: `freeipa`, `keycloak`, `teleport`, `step_ca`, `opnsense`, `bind_dns`, `gitlab`, `gitea`, `jfrog`, `snyk`, `wazuh`, `falco`, `splunk`, `elastic`, `datadog`, `pagerduty`, `servicenow`, `cloudflare`, `paloalto`, `runzero`, `wiz`, `zscaler`, `santa_sync_server`, `nexplane_agent`. New catalog-only as of June 2026: `jamf`, `micromdm`.
- **Remaining Work:** Executor parity for newly added catalogs (smoke gaps tracked in `project_smoke_test_status` user memory).
- **Known Issues:** `POST /connectors` silently drops the `credentials` field — must follow with `PUT /connectors/{id}/credentials` (memory: `feedback_connector_credentials_put_endpoint`). Catalog field names must match executor exactly (e.g., HashiCorp Vault uses `token`, not `vault_token`).
- **Known Risks:** Catalog-vs-executor drift is easy to introduce; no automated lint enforcing alignment.

### Change Management

- **Current Status:** Stable core, expanding rollback surface.
- **Completed Work:** Lifecycle Draft → Planned → Awaiting Approval → Approved → Executing → Verifying → Completed/Failed/Rolled Back; per-CR rollback via implicit reverse op or explicit rollback step; project-level rollback (`projects/{id}/rollback`); maintenance window queuing; freeze enforcement; scheduled CRs (`/recurring-jobs`); batch CR creation and bulk approve; CR manifest endpoint.
- **Remaining Work:** Replace asyncio workflow runner with Temporal; richer approval policies beyond critical-requires-2.
- **Known Issues:** In-flight CRs at backend restart are scrubbed to `failed` (`_scrub_orphaned_crs`) — non-resumable. Plan steps stored in `change_plan`; step credentials never persisted (per `StepOutputStore` contract).
- **Known Risks:** Approval bypass for `ir_responder` during freeze is logged but auditable only via audit events; no quorum enforcement.

### AI Planning

- **Current Status:** Stable and recently improved.
- **Completed Work:** Multi-provider (`_resolve_provider_config` selects Anthropic or OpenAI by org setting); CR manifest is injected dynamically into the system prompt (commit `856f27c` replaced hardcoded `_CHANGE_TYPES_TEXT`); asset context (`_build_asset_context_text`) gives the model live inventory; `AI_MANIFEST_PLAN` smoke phase verifies proposals use real manifest CR types.
- **Remaining Work:** Tool-calling beyond MCP, plan-quality feedback loop, broader provider support (only Anthropic + OpenAI today).
- **Known Issues:** AI not configured returns 402; UI surfaces inline guidance.
- **Known Risks:** Provider keys stored encrypted (Fernet); model name default `claude-sonnet-4-6` is set via env (`AI_MODEL`).

## Open Branches

Active feature branches diverged from `master`. None merged yet (master is clean). Names map to the May 3 spec series:

| Branch | Apparent scope |
|---|---|
| `feature/backup-recovery` | Backup/restore + DR failover work (spec `2026-05-03-backup-recovery-scheduled-ops`, plan `2026-05-28-backup-recovery-plan.md`). |
| `feature/compliance-governance` | CIS dashboard, drift, freeze (`2026-05-03-compliance-governance`, `2026-05-08-cis-v8-compliance-dashboard`). |
| `feature/connector-expansion` | New cloud discovery / security tools / workflow connector tranche (`2026-05-01-connectors-6*`). |
| `feature/database-admin` | DB user/permission/audit CRs across PG/MySQL/MSSQL (`2026-05-03-database-admin`). |
| `feature/fleet-operations` | Rolling restart, canary push, fleet health, maintenance windows (`2026-05-03-fleet-operations`). |
| `feature/iac-orchestration` | Terraform/Ansible/Helm CR types (`2026-05-03-iac-orchestration`). |
| `feature/identity-lifecycle` | Onboard/offboard + access reviews (`2026-05-03-identity-lifecycle`). |
| `feature/incident-response` | IR playbooks, forensic bundles (`2026-05-03-incident-response`). |
| `feature/patch-management` | Patch CRs, campaigns (`2026-05-03-patch-management`). |
| `feature/saas-k8s-actions` | Google Workspace / GitHub / Slack / Entra / K8s actions (`2026-05-03-saas-k8s-change-actions`). |
| `feature/secret-rotation` | DB/SSH/API/SA rotation (`2026-05-03-secret-rotation`). |
| `feature/vuln-remediation` | Webhook ingest + auto-CR + SLA + blast radius (`2026-05-03-vuln-remediation`). |

Most of this work appears to have already landed on `master` (the corresponding services and routers exist). Branches likely contain in-progress or alternate work and should be audited for whether they have anything `master` does not.

## Recent Major Changes

Based on the last ~100 commits and the May/June 2026 spec series:

1. **MDM connector groundwork (June 2026).** `c710ee0` MDM SP1 plan, Jamf + MicroMDM catalogs + enum values, macOS CR-type migrations, macOS agent parity work (defaults, santa, profiles, lock_local_user, patch).
2. **macOS agent shipping (late May–early June).** Darwin os_type, arm64 build support, eBPF darwin shims via pf and BSM audit_control, launchctl bootstrap retries.
3. **eBPF policy autogen (late May).** Network + LSM synthesizer plugins, audit-mode soak → enforce promotion executors, EBPF_POLICY smoke phase, safety-engine rollback set update.
4. **Project rollback (late May).** New `project_rollbacks` table + model + service + REST endpoints + UI drawer + startup-resume + on_cr_failed hook + PROJECT_ROLLBACK smoke phase.
5. **CR manifest (late May).** `ManifestBuilder` infers domain/action_class/touches/preconditions/effects from CR definitions; `GET /cr-manifest` and MCP `get_cr_manifest` tool; AI service consumes manifest dynamically; CR_MANIFEST + AI_MANIFEST_PLAN smoke phases.
6. **SELinux/AppArmor/seccomp auto-gen (late May).** Three sub-plans; per-type permissive learning, module install/rollback/delta, SELINUX_AUTOGEN smoke phase.
7. **Recurring job scheduler + external secret store + MCP agent tokens** (late May).
8. **Credential lifecycle phase 2 + revocation live smoke + executor audit** (mid–late May).
9. **EC2 platform migration** — backend now runs on a dedicated EC2 (i-050bab85006f0b73c, Tailscale 100.101.186.39) per the 2026-05-21 design.

## Known Blockers

### Technical

- **macOS smoke gated on mac2.metal Dedicated Host quota.** `MAC_AGENT_BOOTSTRAP` and `SANTA_SYNC` cannot run until AWS grants the host.
- **GCP service account key creation blocked.** `nexplane-dev` SA lacks `roles/iam.serviceAccountKeyAdmin` (user memory `project_gcp_sa_key_permission_blocked`).
- **DC AMI security group rule.** LDAP runs on DC AMI but `sg-08891be3823c0e4ce` blocks TCP 389 from the platform subnet.
- **Asyncio workflow runner is not durable.** Backend restarts force CRs in `executing`/`verifying` to `failed` via `_scrub_orphaned_crs`.
- **Vite HMR on Docker for Windows is unreliable.** Documented restart workaround.

### Architecture

- **No real multi-tenancy surface.** `organization_id` plumbing exists but there is one seeded org and no admin UI to create more.
- **No Temporal yet.** Workflow runner is "Temporal-ready" but uses asyncio; long-running CRs are at risk on restart.
- **Connector executor parity vs. catalog is unverified.** No CI guard that every catalog action has a corresponding executor function.

### Operational

- **Secrets in `docker-compose.yml`.** Dev SECRET_KEY is checked in and labelled "change in production"; no prod overlay enforces replacement.
- **EC2 platform is single-instance.** No HA, no automated failover for the control plane.
- **S3 agent bucket is public.** Acceptable for the binary download model but increases blast radius if compromised.
- **Mac2.metal scrub is ~5h.** Slows any iteration that requires a fresh Mac host.

## What Should Be Built Next

Top 10 recommendations, in priority order, with rationale:

1. **Durable workflow runtime (Temporal or equivalent).** Today an unplanned backend restart silently fails every in-flight CR. This is the single biggest reliability gap and undermines the rollback guarantee for long-running multi-step CRs.
2. **Catalog–executor parity CI guard.** Lint that every action in `backend/app/connectors/catalog/*.json` has a registered executor function and a matching `change_type_definitions/*.json` entry. Drift is currently caught only by smoke tests.
3. **Finish MDM SP1 executors (Jamf, MicroMDM, macOS enrollment orchestration).** Catalogs and CR types have landed but the executors and end-to-end profile-install + santa-sync rollback are not done; this is the active workstream.
4. **Production deployment story.** Ship a documented prod compose / Helm chart that enforces non-default SECRET_KEY, switches frontend to `Dockerfile.prod` (nginx-spa), removes the host-bound 0.0.0.0:8000 default (currently in compose), and pins agent S3 URLs by environment.
5. **Connector smoke parity for the late-May additions** (`opnsense`, `step_ca`, `bind_dns`, `freeipa`, `keycloak`, `teleport`, `gitlab`, `gitea`, `jfrog`, `snyk`, `wazuh`, `falco`, `splunk`, `elastic`, `datadog`). Per the project's smoke-test-driven-development principle, these are not "done" until each has a passing live phase including rollback.
6. **OCI smoke parity with AWS/Azure/GCP.** The executor tree is in place; sub-project 6 (smoke) needs completion to bring the fourth cloud to first-class status.
7. **Multi-tenant admin surface.** Org CRUD, user invite, per-org connector scoping (the design exists in `2026-05-03-multi-account-connector-scoping-design.md`). Required before any external pilot.
8. **CR manifest typed schemas + planner validation.** The manifest exists but planner output is still string-typed CR types validated post-hoc. Generate Pydantic models per CR type from the manifest and validate AI proposals against them before insert.
9. **Windows agent IP migration GA.** `IP_WIN_A`/`IP_WIN_D` are slow opt-in phases; move to default and add Server 2019/2022 matrix.
10. **Observability for the control plane itself.** No metrics endpoint, no structured logging schema, no traces. The platform proposes telemetry CRs but is itself a black box at runtime — a non-trivial gap before any production deployment.
