# Backlog Review

_Review of strategic planning documents as of 2026-06-05. These documents (`BACKLOG.md`, `ARCHITECTURE_PRINCIPLES.md`, `VISION.md`) do not yet exist under `docs/product/`. This document captures what they should contain and where the gaps are._

---

## Documents That Should Exist But Don't

The following files were not found in `docs/product/` or anywhere in the repo:

- `docs/product/BACKLOG.md`
- `docs/product/ARCHITECTURE_PRINCIPLES.md`
- `docs/product/VISION.md`

Their absence is itself a finding. A project with 200+ change types, 75 connectors, a Go agent, a React frontend, and a live smoke infrastructure has outgrown the "the code is the documentation" model. The sections below describe what each document should contain, drawing on what is currently observable from the code, memory, and architecture map.

---

## BACKLOG.md — What Should Be In It

The closest thing to a backlog that exists today is scattered across:
- `docs/product/CURRENT_STATE.md` ("Top 10 Recommendations")
- `C:\Users\john\.claude\projects\f--Nexplane-nexplane\memory\project_future_tasks.md` (session memory)
- In-code `TODO` comments

### What Is Missing from Any Formal Backlog

**Critical infrastructure work (no ticket, no owner)**
- Temporal migration for the asyncio CR executor — mentioned in `ARCHITECTURE_MAP.md` as "the documented next step" but has no tracked task
- Postgres RLS for multi-tenant data isolation — currently enforced only at the Python service layer
- Fernet key rotation / envelope encryption for connector credentials
- Horizontal scaling blockers (all background workers are single-process asyncio tasks with no distributed lock)

**Smoke test gaps (tracked in session memory, should be in BACKLOG.md)**
- `MAC_AGENT_BOOTSTRAP` and `SANTA_SYNC` phases pending macOS Dedicated Host quota
- OCI smoke phases (partially covered, rollback not fully exercised)
- Azure SQL / Azure Monitor smoke phases
- Windows agent smoke (stubs exist in `test_agent_live.py`, no live coverage)
- GCP SA key permission (`roles/iam.serviceAccountKeyAdmin`) needed before GCP SA key rotation smoke can pass

**Connector gaps (no catalog entry or executor)**
- PagerDuty integration (referenced in runbook templates, no executor)
- Okta MFA push (referenced in IR playbooks, no executor)
- GitHub Actions OIDC connector
- Wiz simulator (AMI cache pattern documented, no implementation)
- Palo Alto firewall connector (AMI cache documented, no implementation)

**Frontend work (no tickets)**
- No end-to-end Playwright tests
- Several large components (ChangeRequestDetail, ProjectDetail, VulnerabilityRemediation) need extraction
- No OpenAPI codegen — type drift is manual
- IR response flow has no dedicated page (done via generic CR detail)

### Recommendations for BACKLOG.md

1. Adopt a format: `## Priority [Critical/High/Medium/Low]` with entries `### [Title]`, each containing Problem, Acceptance Criteria, Dependencies, Effort estimate.
2. Link each item to the relevant executor, route, or model file so the context is navigable.
3. Include a "Not building yet" section (see below) to prevent re-raising deferred ideas.
4. Keep it in the repo (not a ticket system) so it evolves with the code — PRs that close backlog items update this file.

---

## ARCHITECTURE_PRINCIPLES.md — What Should Be In It

No such file exists. The principles are currently implicit in memory (`project_design_philosophy.md`) and scattered across the `ARCHITECTURE_MAP.md` "Key Design Decisions" section. They should be made explicit and permanent.

### Principles Derivable from the Current Code and Memory

**1. The CR abstraction is the trust boundary.**
Every mutation of external infrastructure must go through the CR lifecycle. Anything that bypasses it — direct SDK calls, ad-hoc scripts, untracked CLI commands — does not get audit, approval, rollback, or freeze guarantees. This is why smoke tests must dogfood the platform (memory: `feedback_smoke_test_rollback_pattern`).

**2. The rollback promise is unconditional.**
Every CR type must declare a rollback strategy. For reversible ops, an explicit inverse. For irreversible ops (IAM key revoke, key rotation), reconstitution rollback — save pre-state and re-provision equivalent access. The safety engine enforces this for prod/critical assets. No exception.

**3. Agents are outbound-only.**
The backend never initiates a connection to an agent. Agents long-poll outbound, HMAC-signed. This inverts the firewall problem and makes host compromise a local event, not a pivot into the control plane.

**4. Credentials live in the platform, not in scripts.**
Connector credentials are encrypted at rest in the database, decrypted only at use in `SecretsService`. Engineers never paste credentials into scripts or smoke tests. The platform's own AWS connector (id: `666e237d`) is used for smoke test EC2 operations.

**5. Mocks are silent liabilities.**
Live smoke passing = done. Unit tests that mock infrastructure prove the code compiles, not that the platform works. This is as fundamental as TDD.

**6. AI proposes, Nexplane executes.**
The AI planning system generates CR proposals from a live manifest. The safety engine validates proposals against the manifest. Operators approve. The platform executes. At no point does the AI have direct write access to external systems.

**7. Tailscale is the only network path.**
No public internet exposure, no ngrok, no port forwarding. Managed hosts join the tailnet via the `tailscale_join` CR. The control plane is reachable only via the tailnet.

### Recommendations for ARCHITECTURE_PRINCIPLES.md

1. Formalize the above 7 principles with rationale and the consequence of violating each.
2. Add a "What we don't do" section: no inbound agent connections, no direct SDK calls from smoke tests, no storing plaintext secrets in code.
3. Include a Decision Log appendix — each ADR-style entry records a choice (e.g., "Go for agent binary, not Python") with the date, the tradeoff, and the conditions under which the decision should be revisited.

---

## VISION.md — What Should Be In It

No such file exists. The project vision is in the founder's head and partially in session memory (`project_design_philosophy.md`, `project_design_philosophy_rollback.md`, `project_gtm_content.md`). It needs to be written down.

### What the Vision Should Capture

**The core problem being solved.**
Security operations have a structural incentive misalignment: the people who own the tools to fix security problems (engineering, IT) are not the people who bear the cost of security failures (security team, customers). Nexplane removes the friction of delegation by making every action auditable, reversible, and approval-gated — so the security team can delegate confidently, and the people executing changes have guardrails.

**What Nexplane is not.**
Not a SOAR tool (reactive-only). Not a vulnerability scanner. Not a SIEM. Not a runbook executor that skips the rollback guarantee. The platform spans the full security lifecycle: proactive hardening (CIS benchmarks, key rotation, patch campaigns), microsegmentation, reactive response (IR isolation, forensics, lockdown), and continuous compliance drift detection.

**The rollback guarantee as a differentiator.**
Every action the platform takes can be undone. This is what makes automation safe to delegate. It is the reason operators trust the platform with critical assets. It is harder to build than it looks — reconstitution rollback, project-level reverse traversal, and live smoke verification of rollback paths are all engineering investments in this guarantee.

**The target customer.**
Security engineers at companies with 50–500 managed hosts where the alternative is Jira tickets to DevOps and manual SSH sessions. The platform compresses a 2-week change window into a 30-minute approval-gated execution.

**What success looks like.**
A security engineer wakes up to a CVE. They open Nexplane, see affected assets, get an AI-proposed remediation plan, approve it, and have a verified rollback available — all without writing a line of code or opening a terminal. The audit trail is complete.

### Recommendations for VISION.md

1. Write it in plain language, not engineering language — it should be readable by a customer or a board member.
2. Include a "not this" section to prevent feature creep from each new enterprise customer request.
3. Anchor the timeline: what does a 6-month roadmap look like if this vision is the destination?
4. Review quarterly — product vision should not be a static document.

---

## What Is Missing Across All Three

| Gap | Consequence |
|---|---|
| No formal backlog | Critical infrastructure work (Temporal, RLS, key rotation) has no owner and no priority |
| No principles doc | New contributors make decisions that violate implicit principles (e.g., adding a smoke test that calls boto3 directly) |
| No vision doc | Feature requests are evaluated without a north-star filter; scope creep is invisible |
| Principles in memory only | Memory is per-session and not shared with contributors or reviewers |
| No "not building" list | Deferred features (OCI advanced networking, HSM integration, Wiz live) get re-raised in every planning session |

## What Should Be Reprioritized

Based on current code state, the following items visible in session memory or code comments appear to have been deprioritized but should be escalated:

1. **Temporal migration** — Every other scaling decision depends on this. It is a prerequisite for horizontal scale, reliable background workers, and resumable CR execution.
2. **Multi-tenant DB isolation (RLS)** — Currently a Python-only safety gate. Should be a database-level constraint before the first enterprise customer.
3. **Fernet key rotation** — No path to rotate without decrypting and re-encrypting all credentials. Should be designed before the credential count grows further.

## What Should Be Removed or Deprioritized

1. **Demo seed data in `seed.py`** — inflates every fresh deploy; adds noise to the admin dashboard; should be extracted.
2. **OCI advanced networking phases** — low customer signal, high complexity. Hold until there is a clear enterprise customer.
3. **HSM integration** — high complexity, niche use case. Should be gated on a signed customer with this requirement.
4. **Multi-cloud parallel smoke (`test_multicloud_live.py`, `test_parallel_live.py`)** — these test infrastructure parallelism, not product behavior. They add test infrastructure cost without proving new product guarantees. Consider removing or consolidating.

## What Should Be Expanded

1. **Windows agent smoke coverage** — Windows is listed in the agent platform matrix but all smoke phases use Linux runners. Windows-specific executors (`winpatch`, `winharden`, `changip` netsh) have zero live verification.
2. **Rollback smoke coverage** — Rollback is exercised for ~15 CR types in smoke. The other 180+ deployed CR types have no rollback smoke assertion.
3. **AI planning smoke** — `AI_MANIFEST_PLAN` phase exists but does not assert proposal quality, only proposal format. Should assert that proposals for known scenarios produce semantically correct CR types.
4. **Frontend testing** — Currently zero automated frontend tests. Playwright for the core flows should be added before the first external user.
