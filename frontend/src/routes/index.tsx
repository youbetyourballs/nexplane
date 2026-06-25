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
