import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { canUpload } from "../lib/roles.js";
import { RoleBadge } from "./StatusBadge.jsx";

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
          <NavLink to="/" end className={navLinkClass}>
            <NavIcon d="M5 12 L12 5 L19 12 M7 10 V19 H17 V10" />
            Dashboard
          </NavLink>
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
        </nav>

        <div className="shell-user">
          <div className="shell-user-id">
            <div className="shell-user-name">{user?.username}</div>
            <RoleBadge role={user?.role} />
          </div>
          <button type="button" className="ghost-button" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="shell-main">
        <Outlet />
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
