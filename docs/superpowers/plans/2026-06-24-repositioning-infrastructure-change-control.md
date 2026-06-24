# Repositioning — Infrastructure Change Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reposition Nexplane as "the control plane for infrastructure change" — update README, UI navigation, stub pages for upcoming features, and MCP tool descriptions.

**Architecture:** Pure copy + UI changes. No new backend models or migrations. Sidebar restructured into four sections (top, Operations, Compliance & Identity, Preview). Three stub pages added for Impact Simulation, Recommendations, and Infrastructure Memory. Two new lightweight MCP tools added (`search_assets`, `explain_change_request`).

**Tech Stack:** React + TypeScript (Vite), FastAPI (Python 3.12), lucide-react icons, @tanstack/react-query

---

## File Map

| File | Change |
|------|--------|
| `README.md` | Full rewrite |
| `frontend/src/components/Sidebar.tsx` | Restructure sections, renames, stub items |
| `frontend/src/pages/ImpactSimulationPage.tsx` | New stub page |
| `frontend/src/pages/RecommendationsPage.tsx` | New stub page |
| `frontend/src/pages/InfrastructureMemoryPage.tsx` | New stub page |
| `frontend/src/routes/index.tsx` | Register 3 new routes |
| `backend/app/mcp_tools/assets.py` | Update docstrings + add `search_assets` |
| `backend/app/mcp_tools/change_requests.py` | Update docstrings + add `explain_change_request` |

---

### Task 1: Rewrite README.md

**Files:**
- Modify: `README.md` (root)

- [ ] **Step 1: Replace README.md with the repositioned version**

Write the following as the complete content of `README.md`:

```markdown
# Nexplane — The control plane for infrastructure change

Eliminate uncertainty before every infrastructure change.

---

## The four questions Nexplane answers

| Question | How Nexplane helps |
|----------|--------------------|
| **What do I have?** | Asset inventory across 70+ connectors — cloud, identity, network, endpoints, secrets |
| **What will happen if I change it?** | Impact simulation shows affected assets, dependencies, and blast radius before execution |
| **Who needs to approve it?** | Approval gates with role-based routing, audit trail, and human checkpoints |
| **Can I safely undo it?** | Every change ships with a rollback plan. Rollback is a first-class operation, not an afterthought |

---

## What is Nexplane?

Nexplane is an infrastructure change control platform for teams that need auditability, approval gates, and rollback on every change. It sits between your infrastructure connectors and the people (or AI agents) who want to modify them — enforcing a consistent lifecycle of discovery, planning, approval, execution, and rollback.

Security is the wedge use case: removing stale firewall rules, rotating secrets, remediating findings, and hardening endpoints. But the platform is built for any infrastructure change — DNS updates, VM operations, IAM modifications, certificate rotations, network policy changes, and more. If it touches infrastructure and needs an audit trail, Nexplane is the right home for it.

---

## Who it's for

- **Platform engineering** — standardize how infrastructure changes are proposed, reviewed, and executed across teams
- **Infrastructure engineering** — eliminate change anxiety with blast radius analysis and guaranteed rollback
- **Cloud engineering** — manage cloud config changes with approval gates, audit trail, and connector-native execution
- **SRE / production engineering** — tie every change to a rollback plan before execution; observe outcomes in real time
- **Network engineering** — execute and document network changes with pre/post verification steps
- **Security architecture** — harden infrastructure and track remediation through the full change lifecycle
- **Technology operations** — coordinate and approve cross-team infrastructure changes with a single control plane
- **Operational risk** — full audit trail, approval evidence, and rollback records for every change, queryable by LLMs

---

## The change lifecycle

```
Discover → Understand → Plan → Approve → Execute → Observe → Rollback → Document
```

| Stage | What happens |
|-------|-------------|
| **Discover** | Connectors ingest assets, findings, and configuration state |
| **Understand** | Asset graph, open findings, and blast radius inform the change |
| **Plan** | AI-assisted planning generates steps, prechecks, and rollback path |
| **Approve** | Role-based approval gates with human review and audit evidence |
| **Execute** | Connector-native execution against real infrastructure |
| **Observe** | Post-execution verification checks confirm the intended outcome |
| **Rollback** | One-click rollback using the stored pre-change snapshot |
| **Document** | Full audit trail: who requested, who approved, what changed, what rolled back |

---

## Key capabilities

- **Asset inventory** — 70+ connectors across cloud, identity, network, endpoints, secrets, and observability
- **Asset graph** — dependency relationships, blast radius traversal, owner lookup *(coming soon)*
- **Impact simulation** — "what breaks if I change this?" before you execute *(coming soon)*
- **AI-assisted planning** — Claude generates execution steps, prechecks, and rollback plans
- **Approval gates** — role-based routing with human review; LLMs can propose but humans approve
- **Rollback center** — every change ships with a rollback plan; execute rollback in one step
- **Infrastructure memory** — "why does this exist?" provenance tracking for every asset and change *(coming soon)*
- **Recommendation engine** — "what should I fix next?" prioritized suggestions *(coming soon)*
- **MCP / LLM access** — full change lifecycle exposed via MCP for Claude and other LLM agents
- **Full audit trail** — every change request, approval, execution, and rollback is permanently recorded

---

## MCP / LLM access

Nexplane exposes its full change lifecycle via the Model Context Protocol (MCP). Connect Claude or any MCP-compatible LLM to discover assets, plan changes, route for approval, execute, and roll back — all through the same platform that human operators use.

**Example prompts:**
- *"List all production assets and identify which have open critical findings"*
- *"Create a change request to rotate the SSH key on host web-prod-01"*
- *"What changed on the payroll server in the last 30 days and who approved each change?"*
- *"What is the rollback plan for CR abc-123?"*
- *"Roll back the DNS change from yesterday"*

MCP endpoint: `https://<your-nexplane-host>/mcp` (SSE transport, Bearer token auth)

---

## Quick start

```bash
# Clone and start
git clone https://github.com/youbetyourballs/nexplane
cd nexplane
cp .env.example .env          # edit as needed
docker compose up -d

# Access
# UI:      http://localhost:3000
# API:     http://localhost:8000
# Docs:    http://localhost:8000/docs
# MCP:     http://localhost:8000/mcp
```

Default credentials (demo only): `admin@acme.example` / `admin123`

---

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, @tanstack/react-query |
| Backend | FastAPI, Python 3.12, SQLAlchemy (async), Alembic |
| Database | PostgreSQL 16 |
| Agent | Go (cross-platform: Linux, macOS, Windows) |
| MCP server | FastMCP via SSE transport |
| Secrets | Pluggable: Fernet (default), AWS Secrets Manager, HashiCorp Vault |
| Auth | JWT + OIDC support |
| Deployment | Docker Compose; Helm charts for Kubernetes |

The agent uses outbound long-poll — no inbound ports required on managed hosts.

---

## Connectors (70+)

Cloud: AWS, Azure, GCP, OCI, Cloudflare · Identity: Active Directory, Entra ID, Okta, Google Workspace, Keycloak, LDAP · Security: CrowdStrike, Wiz, Tenable, Snyk, SentinelOne · IaC: Terraform, Ansible, Pulumi, Helm · Endpoints: Jamf, Intune, SCCM · Observability: Datadog, Splunk, Elastic · Ticketing: Jira, ServiceNow, PagerDuty · Source control: GitHub, GitLab · And more.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README for infrastructure change control positioning"
```

---

### Task 2: Restructure Sidebar

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Replace Sidebar.tsx with restructured version**

The new sidebar has four sections: an unlabeled top group, a collapsible Operations group, a collapsible Compliance & Identity group, and a collapsible Preview group. Smoke Tests is removed from the nav. "Asset Inventory" becomes "Assets". "Vulnerability" (route `/remediation`) becomes "Findings & Remediation" and moves to Compliance & Identity.

Write the following as the complete content of `frontend/src/components/Sidebar.tsx`:

```tsx
import { useState } from "react";
import { NavLink } from "react-router-dom";
import clsx from "clsx";
import {
  LayoutDashboard,
  FileStack,
  Plug,
  Server,
  FolderOpen,
  Settings as SettingsIcon,
  ShieldCheck,
  BookOpen,
  ClipboardList,
  HardDrive,
  Clock,
  CalendarClock,
  Bell,
  ChevronDown,
  ChevronRight,
  Wrench,
  ShieldAlert,
  Zap,
  Lightbulb,
  Brain,
  Lock,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";

// ── nav item definitions ─────────────────────────────────────────────────────

const topNavItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/assets", label: "Assets", icon: Server },
  { to: "/connectors", label: "Connectors", icon: Plug },
];

const operationsNavItems = [
  { to: "/runbooks", label: "Runbooks", icon: BookOpen },
  { to: "/scheduled-operations", label: "Scheduled Ops", icon: CalendarClock },
  { to: "/maintenance-windows", label: "Maintenance Windows", icon: Clock },
  { to: "/backup-recovery", label: "Backup & Recovery", icon: HardDrive },
];

const complianceNavItems = [
  { to: "/remediation", label: "Findings & Remediation", icon: ShieldAlert },
  { to: "/access-reviews", label: "Access Reviews", icon: ShieldCheck },
  { to: "/compliance", label: "Compliance", icon: ClipboardList },
];

const previewNavItems = [
  { to: "/impact-simulation", label: "Impact Simulation", icon: Zap },
  { to: "/recommendations", label: "Recommendations", icon: Lightbulb },
  { to: "/infrastructure-memory", label: "Infrastructure Memory", icon: Brain },
];

const bottomNavItems = [
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

// ── components ───────────────────────────────────────────────────────────────

function NavItem({
  to,
  label,
  icon: Icon,
  exact,
  badge,
  preview,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
  exact?: boolean;
  badge?: number;
  preview?: boolean;
}) {
  return (
    <NavLink
      to={to}
      end={exact}
      className={({ isActive }) =>
        clsx(
          "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
          isActive
            ? "bg-brand-600 text-white"
            : "text-slate-400 hover:text-white hover:bg-navy-light"
        )
      }
    >
      <div className="relative flex-shrink-0">
        <Icon className="w-4 h-4" />
        {badge != null && badge > 0 && (
          <span className="absolute -top-1.5 -right-1.5 bg-amber-500 text-white text-[10px] font-bold rounded-full min-w-[14px] h-[14px] flex items-center justify-center px-0.5 leading-none">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </div>
      <span className="flex-1">{label}</span>
      {preview && (
        <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-1 py-0.5 leading-none">
          Preview
        </span>
      )}
    </NavLink>
  );
}

function SectionHeader({
  icon: Icon,
  label,
  open,
  onToggle,
}: {
  icon: React.ElementType;
  label: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      aria-expanded={open}
      onClick={onToggle}
      className="flex items-center gap-2 w-full px-3 py-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wide hover:text-slate-300 transition-colors"
    >
      <Icon className="w-3 h-3" />
      <span>{label}</span>
      {open ? (
        <ChevronDown className="w-3 h-3 ml-auto" />
      ) : (
        <ChevronRight className="w-3 h-3 ml-auto" />
      )}
    </button>
  );
}

// ── Sidebar ──────────────────────────────────────────────────────────────────

export function Sidebar() {
  const { user, logout } = useAuth();
  const [operationsOpen, setOperationsOpen] = useState(true);
  const [complianceOpen, setComplianceOpen] = useState(true);
  const [previewOpen, setPreviewOpen] = useState(true);

  const { data: unreadNotifications = [] } = useQuery<{ id: string; read: boolean }[]>({
    queryKey: ["notifications", "unread"],
    queryFn: () => apiClient.get("/notifications?unread_only=true&limit=50").then(r => r.data),
    refetchInterval: 30_000,
    enabled: !!user,
  });

  const { data: pendingApprovals = [] } = useQuery<{ id: string }[]>({
    queryKey: ["change-requests", "pending-approvals"],
    queryFn: () =>
      apiClient.get("/change-requests?status=awaiting_approval&limit=100").then(r =>
        Array.isArray(r.data) ? r.data : (r.data?.items ?? [])
      ),
    refetchInterval: 60_000,
    enabled: !!user,
  });

  const unreadCount = unreadNotifications.length;
  const pendingCount = pendingApprovals.length;

  const { data: checklist } = useQuery<{
    steps: { id: string; label: string; complete: boolean; detail: string }[];
    connector_count: number;
    asset_count: number;
  }>({
    queryKey: ["onboarding-checklist"],
    queryFn: () => apiClient.get("/onboarding/checklist").then((r) => r.data),
    staleTime: 60_000,
    enabled: !!user,
  });

  const showOnboarding = checklist != null && checklist.connector_count === 0;
  const incompleteSteps = checklist?.steps?.filter((s) => !s.complete) ?? [];

  return (
    <aside className="fixed inset-y-0 left-0 w-60 bg-navy flex flex-col z-10">
      {/* Logo */}
      <div className="flex items-center px-5 py-4 border-b border-navy-border">
        <img src="/title_white.png" alt="Nexplane" className="h-8 w-auto" />
      </div>

      {/* Onboarding checklist */}
      {showOnboarding && incompleteSteps.length > 0 && (
        <div className="px-3 py-3 border-b border-navy-border bg-indigo-900/30">
          <p className="text-xs font-semibold text-indigo-300 mb-2 uppercase tracking-wide">Get started</p>
          <div className="space-y-1.5">
            {incompleteSteps.slice(0, 3).map((step) => (
              <div key={step.id} className="flex items-start gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-1.5 shrink-0" />
                <span className="text-xs text-indigo-200 leading-tight">{step.label}</span>
              </div>
            ))}
          </div>
          <NavLink
            to="/connectors"
            className="mt-2 block text-xs font-medium text-indigo-300 hover:text-white transition-colors"
          >
            Configure connectors →
          </NavLink>
        </div>
      )}

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {/* Top group — core workflow */}
        {topNavItems.map(({ to, label, icon, exact }) => (
          <NavItem
            key={to}
            to={to}
            label={label}
            icon={icon}
            exact={exact}
            badge={to === "/change-requests" && pendingCount > 0 ? pendingCount : undefined}
          />
        ))}

        {/* Operations */}
        <div className="pt-2">
          <SectionHeader
            icon={Wrench}
            label="Operations"
            open={operationsOpen}
            onToggle={() => setOperationsOpen((v) => !v)}
          />
          {operationsOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {operationsNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Compliance & Identity */}
        <div className="pt-2">
          <SectionHeader
            icon={Lock}
            label="Compliance & Identity"
            open={complianceOpen}
            onToggle={() => setComplianceOpen((v) => !v)}
          />
          {complianceOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {complianceNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} />
              ))}
            </div>
          )}
        </div>

        {/* Preview — upcoming capabilities */}
        <div className="pt-2">
          <SectionHeader
            icon={Zap}
            label="Preview"
            open={previewOpen}
            onToggle={() => setPreviewOpen((v) => !v)}
          />
          {previewOpen && (
            <div className="mt-0.5 space-y-0.5 pl-2">
              {previewNavItems.map(({ to, label, icon }) => (
                <NavItem key={to} to={to} label={label} icon={icon} preview />
              ))}
            </div>
          )}
        </div>

        {/* Bottom strip */}
        <div className="pt-2 border-t border-navy-border mt-2 space-y-0.5">
          {bottomNavItems.map(({ to, label, icon }) => (
            <NavItem
              key={to}
              to={to}
              label={label}
              icon={icon}
              badge={to === "/notifications" && unreadCount > 0 ? unreadCount : undefined}
            />
          ))}
        </div>
      </nav>

      {/* User footer */}
      <div className="px-4 py-4 border-t border-navy-border">
        {user && (
          <div className="mb-3">
            <div className="text-slate-300 text-sm font-medium truncate">{user.name}</div>
            <div className="text-slate-500 text-xs truncate">{user.email}</div>
            <div className="text-slate-600 text-xs mt-0.5 capitalize">{user.role.replace("_", " ")}</div>
          </div>
        )}
        <button
          onClick={logout}
          className="text-slate-500 hover:text-slate-300 text-xs transition-colors"
        >
          Sign out
        </button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat: restructure sidebar — lifecycle sections, Preview group, renames"
```

---

### Task 3: Create stub pages

**Files:**
- Create: `frontend/src/pages/ImpactSimulationPage.tsx`
- Create: `frontend/src/pages/RecommendationsPage.tsx`
- Create: `frontend/src/pages/InfrastructureMemoryPage.tsx`

Each stub page answers one of the four core questions, explains what the feature will do, and links to an existing alternative.

- [ ] **Step 1: Create ImpactSimulationPage.tsx**

```tsx
// frontend/src/pages/ImpactSimulationPage.tsx
import { Zap, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function ImpactSimulationPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Zap className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Impact Simulation</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">What will happen if I change it?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        Impact Simulation will let you model infrastructure changes before executing them. Select an
        asset, choose the change you're considering, and the platform will traverse the asset graph
        to show you: which systems depend on this asset, what will break, who will be affected, the
        blast radius, and the recommended rollback strategy — all before a single byte changes in
        production.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">What you'll be able to simulate:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Remove or restrict a firewall rule",
            "Rotate a certificate or secret",
            "Change a DNS record",
            "Decommission a VM or instance",
            "Restrict an IAM permission",
            "Remove public exposure",
            "Restart or migrate a service",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Asset details to explore an asset's connections and open findings
        before creating a change request.
      </p>
      <Link
        to="/assets"
        className="inline-flex items-center gap-2 text-sm font-medium text-brand-400 hover:text-brand-300 transition-colors"
      >
        Browse assets <ArrowRight className="w-4 h-4" />
      </Link>
    </div>
  );
}
```

- [ ] **Step 2: Create RecommendationsPage.tsx**

```tsx
// frontend/src/pages/RecommendationsPage.tsx
import { Lightbulb, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function RecommendationsPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Lightbulb className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Recommendations</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">What should I fix next?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        The Recommendation Engine will continuously analyze your asset inventory, open findings,
        change history, and connector data to surface prioritized, actionable improvement
        suggestions — think Dependabot for infrastructure. Each recommendation will include the
        affected assets, severity, confidence score, and a one-click path to creating the
        remediation change request.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">Example recommendations:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Remove stale firewall rules with no recent traffic",
            "Rotate secrets older than 90 days",
            "Remove unused IAM permissions",
            "Archive inactive user accounts",
            "Add owners to assets missing ownership",
            "Improve rollback coverage for high-risk change types",
            "Convert high-frequency manual runbooks into executable workflows",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Findings & Remediation to work through open security findings on your assets.
      </p>
      <Link
        to="/remediation"
        className="inline-flex items-center gap-2 text-sm font-medium text-brand-400 hover:text-brand-300 transition-colors"
      >
        View findings <ArrowRight className="w-4 h-4" />
      </Link>
    </div>
  );
}
```

- [ ] **Step 3: Create InfrastructureMemoryPage.tsx**

```tsx
// frontend/src/pages/InfrastructureMemoryPage.tsx
import { Brain, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function InfrastructureMemoryPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Brain className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Infrastructure Memory</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">Why does this exist?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        Infrastructure Memory will preserve the institutional knowledge behind every asset and
        configuration in your environment. Instead of asking a colleague why TCP 8443 is open or
        who approved that firewall rule three years ago, you'll be able to query the platform
        directly — and get a sourced, auditable answer linked to the original change request,
        approval, and business justification.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">Questions you'll be able to answer:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Why is TCP 8443 open on this host?",
            "Who approved this firewall rule?",
            "Why does this DNS record exist?",
            "What system owns this certificate?",
            "What change introduced this IAM permission?",
            "Is this asset still in use?",
            "Can this be safely removed?",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Asset details to view the change timeline for any asset.
      </p>
      <Link
        to="/assets"
        className="inline-flex items-center gap-2 text-sm font-medium text-brand-400 hover:text-brand-300 transition-colors"
      >
        Browse assets <ArrowRight className="w-4 h-4" />
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ImpactSimulationPage.tsx frontend/src/pages/RecommendationsPage.tsx frontend/src/pages/InfrastructureMemoryPage.tsx
git commit -m "feat: add stub pages for Impact Simulation, Recommendations, Infrastructure Memory"
```

---

### Task 4: Register stub routes

**Files:**
- Modify: `frontend/src/routes/index.tsx`

- [ ] **Step 1: Update routes/index.tsx**

Add imports for the three new pages and register their routes inside the Layout `<Route>`:

```tsx
import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "../components/Layout";
import { Dashboard } from "../pages/Dashboard";
import { ChangeRequestList } from "../pages/ChangeRequestList";
import { ChangeRequestDetail } from "../pages/ChangeRequestDetail";
import { CreateChangeRequest } from "../pages/CreateChangeRequest";
import { ApprovalsQueue } from "../pages/ApprovalsQueue";
import { Assets } from "../pages/Assets";
import { AssetDetail } from "../pages/AssetDetail";
import { Connectors } from "../pages/Connectors";
import { SmokeTests } from "../pages/SmokeTests";
import { Projects } from "../pages/Projects";
import { ProjectDetail } from "../pages/ProjectDetail";
import { Settings } from "../pages/Settings";
import VulnerabilityRemediation from "../pages/VulnerabilityRemediation";
import { Runbooks } from "../pages/Runbooks";
import { RunbookEditor } from "../pages/RunbookEditor";
import { RunbookExecution } from "../pages/RunbookExecution";
import { AccessReviews } from "../pages/AccessReviews";
import { AccessReviewDetail } from "../pages/AccessReviewDetail";
import { Compliance } from "../pages/Compliance";
import BackupRecovery from "../pages/BackupRecovery";
import { MaintenanceWindows } from "../pages/MaintenanceWindows";
import ScheduledOperations from "../pages/ScheduledOperations";
import { Notifications } from "../pages/Notifications";
import { ImpactSimulationPage } from "../pages/ImpactSimulationPage";
import { RecommendationsPage } from "../pages/RecommendationsPage";
import { InfrastructureMemoryPage } from "../pages/InfrastructureMemoryPage";
import { useAuth } from "../hooks/useAuth";

export function AppRoutes() {
  const { user, isLoading } = useAuth();

  if (isLoading) return null;
  if (!user) return <Navigate to="/login" replace />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/change-requests" element={<ChangeRequestList />} />
        <Route path="/change-requests/new" element={<CreateChangeRequest />} />
        <Route path="/change-requests/:id" element={<ChangeRequestDetail />} />
        <Route path="/approvals" element={<ApprovalsQueue />} />
        <Route path="/projects" element={<Projects />} />
        <Route path="/projects/new" element={<ProjectDetail />} />
        <Route path="/projects/:id" element={<ProjectDetail />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/:id" element={<AssetDetail />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/remediation" element={<VulnerabilityRemediation />} />
        <Route path="/runbooks" element={<Runbooks />} />
        <Route path="/runbooks/new" element={<RunbookEditor />} />
        <Route path="/runbooks/:id" element={<RunbookEditor />} />
        <Route path="/executions/:id" element={<RunbookExecution />} />
        <Route path="/access-reviews" element={<AccessReviews />} />
        <Route path="/access-reviews/:id" element={<AccessReviewDetail />} />
        <Route path="/compliance" element={<Compliance />} />
        <Route path="/backup-recovery" element={<BackupRecovery />} />
        <Route path="/maintenance-windows" element={<MaintenanceWindows />} />
        <Route path="/scheduled-operations" element={<ScheduledOperations />} />
        <Route path="/smoke-tests" element={<SmokeTests />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/impact-simulation" element={<ImpactSimulationPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/infrastructure-memory" element={<InfrastructureMemoryPage />} />
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/routes/index.tsx
git commit -m "feat: register routes for stub preview pages"
```

---

### Task 5: Update MCP asset tool descriptions + add search_assets

**Files:**
- Modify: `backend/app/mcp_tools/assets.py`

- [ ] **Step 1: Update docstrings for all 5 existing tools**

In `backend/app/mcp_tools/assets.py`, replace the docstrings (the triple-quoted string immediately inside each `async def`) as follows. Change only the docstrings — do not alter any other code.

`list_assets` — replace docstring with:
```python
    """
    Enumerate infrastructure assets across connected systems. Use to answer: what do we have?
    Filter by asset_type (server/cloud_account/dns_zone/etc.), environment (dev/staging/prod),
    criticality (low/medium/high/critical), or connector_id.
    Returns summary fields — use get_asset for full detail.
    """
```

`get_asset` — replace docstring with:
```python
    """
    Get full context for an asset including owner, connector source, and recent changes.
    Use before creating a CR to understand what you're touching.
    """
```

`get_asset_context` — replace docstring with:
```python
    """
    Get a full planning context bundle for an asset: recent CRs, open findings, timeline, and
    connector. Use before creating a CR to ensure the plan is appropriate for this asset.
    """
```

`list_asset_findings` — replace docstring with:
```python
    """
    List open security findings for an asset. Use to identify what needs remediation before or
    after a change. Filter by status (open/actionable/remediating/etc.) or severity (critical/high/medium/low).
    """
```

`get_asset_timeline` — replace docstring with:
```python
    """
    Get ordered change and event history for an asset. Use to answer: what changed recently
    and who approved it? Returns the last N timeline events including CR executions, finding
    ingests, and scan events.
    """
```

- [ ] **Step 2: Add search_assets tool at the end of assets.py**

Append the following function after the closing of `get_asset_timeline`:

```python
@mcp.tool()
async def search_assets(
    token: str,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Search assets by name substring or tag value. Use to find assets when you don't know
    the exact ID. Returns the same fields as list_assets.
    """
    from sqlalchemy import select, or_
    from sqlalchemy import cast, String
    from app.models.asset import Asset

    user, db, db_cm = await _auth(token)
    try:
        stmt = (
            select(Asset)
            .where(Asset.organization_id == user.organization_id)
            .order_by(Asset.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        assets = result.scalars().all()

        q = query.lower()
        matched = [
            a for a in assets
            if q in a.name.lower()
            or any(q in str(tag).lower() for tag in (a.tags or []))
        ]

        return [
            {
                "id": str(a.id),
                "name": a.name,
                "asset_type": str(a.asset_type),
                "environment": str(a.environment),
                "criticality": str(a.criticality),
                "connector_id": str(a.connector_id) if a.connector_id else None,
                "tags": a.tags,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in matched
        ]
    finally:
        await db_cm.__aexit__(None, None, None)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/mcp_tools/assets.py
git commit -m "feat: update MCP asset tool descriptions + add search_assets tool"
```

---

### Task 6: Update MCP change request tool descriptions + add explain_change_request

**Files:**
- Modify: `backend/app/mcp_tools/change_requests.py`

- [ ] **Step 1: Update docstrings for existing tools**

In `backend/app/mcp_tools/change_requests.py`, replace the docstrings (only) for the following tools:

`list_change_types`:
```python
    """
    Discover what infrastructure changes are available. Use this first to find the right
    change_type before creating a CR.
    """
```

`get_change_type`:
```python
    """
    Get the full parameter schema for a change type including all parameters and their types.
    Use to understand what's required before creating a CR.
    """
```

`list_change_requests`:
```python
    """
    Query change history for the org. Use to answer: what changed recently, who approved it,
    and what's in flight? Filter by status (draft/approved/executed/rolled_back/failed),
    change_type, or asset_id. Returns summary fields — use get_change_request for full detail.
    """
```

`get_change_request`:
```python
    """
    Get full CR detail including plan steps, parameters, approval history, and execution log.
    Automatically includes the asset context bundle so you can validate the plan is appropriate.
    """
```

`get_change_request_plan`:
```python
    """
    Get the AI-generated execution plan for a CR — steps, estimated impact, rollback path.
    Review before approving. Call this after create_change_request and before approve_change_request.
    Automatically includes the asset context bundle.
    """
```

`create_change_request`:
```python
    """
    Create a draft Change Request for an infrastructure change against a target asset.
    The CR is created in draft state — it must be reviewed, approved, and executed separately.
    This tool NEVER executes a change directly. Use list_change_types to discover available types.
    Returns the draft CR and asset context bundle so you can validate the plan before approving.
    """
```

`approve_change_request`:
```python
    """
    Approve a Change Request. Respects role-based permissions — tokens without approval
    permission are rejected. A user cannot approve a CR they created (platform-enforced).
    """
```

`execute_change_request`:
```python
    """
    Execute an approved Change Request. Triggers the executor against the target connector.
    The CR must be in approved state. Execution is asynchronous — poll get_change_request for status.
    """
```

`rollback_change_request`:
```python
    """
    Roll back an executed Change Request using the stored rollback snapshot. Only executed CRs
    can be rolled back. Creates a rollback execution run. Returns rollback feasibility and steps.
    """
```

`submit_for_approval`:
```python
    """
    Move a CR from Draft to Awaiting Approval so approvers are notified.
    Use after create_change_request and reviewing get_change_request_plan.
    Returns updated CR status.
    """
```

- [ ] **Step 2: Add explain_change_request tool at the end of change_requests.py**

Append the following function after the closing of `get_cr_manifest`:

```python
@mcp.tool()
async def explain_change_request(token: str, cr_id: str) -> dict[str, Any]:
    """
    Get a compact human-readable summary of a CR suitable for LLM reasoning: what it does,
    what it touches, the risk level, who approved it, and whether rollback is available.
    Use instead of get_change_request when you need a quick, structured overview.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.change_request import ChangeRequest
    from app.models.approval import Approval
    from app.models.change_plan import ChangePlan

    user, db, db_cm = await _auth(token)
    try:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.id == _uuid.UUID(cr_id),
                ChangeRequest.organization_id == user.organization_id,
            ).options(selectinload(ChangeRequest.approvals))
        )
        cr = result.scalar_one_or_none()
        if cr is None:
            return {"error": "Change request not found"}

        # Fetch latest plan for risk info
        plan_result = await db.execute(
            select(ChangePlan).where(ChangePlan.change_request_id == cr.id)
            .order_by(ChangePlan.created_at.desc()).limit(1)
        )
        plan = plan_result.scalar_one_or_none()
        plan_data = plan.plan_data if plan else {}

        approvers = [
            {
                "approver_id": str(a.approver_id),
                "decision": str(a.decision),
                "comment": a.comment,
                "decided_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in (cr.approvals or [])
            if str(a.decision) == "approved"
        ]

        blast = plan_data.get("blast_radius", {})
        rollback = plan_data.get("rollback_plan", {})

        return {
            "id": str(cr.id),
            "title": cr.title,
            "change_type": str(cr.change_type),
            "lifecycle_stage": str(cr.status),
            "description": cr.description,
            "affected_asset_ids": cr.target_asset_ids or [],
            "risk_level": blast.get("estimated_impact", "unknown"),
            "rollback_available": rollback.get("automatic", False),
            "rollback_strategy": rollback.get("strategy", "unknown"),
            "approved_by": approvers,
            "created_at": cr.created_at.isoformat() if cr.created_at else None,
        }
    finally:
        await db_cm.__aexit__(None, None, None)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/mcp_tools/change_requests.py
git commit -m "feat: update MCP CR tool descriptions + add explain_change_request tool"
```

---

### Task 7: Restart frontend and verify

**Files:** none (runtime verification)

- [ ] **Step 1: Restart frontend container**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d"
```

Wait ~10 seconds for Vite to start.

- [ ] **Step 2: Verify sidebar in browser**

Open `http://100.101.186.39:3000` and confirm:
- Sidebar shows: Dashboard, Change Requests, Projects, Assets, Connectors at top
- "Operations" collapsible section with Runbooks, Scheduled Ops, Maintenance Windows, Backup & Recovery
- "Compliance & Identity" section with Findings & Remediation, Access Reviews, Compliance
- "Preview" section with Impact Simulation, Recommendations, Infrastructure Memory — each with amber "Preview" badge
- Smoke Tests no longer visible in the nav
- Bottom strip shows Notifications, Settings only

- [ ] **Step 3: Verify stub pages**

Navigate to each preview item and confirm each page loads with the amber badge, question framing, bullet list, and link to an existing alternative:
- `http://100.101.186.39:3000/impact-simulation`
- `http://100.101.186.39:3000/recommendations`
- `http://100.101.186.39:3000/infrastructure-memory`

- [ ] **Step 4: Verify MCP tools (optional — requires API token)**

```bash
# From EC2, hit the MCP endpoint to confirm new tools are listed
curl -s -H "Authorization: Bearer <your-api-token>" \
  http://localhost:8000/mcp/tools | python3 -m json.tool | grep '"name"'
# Should include: search_assets, explain_change_request
```

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -p  # stage only relevant changes
git commit -m "fix: <description of any fixes>"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| README rewrite with hero, lifecycle, personas, MCP section | Task 1 |
| Sidebar top group: Dashboard, Change Requests, Projects, Assets, Connectors | Task 2 |
| Sidebar Operations: Runbooks, Scheduled Ops, Maintenance Windows, Backup & Recovery | Task 2 |
| Sidebar Compliance & Identity: Findings & Remediation, Access Reviews, Compliance | Task 2 |
| Sidebar Preview: Impact Simulation, Recommendations, Infrastructure Memory with amber badge | Task 2 |
| Smoke Tests removed from main nav | Task 2 |
| "Asset Inventory" → "Assets" | Task 2 |
| "Vulnerability" → "Findings & Remediation" (moved to Compliance & Identity) | Task 2 |
| Three stub pages with question framing, description, bullet list, link | Task 3 |
| Routes registered for stub pages | Task 4 |
| MCP asset tool docstrings updated | Task 5 |
| `search_assets` tool added | Task 5 |
| MCP CR tool docstrings updated | Task 6 |
| `explain_change_request` tool added | Task 6 |

All spec requirements covered. ✓
