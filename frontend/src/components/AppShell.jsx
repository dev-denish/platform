import { Suspense } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { canManageUsers, canManageWmsSources, canUpload } from "../lib/roles.js";
import { RoleBadge } from "./StatusBadge.jsx";
import ChangePasswordButton from "./ChangePasswordButton.jsx";
import Spinner from "./Spinner.jsx";

export default function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="shell">
      <aside className="shell-sidebar">
        <div className="shell-brand">
          <span className="shell-brand-mark" aria-hidden="true" />
          <div>
            <div className="shell-brand-name">dMRV</div>
            <div className="shell-brand-sub">Analytical Platform</div>
          </div>
        </div>

        <nav className="shell-nav">
          <div className="shell-nav-group">
            <div className="shell-nav-group-label">Overview</div>
            <NavLink to="/" end className={navLinkClass}>
              <NavIcon d="M5 12 L12 5 L19 12 M7 10 V19 H17 V10" />
              Dashboard
            </NavLink>
          </div>

          <div className="shell-nav-group">
            <div className="shell-nav-group-label">Projects</div>
            <NavLink to="/projects" className={navLinkClass}>
              <NavIcon d="M4 6 H20 M4 12 H20 M4 18 H14" />
              Projects
            </NavLink>
            {user && canUpload(user.role) ? (
              <NavLink to="/upload" className={navLinkClass}>
                <NavIcon d="M12 19 V6 M6 11 L12 5 L18 11 M5 19 H19" />
                Upload dataset
              </NavLink>
            ) : null}
          </div>

          {user && (canManageUsers(user.role) || canManageWmsSources(user.role)) ? (
            <div className="shell-nav-group">
              <div className="shell-nav-group-label">Administration</div>
              {canManageUsers(user.role) ? (
                <NavLink to="/users" className={navLinkClass}>
                  <NavIcon d="M9 11 A3.5 3.5 0 1 0 9 4 A3.5 3.5 0 1 0 9 11 M2.5 20 C2.5 15.5 5.5 13.5 9 13.5 C12.5 13.5 15.5 15.5 15.5 20 M16 6 A2.5 2.5 0 1 1 16 11 M15 13.7 C18 14 20 15.8 20 19.5" />
                  Users
                </NavLink>
              ) : null}
              {canManageWmsSources(user.role) ? (
                <NavLink to="/wms-domains" className={navLinkClass}>
                  <NavIcon d="M12 3 C7 3 3 7 3 12 C3 17 7 21 12 21 C17 21 21 17 21 12 C21 7 17 3 12 3 M3 12 H21 M12 3 C14.5 5.5 15.8 8.6 15.8 12 C15.8 15.4 14.5 18.5 12 21 M12 3 C9.5 5.5 8.2 8.6 8.2 12 C8.2 15.4 9.5 18.5 12 21" />
                  WMS/WFS domains
                </NavLink>
              ) : null}
            </div>
          ) : null}
        </nav>

        <div className="shell-user">
          <div className="shell-user-id">
            <div className="shell-user-name">{user?.username}</div>
            <RoleBadge role={user?.role} />
          </div>
          <div className="shell-user-actions">
            <ChangePasswordButton />
            <button type="button" className="ghost-button" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="shell-main">
        <Suspense
          fallback={
            <div className="full-screen-center">
              <Spinner label="Loading…" />
            </div>
          }
        >
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}

function navLinkClass({ isActive }) {
  return `shell-nav-link${isActive ? " shell-nav-link-active" : ""}`;
}

function NavIcon({ d }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
