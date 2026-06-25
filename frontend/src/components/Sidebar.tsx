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
import { DemoOrgSwitcher } from './DemoOrgSwitcher';

// ── nav item definitions ─────────────────────────────────────────────────────

const coreNavItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/projects", label: "Projects", icon: FolderOpen },
];

const discoverNavItems = [
  { to: "/assets", label: "Assets", icon: Server },
  { to: "/connectors", label: "Connectors", icon: Plug },
  { to: "/infrastructure-memory", label: "Infrastructure Memory", icon: Brain },
];

const observeNavItems = [
  { to: "/impact-simulation", label: "Impact Simulation", icon: Zap },
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
  { to: "/recommendations", label: "Recommendations", icon: Lightbulb },
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
        {/* Core workflow */}
        {coreNavItems.map(({ to, label, icon, exact }) => (
          <NavItem
            key={to}
            to={to}
            label={label}
            icon={icon}
            exact={exact}
            badge={to === "/change-requests" && pendingCount > 0 ? pendingCount : undefined}
          />
        ))}

        {/* Discover */}
        <div className="px-3 pt-4 pb-1">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Discover
          </span>
        </div>
        {discoverNavItems.map(({ to, label, icon }) => (
          <NavItem key={to} to={to} label={label} icon={icon} />
        ))}

        {/* Observe */}
        <div className="px-3 pt-4 pb-1">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Observe
          </span>
        </div>
        {observeNavItems.map(({ to, label, icon }) => (
          <NavItem key={to} to={to} label={label} icon={icon} />
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

        <DemoOrgSwitcher />

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
