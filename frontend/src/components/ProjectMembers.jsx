import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { apiFetch } from "../config.js";
import ErrorBanner from "./ErrorBanner.jsx";
import EmptyState from "./EmptyState.jsx";
import { formatDate } from "../lib/format.js";
import { PROJECT_ROLES, canManageMembers } from "../lib/roles.js";
import { useCollapse } from "../lib/useCollapse.js";

/**
 * Wave: project-level RBAC. `members` is null while the section's own
 * request is in flight (kept separate from the page's main loading state -
 * a project a user CAN see but isn't managing still renders everything else
 * on the page immediately). `onChanged` re-fetches just this section, the
 * same pattern ProjectDetailPage's `reloadLayers` already uses for tiles.
 *
 * Visibility choice (explicitly called out, not left implicit): a
 * read-only member list is shown to EVERYONE who can see the project at
 * all, not just managers - who's on a project isn't sensitive information
 * within a project you already have view access to, and it tells a
 * non-manager (e.g. a Viewer) who to ask for a role change.
 */
export default function ProjectMembers({ projectId, members, currentUser, onChanged }) {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState(PROJECT_ROLES[0]);
  const [busyId, setBusyId] = useState(null); // user_id of the row being mutated, or "new"
  const [error, setError] = useState(null);
  const [open, toggleOpen] = useCollapse(`collapse:project:${projectId}:members`, true);

  const myMembership = members?.find((m) => m.user_id === currentUser?.user_id);
  const canManage = canManageMembers(currentUser?.role, myMembership?.role);

  async function handleAdd(e) {
    e.preventDefault();
    if (!username.trim()) return;
    setBusyId("new");
    setError(null);
    try {
      await apiFetch(`/projects/${projectId}/members`, {
        method: "POST",
        body: JSON.stringify({ username: username.trim(), role }),
      });
      setUsername("");
      await onChanged();
    } catch (err) {
      setError(err.message ?? "Could not add this member.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleRemove(userId) {
    setBusyId(userId);
    setError(null);
    try {
      await apiFetch(`/projects/${projectId}/members/${userId}`, { method: "DELETE" });
      await onChanged();
    } catch (err) {
      setError(err.message ?? "Could not remove this member.");
      setBusyId(null);
    }
  }

  async function handleRoleChange(userId, newRole) {
    setBusyId(userId);
    setError(null);
    try {
      await apiFetch(`/projects/${projectId}/members/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      });
      await onChanged();
    } catch (err) {
      setError(err.message ?? "Could not change this member's role.");
      setBusyId(null);
    }
  }

  return (
    <section className="panel">
      <button className="collapsible-header" aria-expanded={open} onClick={toggleOpen}>
        <span>Members</span>
        <span className={`collapsible-chevron${open ? " collapsible-chevron-open" : ""}`} aria-hidden="true">
          <ChevronDown size={16} strokeWidth={2} className="icon" />
        </span>
      </button>
      <div className="collapsible-body" data-open={open} inert={open ? undefined : ""}>
        <div className="collapsible-body-inner">
          <ErrorBanner message={error} />
          {!members || members.length === 0 ? (
            <EmptyState
              title="No members yet"
              detail="Only Administrators can see this project until someone is added as a member."
            />
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Project role</th>
                  <th>Added</th>
                  {canManage ? <th /> : null}
                </tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.user_id}>
                    <td>{m.username}</td>
                    <td className="mono-cell">
                      {canManage ? (
                        <select
                          className="field-input"
                          value={m.role}
                          disabled={busyId === m.user_id}
                          onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                        >
                          {PROJECT_ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      ) : (
                        m.role
                      )}
                    </td>
                    <td className="mono-cell">{formatDate(m.added_at)}</td>
                    {canManage ? (
                      <td className="table-actions-cell">
                        <button
                          type="button"
                          className="link-button table-danger-link"
                          disabled={busyId === m.user_id}
                          onClick={() => handleRemove(m.user_id)}
                        >
                          {busyId === m.user_id ? "Removing…" : "Remove"}
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {canManage ? (
            <form className="form-actions" onSubmit={handleAdd}>
              <input
                className="field-input"
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={busyId === "new"}
                aria-label="Username to add"
              />
              <select
                className="field-input"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                disabled={busyId === "new"}
                aria-label="Project role for new member"
              >
                {PROJECT_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
              <button type="submit" className="primary-button" disabled={busyId === "new" || !username.trim()}>
                {busyId === "new" ? "Adding…" : "Add member"}
              </button>
            </form>
          ) : null}
        </div>
      </div>
    </section>
  );
}
