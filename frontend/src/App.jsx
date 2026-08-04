import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext.jsx";
import ProtectedRoute from "./routes/ProtectedRoute.jsx";
import RoleRoute from "./routes/RoleRoute.jsx";
import AppShell from "./components/AppShell.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import { MANAGE_USERS_ROLES, MANAGE_WMS_SOURCES_ROLES, UPLOAD_ROLES } from "./lib/roles.js";

// Code-split per shell route - AppShell wraps its <Outlet /> in the one
// Suspense boundary these all render through (see AppShell.jsx).
const ProjectsPage = lazy(() => import("./pages/ProjectsPage.jsx"));
const ProjectDetailPage = lazy(() => import("./pages/ProjectDetailPage.jsx"));
const UploadPage = lazy(() => import("./pages/UploadPage.jsx"));
const UsersPage = lazy(() => import("./pages/UsersPage.jsx"));
const WmsDomainsPage = lazy(() => import("./pages/WmsDomainsPage.jsx"));

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            {/* Redesign: Projects is the app's home page now - Dashboard moved
                inside ProjectDetailPage (Dashboard/Maps toggle). "/projects"
                stays a working path too, so an existing bookmark doesn't 404. */}
            <Route index element={<ProjectsPage />} />
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
