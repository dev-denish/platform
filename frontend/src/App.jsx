import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext.jsx";
import ProtectedRoute from "./routes/ProtectedRoute.jsx";
import RoleRoute from "./routes/RoleRoute.jsx";
import AppShell from "./components/AppShell.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import ProjectDetailPage from "./pages/ProjectDetailPage.jsx";
import UploadPage from "./pages/UploadPage.jsx";
import UsersPage from "./pages/UsersPage.jsx";
import WmsDomainsPage from "./pages/WmsDomainsPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import { MANAGE_USERS_ROLES, MANAGE_WMS_SOURCES_ROLES, UPLOAD_ROLES } from "./lib/roles.js";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route index element={<DashboardPage />} />
            <Route path="projects" element={<ProjectsPage />} />
            <Route path="projects/:projectId" element={<ProjectDetailPage />} />
            <Route element={<RoleRoute allow={UPLOAD_ROLES} />}>
              <Route path="upload" element={<UploadPage />} />
            </Route>
            <Route element={<RoleRoute allow={MANAGE_USERS_ROLES} />}>
              <Route path="users" element={<UsersPage />} />
            </Route>
            <Route element={<RoleRoute allow={MANAGE_WMS_SOURCES_ROLES} />}>
              <Route path="wms-domains" element={<WmsDomainsPage />} />
            </Route>
          </Route>
        </Route>

        <Route path="/404" element={<NotFoundPage />} />
        <Route path="*" element={<Navigate to="/404" replace />} />
      </Routes>
    </AuthProvider>
  );
}
