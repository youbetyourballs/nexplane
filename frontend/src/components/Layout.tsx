// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Sidebar } from "./Sidebar";
import { FreezeAlert } from "./FreezeAlert";
import { apiClient } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import UpdateBanner from "./UpdateBanner";
import DegradedModeBanner from "./DegradedModeBanner";

export function Layout() {
  const [freezeDismissed, setFreezeDismissed] = useState(false);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { data: activeFreeze } = useQuery({
    queryKey: ["active-freeze"],
    queryFn: () =>
      apiClient
        .get("/compliance/freeze-windows/active")
        .then((r) => r.data)
        .catch(() => null),
    refetchInterval: 60_000,
  });

  return (
    <>
      <DegradedModeBanner isAdmin={isAdmin} />
      {activeFreeze && !freezeDismissed && (
        <FreezeAlert
          reason={activeFreeze.reason}
          endAt={activeFreeze.end_at}
          onDismiss={() => setFreezeDismissed(true)}
        />
      )}
      <UpdateBanner isAdmin={isAdmin} />
      <div className="min-h-screen bg-slate-50 flex">
        <Sidebar />
        <main className="flex-1 ml-60 min-h-screen">
          <Outlet />
        </main>
      </div>
    </>
  );
}
