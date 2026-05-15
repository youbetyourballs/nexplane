# Nexplane — Commercial Partner Pitch Document

**Version:** 1.0  
**Date:** May 2025  
**Classification:** Internal / Partner Confidential  
**Audience:** BD, Partnerships, and Technical Integration teams

---

## What Is Nexplane

Nexplane is a security operations orchestration platform that connects asset discovery, vulnerability remediation, identity lifecycle management, configuration hardening, and change management into a single workflow engine. It operates across AWS, GCP, Azure, OCI, and on-prem Linux/Windows environments.

Nexplane is not a SOAR. It is not an agent-only tool. It is not another dashboard aggregator. It is a change management platform with security intent baked in — meaning it can take a finding from your scanner, reason about the appropriate fix, model the change, get it approved, execute it, and verify the result, all with full audit trail and rollback capability.

The distinction matters for partners: Nexplane makes your data actionable at the remediation layer, not just the visibility layer.

---

## What Differentiates Nexplane from Other Orchestration Platforms

| Capability | SOAR | Agent-only tools | Nexplane |
|---|---|---|---|
| Finding ingestion | Yes | No | Yes |
| Automated remediation | Partial (scripts) | Yes | Yes |
| Change management workflow | No | No | Yes |
| Identity lifecycle | No | No | Yes |
| Rollback and audit | No | No | Yes |
| Cloud + on-prem unified | No | Partial | Yes |
| Approval gates and ITSM integration | Partial | No | Yes |
| Posture scoring and drift detection | No | No | Yes |

**The core loop Nexplane closes that others do not:**

```
FIND (scanner/CNAPP) → TRIAGE (risk scoring + context) → PLAN (change modeling)
→ APPROVE (ITSM/human gate) → EXECUTE (connector with rollback) → VERIFY (re-scan/diff)
→ CLOSE (ticket, audit record, compliance evidence)
```

Most platforms stop at FIND or PLAN. Nexplane closes the loop to CLOSE.

---

## Partner Categories and Integration Specifications

---

### 1. Security Posture / CNAPP

**Partners: Wiz, Lacework, Orca Security, Aqua Security**

#### What Nexplane Needs from Them

- **API access** to findings, issues, and asset inventory endpoints
- **Webhook or event stream** for real-time finding ingestion (new critical, severity change, status change)
- **Test tenant or sandbox environment** with seeded findings across multiple cloud providers
- **Data model documentation**: finding schema, severity taxonomy, asset identifiers (cloud resource IDs, ARNs, etc.), rule/policy IDs
- **Re-scan or re-evaluate API** (or equivalent) so Nexplane can trigger verification after remediation
- **GraphQL or REST SDK** (whichever is primary) with long-lived service account credentials

#### What Nexplane Offers Them

- Findings from their platform become actionable remediations with tracked outcomes — improves their customers' mean time to remediate (MTTR) metrics
- Co-sell opportunity: customers who use both platforms get full-loop coverage; Nexplane can position their scanner as the preferred CNAPP in joint accounts
- Integration listed in their marketplace/partner directory
- Joint case studies: "X found it, Nexplane fixed it"

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Finding ingest | Pull / Push | Nexplane polls or receives webhooks for new/updated findings |
| Asset context enrichment | Pull | Nexplane queries asset details to populate change context |
| Re-scan trigger | Push | Nexplane calls re-evaluate after remediation executor completes |
| Posture score tracking | Pull | Nexplane tracks posture score over time per account/project |

#### Technical Prerequisites

- API key or OAuth2 client credentials (service account)
- Webhook endpoint registration (Nexplane exposes HTTPS receiver)
- Minimum API permissions: read findings, read assets, trigger re-scan
- SDK versions: Wiz GraphQL API v1+, Lacework REST v2, Orca REST v1, Aqua REST v2
- IP allowlist if tenant is network-restricted

---

### 2. Endpoint / XDR

**Partners: CrowdStrike Falcon, SentinelOne, Carbon Black, Microsoft Defender for Endpoint**

#### What Nexplane Needs from Them

- **API access** to device inventory, detections, vulnerabilities (where available), and RTR/remote execution capability
- **Test environment** with enrolled endpoints (Windows and Linux) and seeded detections
- **Device identity mapping**: hostname, MAC, OS version, sensor version, tags/groups
- **Real-time threat signal stream** (webhook, SIEM forward, or streaming API)
- **Remote action API**: script execution, file quarantine, process termination — for remediation executor use
- **Documentation on rate limits** and recommended polling patterns

#### What Nexplane Offers Them

- XDR detections trigger automated containment and remediation workflows with approval gates — reduces analyst alert fatigue
- Nexplane tracks which detections were remediated vs. suppressed, giving their platform better feedback loop data
- Co-sell: joint value proposition around "detect and remediate in one workflow"
- Nexplane can integrate their sensor deployment into identity and hardening workflows (install sensor as part of provisioning)

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Device inventory sync | Pull | Nexplane imports device list to correlate with asset inventory |
| Detection/alert ingest | Push/Pull | Detections trigger Nexplane remediation workflows |
| Vulnerability data ingest | Pull | Where XDR includes vuln data (CS Spotlight, etc.) |
| Remote action execution | Push | Nexplane calls RTR/live response to execute fix scripts |
| Sensor deployment | Push | Nexplane installs/updates sensor as part of provisioning workflows |

#### Technical Prerequisites

- OAuth2 client credentials (CrowdStrike), API token (SentinelOne, Carbon Black), Azure AD app registration (MDE)
- RTR/Live Response API access — may require elevated tier
- Webhook configuration for real-time detections
- SDK: CrowdStrike `falconpy`, SentinelOne REST v2.1, Carbon Black REST v6, MDE REST v1.0
- MDE requires M365 Defender tenant with appropriate RBAC roles

---

### 3. Vulnerability Management

**Partners: Tenable.io/Nessus, Qualys VMDR, Rapid7 InsightVM**

#### What Nexplane Needs from Them

- **API access** to vulnerability findings, asset lists, and scan history
- **Scan launch API** to trigger targeted rescans post-remediation
- **Finding schema documentation**: CVE mapping, CVSS scores, affected asset identifiers, plugin/QID/check IDs
- **Export API** for bulk finding pull (paginated, filterable by severity and date)
- **Sandbox/test tenant** with pre-populated scan data across Linux and Windows assets
- **Agent-based vs. credentialed scan distinction** in finding metadata

#### What Nexplane Offers Them

- Nexplane converts their scan output into executed remediations with evidence — significantly improving customer patch compliance rates
- Closed-loop remediation data can feed back into their dashboards (Nexplane can update finding status via API where supported)
- Co-sell: scanner customers with Nexplane integration have higher patch rate metrics — marketable outcome
- Joint integration in their ecosystem/marketplace

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Finding ingest | Pull | Nexplane pulls findings on schedule or on-demand |
| Asset inventory sync | Pull | Correlate scanner assets with Nexplane asset registry |
| Rescan trigger | Push | Nexplane triggers targeted rescan after patch execution |
| Finding status update | Push | Where API allows, Nexplane marks findings as remediated |
| Scan credential management | Bi-dir | Nexplane can provision/rotate scan credentials via PAM integration |

#### Technical Prerequisites

- API key or username/password (Tenable, Qualys), OAuth2 (InsightVM)
- Export/download API access (some tiers restrict this)
- SDK: Tenable `pytenable`, Qualys REST/XML API, Rapid7 `rapid7vmconsole` or REST
- Rescan API may require admin-level credentials — document minimum required permissions

---

### 4. SIEM / Log Management

**Partners: Splunk, Elastic Security**

#### What Nexplane Needs from Them

- **Ingest endpoint** for Nexplane to ship structured event logs (workflow start/complete, change executed, rollback triggered, approval decisions)
- **Search/query API** so Nexplane can pull correlated events back for context enrichment during triage
- **Alert/detection webhook or polling API** to receive SIEM-generated alerts as Nexplane workflow triggers
- **Data model documentation** (Splunk CIM, Elastic ECS) so Nexplane events map cleanly to their schemas
- **Dev/sandbox instance** for integration testing (Splunk Cloud trial or self-hosted; Elastic Cloud trial)
- **Index/data stream provisioning guidance** for Nexplane event types

#### What Nexplane Offers Them

- Nexplane generates high-signal, structured operational events (not raw logs) — valuable data for their customers' SOC dashboards
- SIEM alerts can trigger Nexplane remediation workflows, making their detection data actionable without manual analyst handoff
- Pre-built Nexplane dashboards/apps for their platform (Splunk App, Elastic integration package)
- Co-sell: joint story around "SIEM detects anomaly, Nexplane remediates the underlying misconfiguration"

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Event log shipping | Push | Nexplane sends structured workflow events via HEC (Splunk) or Logstash/Beats (Elastic) |
| Alert ingestion | Pull/Push | SIEM alerts trigger Nexplane workflows |
| Context query | Pull | Nexplane queries SIEM for historical context before executing changes |
| Audit trail export | Push | Nexplane ships change audit records to SIEM for compliance retention |

#### Technical Prerequisites

- Splunk: HEC token, index name, CIM-compliant field mapping; optional: Splunk REST API token for search
- Elastic: API key, data stream name, ILM policy; Elasticsearch REST endpoint
- Network access from Nexplane worker nodes to SIEM ingest endpoints
- Pre-built index templates / mappings provided by Nexplane

---

### 5. PAM / Secrets

**Partners: CyberArk, BeyondTrust, HashiCorp Vault Enterprise**

#### What Nexplane Needs from Them

- **Secret retrieval API**: Nexplane executors must fetch credentials at runtime (never store them)
- **Dynamic secret generation** (Vault especially): short-lived credentials for scan accounts, SSH, database access
- **Session recording API** (CyberArk PSM, BeyondTrust): Nexplane-initiated privileged sessions should be recorded
- **Credential rotation integration**: Nexplane triggers rotation post-use or on schedule
- **Test vault instance** with representative secret types (SSH keys, API tokens, database passwords, cloud IAM keys)
- **SDK and auth method documentation**: AppRole (Vault), OAuth2/API key (CyberArk), API key (BeyondTrust)

#### What Nexplane Offers Them

- Nexplane is a high-value consumer of privileged credentials — every remediation workflow is a PAM use case (privileged access for a defined purpose, with full audit trail)
- Nexplane can enforce just-in-time access patterns: request credential, use it, confirm use, expire it
- Co-sell: PAM customers with Nexplane get evidence that privileged access was used for a specific approved change — not just "accessed"
- Integration demonstrates PAM platform value in automated workflows, not just human sessions

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Credential fetch at runtime | Pull | Executors retrieve secrets just before use |
| Dynamic credential generation | Pull | Vault issues short-lived creds for specific executor runs |
| Session recording | Push | Nexplane registers session with PAM before connecting to target |
| Credential rotation trigger | Push | Nexplane requests rotation after remediation completes |
| Access request workflow | Bi-dir | Nexplane creates access request, PAM approves, Nexplane consumes |

#### Technical Prerequisites

- Vault: AppRole auth, Vault Enterprise license for namespaces; or token auth for dev
- CyberArk: CCP/AIM for credential retrieval, REST API v2+, application identity registration
- BeyondTrust: API key, Password Safe REST API
- Nexplane never persists secrets in its database — all fetched at executor runtime and zeroed after use

---

### 6. ITSM / Ticketing

**Partners: ServiceNow, PagerDuty**

#### What Nexplane Needs from Them

- **Ticket creation API**: Nexplane creates change requests, incidents, and tasks with structured metadata
- **Approval workflow API**: Nexplane polls or receives webhooks for approval decisions on pending changes
- **Ticket status update API**: Nexplane updates tickets as changes progress (approved, executing, complete, rolled back)
- **CMDB read access** (ServiceNow): Nexplane queries CMDB to enrich change records with CI relationships
- **Webhook / event subscription**: receive ticket state transitions in real time
- **Dev instance**: ServiceNow PDI (Personal Developer Instance) or PagerDuty trial account

#### What Nexplane Offers Them

- Nexplane generates high-quality, structured change records — not freeform text tickets. Their CMDB and change history becomes more valuable.
- Every Nexplane workflow creates an auditable ticket with before/after state, execution logs, and approval chain — improves their customers' change management maturity
- Co-sell: customers with mature ITSM practices can now automate security remediation without bypassing change control
- Nexplane can be positioned as the "security remediation" category within their workflow ecosystem

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Change request creation | Push | Nexplane creates CHG record before executing changes |
| Approval gate polling | Pull/Push | Nexplane waits for approval before proceeding |
| Ticket status updates | Push | Nexplane updates ticket as workflow progresses |
| CMDB enrichment | Pull | Nexplane queries affected CIs and relationships |
| Incident linking | Push | Nexplane links remediation ticket to originating incident |
| On-call escalation | Push | Nexplane pages on-call via PagerDuty for high-risk changes |

#### Technical Prerequisites

- ServiceNow: OAuth2 client credentials or basic auth, ITOM/ITSM license, Change Management module
- PagerDuty: API key, Events API v2 for alerts, REST API v2 for service/escalation queries
- ServiceNow table API access: `change_request`, `cmdb_ci`, `incident`, `task`
- Webhook endpoint on Nexplane side for receiving approval decisions

---

### 7. Supply Chain / Code Security

**Partners: Snyk, JFrog Xray**

#### What Nexplane Needs from Them

- **Finding API**: vulnerabilities in container images, open source dependencies, IaC misconfigs
- **Image/artifact inventory**: what is deployed where, with which vulnerabilities
- **Webhook / event stream**: new critical CVE in a deployed image triggers Nexplane workflow
- **Fix recommendation data**: Snyk's fix PRs, JFrog's patch recommendations — Nexplane uses these to construct remediation steps
- **Registry integration context**: which registry, which tag, which deployed workload
- **Test org** with representative projects (Node, Python, Java, Docker images, Terraform)

#### What Nexplane Offers Them

- Supply chain findings become operational remediations: Nexplane can trigger image rebuilds, workload restarts, dependency updates in CI/CD pipelines
- Nexplane closes the "we found a vuln in prod image" loop by orchestrating the path from finding to redeployed clean image
- Co-sell: their customers get SLA-backed remediation workflows, not just a list of findings
- Integration into Nexplane's asset registry means image vulnerabilities are correlated with runtime workloads

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| Finding ingest | Pull/Push | Nexplane ingests container/dependency vulns |
| Fix recommendation consumption | Pull | Nexplane reads suggested fixes to build remediation plan |
| Workload correlation | Pull | Map vulnerable image to running workload (K8s, ECS, etc.) |
| Remediation trigger | Push | Nexplane triggers CI/CD pipeline or image rebuild |
| Re-scan after remediation | Push | Nexplane requests re-evaluation of fixed artifact |

#### Technical Prerequisites

- Snyk: API token, REST API v1/v3 (beta), org ID
- JFrog: API key or access token, Artifactory + Xray instance, REST API v2
- Nexplane needs read access to project/org, findings, and artifact metadata
- Webhook registration endpoint for real-time finding events

---

### 8. Identity / SSO

**Partners: Okta (extended), Ping Identity**

#### What Nexplane Needs from Them

- **SCIM API**: bidirectional user/group sync for identity lifecycle workflows (provision, deprovision, group membership changes)
- **Risk signals API** (Okta Identity Engine, Ping Risk): Nexplane consumes identity risk scores to make remediation decisions (e.g., force MFA reset, suspend account, trigger access review)
- **Event hooks / log streaming**: real-time identity events (login anomaly, MFA bypass, account lockout, password change) trigger Nexplane workflows
- **Application and entitlement model**: Nexplane needs to understand what access a user has in order to model least-privilege remediations
- **Admin API**: Nexplane can perform user actions (suspend, force MFA, reset password, remove group) as part of automated identity remediation
- **Test tenant** with representative user/group structure and seeded risk events

#### What Nexplane Offers Them

- Nexplane turns identity risk signals into structured remediation actions with approval gates — their risk data drives real operational outcomes
- Identity lifecycle (joiner/mover/leaver) orchestration becomes part of a broader security workflow, not a siloed process
- Nexplane can enforce identity hygiene policies automatically: dormant accounts, excessive permissions, MFA exemptions
- Co-sell: customers using both platforms get automated response to identity risk, reducing time from detection to containment

#### Key Integration Points

| Integration Point | Direction | Description |
|---|---|---|
| SCIM user/group sync | Bi-dir | Nexplane syncs identity state for workflow context |
| Risk signal ingest | Push | Identity risk scores trigger Nexplane workflow evaluation |
| Event hook / syslog stream | Push | Login anomalies, lockouts trigger Nexplane workflows |
| Entitlement read | Pull | Nexplane queries user's current access for remediation modeling |
| User action execution | Push | Nexplane suspends, modifies, or resets users as remediation step |
| MFA policy enforcement | Push | Nexplane enforces MFA policy changes via Admin API |

#### Technical Prerequisites

- Okta: API token or OAuth2 (SSWS or OIDC), Okta Identity Engine tenant, Admin API access, SCIM 2.0 endpoint
- Ping: PingOne API token or OAuth2, PingFederate admin credentials (on-prem), PingDirectory REST API
- Risk signals API may require Okta Identity Engine Advanced tier or Ping Risk license
- Nexplane needs `USER_ADMIN` and `GROUP_MEMBERSHIP_ADMIN` equivalent roles

---

## Partner Pitch Email Template

The following template should be customized per company. Replace all bracketed fields before sending.

---

**Subject:** Nexplane Integration Partnership — [COMPANY NAME] + Nexplane: Closing the Remediation Loop

---

Hi [FIRST NAME],

I'm reaching out because [COMPANY NAME]'s platform is one of the most important sources of security signal in the environments our mutual customers operate in — and right now, too much of that signal doesn't result in a closed finding.

We're building Nexplane, a security operations platform that orchestrates the full remediation lifecycle across cloud and on-prem infrastructure. We're not another SOAR or dashboard layer. We're a change management engine with security intent: we take findings, model the fix, get it approved, execute it with full rollback capability, and close the loop back to the originating system.

**What we're looking for from [COMPANY NAME]:**

- API access (or a test tenant / sandbox) to begin building the integration
- A technical contact who can advise on the right ingestion patterns and data models
- Optionally: co-development interest if there are integration patterns you'd prefer to own on your side

**What you get:**

- Your findings become measurable remediations — customers can see their MTTR dropping as a direct result of the [COMPANY NAME] + Nexplane integration
- Co-sell opportunity: we're positioning best-in-class tools as the preferred stack for customers who want full-loop security operations
- A partner integration that surfaces in the Nexplane connector library, with attribution and joint case study potential

**How it would work:**

Nexplane calls [COMPANY NAME]'s API to ingest [findings / detections / vulnerabilities / identity risk signals]. We enrich that data with asset context, model a remediation plan, route it through an approval workflow, execute the change via our connector layer, and then call back to [COMPANY NAME] to verify the finding is resolved.

The integration is bidirectional: your platform feeds us signal, and we feed you closed-loop remediation status.

We're currently building the [COMPANY NAME] connector and would like to start with [sandbox access / a dev API key / the API documentation package]. We expect the initial integration to be a 4–6 week build once we have access, and we're happy to share the integration code for your team to review or contribute to.

Would a 30-minute technical call make sense this week or next? I can send a more detailed integration spec ahead of time if useful.

Thanks,  
[YOUR NAME]  
[TITLE], Nexplane  
[EMAIL] | [PHONE]

---

*Nexplane is an early-stage security operations platform. We're selectively engaging design partners in each category to build the initial integration set. This is a ground-floor partnership opportunity.*

---

## Partner Engagement Tracker

| Partner | Category | Contact Status | Sandbox Access | API Docs | Integration Stage |
|---|---|---|---|---|---|
| Wiz | CNAPP | Not started | No | Public | Not started |
| Lacework | CNAPP | Not started | No | Public | Not started |
| Orca Security | CNAPP | Not started | No | Public | Not started |
| Aqua Security | CNAPP | Not started | No | Public | Not started |
| CrowdStrike Falcon | XDR | Not started | No | Partner portal | Not started |
| SentinelOne | XDR | Not started | No | Public | Not started |
| Carbon Black | XDR | Not started | No | Public | Not started |
| Microsoft Defender for Endpoint | XDR | Not started | No | Public | Not started |
| Tenable.io | Vuln Mgmt | Not started | No | Public | Not started |
| Qualys VMDR | Vuln Mgmt | Not started | No | Public | Not started |
| Rapid7 InsightVM | Vuln Mgmt | Not started | No | Public | Not started |
| Splunk | SIEM | Not started | No | Public | Not started |
| Elastic Security | SIEM | Not started | No | Public | Not started |
| CyberArk | PAM | Not started | No | Partner portal | Not started |
| BeyondTrust | PAM | Not started | No | Public | Not started |
| HashiCorp Vault Enterprise | PAM | Not started | No | Public | Not started |
| ServiceNow | ITSM | Not started | No | PDI available | Not started |
| PagerDuty | ITSM | Not started | No | Public | Not started |
| Snyk | Supply Chain | Not started | No | Public | Not started |
| JFrog Xray | Supply Chain | Not started | No | Public | Not started |
| Okta | Identity | Not started | No | Public | Not started |
| Ping Identity | Identity | Not started | No | Public | Not started |

---

## Prioritization Guidance

For the initial design partner cohort, recommended prioritization:

**Tier 1 — Start here (highest workflow impact, best API access):**
- Wiz (CNAPP — best API, largest market share in cloud posture)
- Tenable.io (Vuln Mgmt — most complete API, widely deployed)
- ServiceNow (ITSM — approval workflow is a blocker for enterprise deployments)
- HashiCorp Vault Enterprise (PAM — most open, dynamic secrets are core to executor security model)
- Okta (Identity — SCIM + risk signals unlock identity lifecycle workflows)

**Tier 2 — Second cohort (high value, slightly more complex partnership):**
- CrowdStrike Falcon (XDR — Spotlight vuln data + RTR for remediation is a strong combined story)
- Splunk (SIEM — large installed base, HEC is straightforward)
- Snyk (Supply Chain — strong developer adoption, clean API)
- CyberArk (PAM — enterprise requirement in many accounts, more complex partnership)

**Tier 3 — Third cohort (complete the category coverage):**
- All remaining partners

---

*Document maintained by Nexplane BD/Partnerships. Update partner contact status and integration stage as outreach progresses.*
