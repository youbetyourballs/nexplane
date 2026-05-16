# Nexplane Pricing and Deployment Model

## Tier Structure

### Free (Self-Hosted)

Deployment: Docker Compose on operator-managed infrastructure. Single command install.
No phone-home. No cloud dependency. Agent binaries served from GitHub Releases.

Included:
- Full platform: change requests, approval workflows, rollback, asset inventory
- All open-source connectors (AWS, GCP, Azure, OCI, Linux, Windows, community connectors)
- Unlimited assets
- Bring-your-own AI key (Anthropic or OpenAI -- already supported)

Not included:
- Managed updates (operator runs docker compose pull)
- SLA or support (community only: GitHub Issues, Discord, docs)
- Compliance dashboard
- SSO/SAML
- Enterprise connectors (SCCM, AD/DC recovery, Intune)

Conversion mechanism: operational overhead. Teams hit the ceiling when something breaks
and there is no support line. Deliberate friction, not feature gates.

Agent distribution: GitHub Releases, versioned binaries. CI already builds linux-amd64,
linux-arm64, windows-amd64. Add darwin-amd64 and darwin-arm64 when macOS agent ships.


### Managed (Cloud-Hosted)

Deployment: dedicated instance per customer. Not shared multi-tenant -- each customer
gets an isolated deployment. Slightly higher cost than a shared model but:
- Data never touches another customer's infrastructure (clean compliance answer)
- Blast radius contained per customer
- SOC2/ISO27001/HIPAA posture is straightforward
- Support is simpler (one customer's logs, not shared infrastructure)

Agent model: Tailscale-style. Agent runs in the operator's environment, phones home to
their dedicated Nexplane control plane hosted by us. Only the control plane is hosted --
execution happens in the operator's cloud/on-prem via the agent or their cloud credentials.

Data that leaves operator environment: asset metadata, change history, audit trail.
Credentials and secrets: never. Credentials stay in agent's local encrypted store,
used in-flight only. This must be architecturally enforced and independently auditable.

Pricing: flat monthly rate up to 500 assets. Per-asset above that. No per-seat pricing.
Support: email + ticket, 24-hour response SLA. Premium add-on: 4-hour SLA.

Included above free tier:
- Managed updates (zero-downtime rolling deploys)
- Managed database backups with point-in-time restore
- Compliance dashboard (CIS Controls v8, SOC2 control mapping)
- SSO/SAML
- Multi-user approval workflows with role enforcement
- AI assistant without bring-your-own-key friction (usage billed at cost)
- Email support with SLA

Infrastructure model: Kubernetes + Helm. Each customer gets a dedicated namespace
in our managed cluster (managed tier) or a dedicated cluster in their VPC (enterprise).
Helm chart per customer, deployed via GitOps pipeline. Customer-specific secrets in
Vault or AWS Secrets Manager.


### Enterprise

Deployment: two options.

Option A -- VPC-deployed: Nexplane control plane runs in the customer's AWS/Azure/GCP
account, deployed and managed by us via a dedicated pipeline. Customer controls networking
and data residency. We control software, updates, and SLA.

Option B -- Air-gapped: customer runs everything. We provide versioned images, a Helm
chart, and a support contract. Updates are manual on their approval cadence. Required for
defense/government customers. Adds significant support overhead -- price accordingly.

Pricing: annual contract, negotiated. Mid-market starting point ~$80-150k/year
(500-5k assets). Price anchors on asset count and connectors required.

Included above managed tier:
- Dedicated cluster (not just namespace)
- Custom connector development with SLA
- AD/DC disaster recovery module (when built)
- SCCM/MECM connector with support
- Dedicated support engineer
- SLA with remediation guarantees
- Audit export to customer SIEM (Splunk, Elastic)
- Air-gapped deployment option
- Custom data retention policies
- Penetration test reports and security documentation on request


## Open Questions

### AI assistant per tier
- Free: operator brings their own Anthropic/OpenAI key. Already works today.
- Managed: Nexplane proxies AI calls. We pay inference cost -- factor into flat rate.
  Some customers will want "AI off" mode -- must support this.
- Enterprise: customer brings their own key or pays per-call overage. Some enterprises
  prohibit external AI calls entirely -- "AI off" mode required.

### Free-to-managed conversion triggers
Operational friction (no support, manual upgrades) is the primary driver.
Feature differences to sharpen the pull:
- Compliance dashboard (managed only)
- SSO/SAML (managed only)
- Managed backups (managed only)
- AI assistant without key friction (managed only)
- Connector auto-updates (managed only -- free tier lags one release)

### License enforcement on free tier
Model: feature flags enforced in code. The free binary simply does not include
enterprise features (compliance dashboard, SSO, SCCM connector, AD/DC recovery).
No phone-home license check -- operators will route around it and it creates trust issues.
Open-core: community connectors and core platform are open. Enterprise connectors
and features are closed-source or require a license key to unlock.

### Support tooling for free tier (community)
Required before free tier ships:
- Docs site (separate from nexplane.ai -- docs.nexplane.ai)
- Runbook for common failure modes (database connection issues, agent registration,
  connector credential errors)
- GitHub Discussions enabled
- Discord server
- CHANGELOG with migration notes per release


## Deployment Infrastructure (Managed Tier)

Each customer: dedicated Helm release in a shared Kubernetes cluster.
Components per customer namespace:
- nexplane-backend (FastAPI, 2 replicas)
- nexplane-frontend (nginx serving static build)
- nexplane-postgres (or RDS instance -- RDS preferred for managed backups)
- nexplane-redis (session cache, job queue)

Isolation: Kubernetes NetworkPolicy restricts cross-namespace traffic.
Secrets: per-customer Vault path or AWS Secrets Manager prefix.
Updates: GitOps (ArgoCD or Flux). New release triggers rolling deploy per customer
on a configurable maintenance window.

Agent comms: agent connects outbound to customer's control plane URL over TLS.
No inbound ports required in customer environment. Same model as Tailscale, CrowdStrike,
Datadog agents.


## Packaging Decisions Needed

1. Helm chart for self-hosted (currently only Docker Compose)
   - Required for enterprise VPC-deployed option
   - Also useful for managed-tier internal deployment consistency

2. GitHub Releases for agent binaries
   - Currently S3 only. Free tier needs an S3-free distribution path.
   - GitHub Releases is the standard for open-source binary distribution.

3. Docker Hub vs GitHub Container Registry for images
   - GHCR is free for public images, integrated with GitHub Actions
   - Docker Hub has pull rate limits for unauthenticated users -- risk for free tier

4. Docs site
   - MkDocs or Docusaurus. Hosted on docs.nexplane.ai.
   - Content: getting started, connector reference, change type catalog, API reference,
     runbooks, architecture overview.

5. Community infrastructure
   - GitHub Discussions (free, already available)
   - Discord server (free)
   - Status page (statuspage.io or self-hosted) for managed tier SLA tracking
