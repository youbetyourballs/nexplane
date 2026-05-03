import { NavLink } from "react-router-dom";
import clsx from "clsx";
import {
  LayoutDashboard,
  FileStack,
  CheckSquare,
  Plug,
  Server,
  FolderOpen,
  Settings as SettingsIcon,
  BookOpen,
} from "lucide-react";
import { useAuth } from "../hooks/useAuth";

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/runbooks", label: "Runbooks", icon: BookOpen },
  { to: "/approvals", label: "Approvals Queue", icon: CheckSquare },
  { to: "/assets", label: "Asset Inventory", icon: Server },
  { to: "/connectors", label: "Connectors", icon: Plug },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export function Sidebar() {
  const { user, logout } = useAuth();

  return (
    <aside className="fixed inset-y-0 left-0 w-60 bg-navy flex flex-col z-10">
      <div className="flex items-center px-5 py-4 border-b border-navy-border">
        <img src="/title_white.png" alt="Nexplane" className="h-8 w-auto" />
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map(({ to, label, icon: Icon, exact }) => (
          <NavLink
            key={to}
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
            <Icon className="w-4 h-4 flex-shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>

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
