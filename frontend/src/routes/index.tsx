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
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/:id" element={<AssetDetail />} />
        <Route path="/connectors" element={<Connectors />} />
      </Route>
    </Routes>
  );
}
