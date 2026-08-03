import { useEffect, useState } from "react";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Pagination from "../components/Pagination.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import PermanentDeleteDialog from "../components/PermanentDeleteDialog.jsx";
import BulkPermanentDeleteDialog from "../components/BulkPermanentDeleteDialog.jsx";
import ResetPasswordDialog from "../components/ResetPasswordDialog.jsx";
import { RoleBadge } from "../components/StatusBadge.jsx";
import { formatDate } from "../lib/format.js";
import { ROLES, canManageUsers } from "../lib/roles.js";

const LIMIT = 50;
const CREATE_ROLES = Object.values(ROLES);
const MIN_PASSWORD_LENGTH = 8; // mirrors app.core.security.MIN_PASSWORD_LENGTH; the API is the real enforcement point

const CREATE_INITIAL = { username: "", password: "", role: ROLES.VIEWER };

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const [page, setPage] = useState(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Wave: three-tier removal - hidden accounts are excluded from the
  // default list; this toggle asks the API to include them instead of
  // filtering client-side (so pagination/total stay correct either way).
  const [showHidden, setShowHidden] = useState(false);

  const [form, setForm] = useState(CREATE_INITIAL);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  // Held ONLY in this component's own state, from the admin's own form
  // input - never round-tripped through the API response (UserOut never
  // carries a password field), and cleared the moment they dismiss it.
  const [justCreated, setJustCreated] = useState(null);

  const [confirmTarget, setConfirmTarget] = useState(null);
  const [deactivatingId, setDeactivatingId] = useState(null);
  const [hidingId, setHidingId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  const [resetTarget, setResetTarget] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState(null);
  // Same one-time-reveal convention as justCreated above - held only in
  // this component's own state, from the admin's own form input.
  const [justReset, setJustReset] = useState(null);

  const [selected, setSelected] = useState(() => new Set());
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const canDelete = currentUser && canManageUsers(currentUser.role);

  useEffect(() => {
    load(offset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, showHidden]);

  async function load(currentOffset) {
    setLoading(true);
    setError(null);
    setSelected(new Set());
    try {
      setPage(
        await apiFetch(
          `/users?limit=${LIMIT}&offset=${currentOffset}&include_hidden=${showHidden}`
        )
      );
    } catch (err) {
      setError(err.message ?? "Could not load users.");
    } finally {
      setLoading(false);
    }
  }

  function toggleSelected(userId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  function toggleSelectAll() {
    const visibleIds = page?.items.map((u) => u.user_id) ?? [];
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
    setSelected(allSelected ? new Set() : new Set(visibleIds));
  }

  function updateForm(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function formValid() {
    return form.username.trim().length > 0 && form.password.length >= MIN_PASSWORD_LENGTH;
  }

  async function handleCreate(e) {
    e.preventDefault();
    if (!formValid()) return;
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch("/users", {
        method: "POST",
        body: JSON.stringify({
          username: form.username.trim(), password: form.password, role: form.role,
        }),
      });
      setJustCreated({ username: form.username.trim(), password: form.password });
      setForm(CREATE_INITIAL);
      await load(0);
      setOffset(0);
    } catch (err) {
      setCreateError(err.message ?? "Could not create this user.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeactivate() {
    const target = confirmTarget;
    setConfirmTarget(null);
    setDeactivatingId(target.user_id);
    setError(null);
    try {
      await apiFetch(`/users/${target.user_id}`, { method: "DELETE" });
      await load(offset);
    } catch (err) {
      setError(err.message ?? "Could not deactivate this user.");
    } finally {
      setDeactivatingId(null);
    }
  }

  async function handleActivate(user) {
    setDeactivatingId(user.user_id);
    setError(null);
    try {
      await apiFetch(`/users/${user.user_id}/activate`, { method: "POST" });
      await load(offset);
    } catch (err) {
      setError(err.message ?? "Could not activate this user.");
    } finally {
      setDeactivatingId(null);
    }
  }

  async function handleHide(user) {
    setHidingId(user.user_id);
    setError(null);
    try {
      await apiFetch(`/users/${user.user_id}/hide`, { method: "POST" });
      await load(offset);
    } catch (err) {
      setError(err.message ?? "Could not hide this user.");
    } finally {
      setHidingId(null);
    }
  }

  async function handleUnhide(user) {
    setHidingId(user.user_id);
    setError(null);
    try {
      await apiFetch(`/users/${user.user_id}/unhide`, { method: "POST" });
      await load(offset);
    } catch (err) {
      setError(err.message ?? "Could not unhide this user.");
    } finally {
      setHidingId(null);
    }
  }

  async function handlePermanentDelete() {
    const target = deleteTarget;
    setDeleteTarget(null);
    setDeletingId(target.user_id);
    setError(null);
    try {
      await apiFetch(`/users/${target.user_id}/permanent`, { method: "DELETE" });
      await load(offset);
    } catch (err) {
      setError(err.message ?? "Could not permanently delete this user.");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleBulkDelete() {
    const ids = [...selected];
    setBulkConfirmOpen(false);
    setBulkDeleting(true);
    setError(null);
    try {
      const { results } = await apiFetch("/users/bulk-permanent-delete", {
        method: "POST",
        body: JSON.stringify({ ids }),
      });
      const failed = results.filter((r) => !r.success);
      const succeededCount = results.length - failed.length;
      await load(offset); // also clears `selected`
      if (failed.length > 0) {
        const successPrefix = succeededCount > 0 ? `${succeededCount} deleted. ` : "";
        setError(
          `${successPrefix}${failed.length} could not be deleted: ` +
            failed.map((r) => `"${r.name}" (${r.error})`).join("; ")
        );
      }
    } catch (err) {
      setError(err.message ?? "Could not delete these users.");
    } finally {
      setBulkDeleting(false);
    }
  }

  async function handleResetPassword(newPassword) {
    const target = resetTarget;
    setResetting(true);
    setResetError(null);
    try {
      await apiFetch(`/users/${target.user_id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password: newPassword }),
      });
      setResetTarget(null);
      setJustReset({ username: target.username, password: newPassword });
    } catch (err) {
      setResetError(err.message ?? "Could not reset this user's password.");
    } finally {
      setResetting(false);
    }
  }

  const selectedUsernames = page ? page.items.filter((u) => selected.has(u.user_id)).map((u) => u.username) : [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Administration</p>
          <h1 className="page-title">Users</h1>
        </div>
      </header>

      {justCreated ? (
        <div className="success-banner credentials-banner" role="status">
          <div className="credentials-banner-body">
            <p className="credentials-banner-title">
              Account created for <strong>{justCreated.username}</strong>
            </p>
            <p className="field-hint">
              Copy this password now and share it securely - it will not be shown again.
            </p>
            <code className="credentials-banner-password">{justCreated.password}</code>
          </div>
          <button type="button" className="ghost-button" onClick={() => setJustCreated(null)}>
            Dismiss
          </button>
        </div>
      ) : null}

      {justReset ? (
        <div className="success-banner credentials-banner" role="status">
          <div className="credentials-banner-body">
            <p className="credentials-banner-title">
              Password reset for <strong>{justReset.username}</strong>
            </p>
            <p className="field-hint">
              Copy this password now and share it securely - it will not be shown again.
            </p>
            <code className="credentials-banner-password">{justReset.password}</code>
          </div>
          <button type="button" className="ghost-button" onClick={() => setJustReset(null)}>
            Dismiss
          </button>
        </div>
      ) : null}

      <ErrorBanner message={error} onRetry={() => load(offset)} />

      <ConfirmDialog
        open={confirmTarget != null}
        title="Deactivate this user?"
        detail={
          confirmTarget
            ? `"${confirmTarget.username}" will no longer be able to log in. Their account history is kept, and the username can be reused for a new account if needed.`
            : ""
        }
        confirmLabel="Deactivate"
        danger
        onConfirm={handleDeactivate}
        onCancel={() => setConfirmTarget(null)}
      />

      <PermanentDeleteDialog
        open={deleteTarget != null}
        username={deleteTarget?.username}
        onConfirm={handlePermanentDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      <ResetPasswordDialog
        open={resetTarget != null}
        username={resetTarget?.username}
        error={resetError}
        submitting={resetting}
        onConfirm={handleResetPassword}
        onCancel={() => setResetTarget(null)}
      />

      <BulkPermanentDeleteDialog
        open={bulkConfirmOpen}
        usernames={selectedUsernames}
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkConfirmOpen(false)}
      />

      {canDelete && selected.size > 0 ? (
        <div className="bulk-action-bar">
          <span className="bulk-action-bar-count">{selected.size} selected</span>
          <button
            type="button"
            className="danger-button"
            disabled={bulkDeleting}
            onClick={() => setBulkConfirmOpen(true)}
          >
            {bulkDeleting ? "Deleting…" : "Permanently delete"}
          </button>
        </div>
      ) : null}

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Create user</h2>
        </div>
        <form className="form-grid" onSubmit={handleCreate}>
          <label className="field">
            <span className="field-label">Username</span>
            <input
              className="field-input"
              value={form.username}
              onChange={(e) => updateForm("username", e.target.value)}
              disabled={creating}
              autoComplete="off"
            />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input
              type="password"
              className="field-input"
              value={form.password}
              onChange={(e) => updateForm("password", e.target.value)}
              disabled={creating}
              autoComplete="new-password"
            />
            <span className="field-hint">At least {MIN_PASSWORD_LENGTH} characters.</span>
          </label>
          <label className="field">
            <span className="field-label">Role</span>
            <select
              className="field-input"
              value={form.role}
              onChange={(e) => updateForm("role", e.target.value)}
              disabled={creating}
            >
              {CREATE_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
          <ErrorBanner message={createError} />
          <div className="form-actions">
            <button type="submit" className="primary-button" disabled={creating || !formValid()}>
              {creating ? "Creating…" : "Create user"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">All accounts</h2>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => {
                setShowHidden(e.target.checked);
                setOffset(0);
              }}
            />
            Show hidden
          </label>
        </div>
        {loading ? (
          <div className="full-screen-center">
            <Spinner label="Loading users…" />
          </div>
        ) : !page || page.items.length === 0 ? (
          <EmptyState title="No users yet" />
        ) : (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  {canDelete ? (
                    <th className="select-cell">
                      <input
                        type="checkbox"
                        aria-label="Select all users on this page"
                        checked={
                          page.items.length > 0 && page.items.every((u) => selected.has(u.user_id))
                        }
                        onChange={toggleSelectAll}
                      />
                    </th>
                  ) : null}
                  <th>Username</th>
                  <th>Role</th>
                  <th>Created</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((u) => {
                  const active = !u.deleted_at;
                  const hidden = !!u.hidden_at;
                  const busy = deactivatingId === u.user_id || hidingId === u.user_id || deletingId === u.user_id;
                  return (
                    <tr key={u.user_id}>
                      {canDelete ? (
                        <td className="select-cell">
                          <input
                            type="checkbox"
                            aria-label={`Select "${u.username}"`}
                            checked={selected.has(u.user_id)}
                            onChange={() => toggleSelected(u.user_id)}
                          />
                        </td>
                      ) : null}
                      <td>{u.username}</td>
                      <td>
                        <RoleBadge role={u.role} />
                      </td>
                      <td className="mono-cell">{formatDate(u.created_at)}</td>
                      <td>
                        <span className={`status-badge ${active ? "tone-active" : "tone-archived"}`}>
                          <span className="status-dot" aria-hidden="true" />
                          {active ? "Active" : "Deactivated"}
                        </span>
                        {hidden ? (
                          <span className="status-badge tone-archived status-badge-gap">
                            <span className="status-dot" aria-hidden="true" />
                            Hidden
                          </span>
                        ) : null}
                      </td>
                      <td className="table-actions-cell table-actions-multi">
                        {active ? (
                          <button
                            type="button"
                            className="link-button table-danger-link"
                            disabled={busy}
                            onClick={() => setConfirmTarget(u)}
                          >
                            {deactivatingId === u.user_id ? "Deactivating…" : "Deactivate"}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="link-button"
                            disabled={busy}
                            onClick={() => handleActivate(u)}
                          >
                            {deactivatingId === u.user_id ? "Activating…" : "Activate"}
                          </button>
                        )}
                        {hidden ? (
                          <button
                            type="button"
                            className="link-button"
                            disabled={busy}
                            onClick={() => handleUnhide(u)}
                          >
                            {hidingId === u.user_id ? "Unhiding…" : "Unhide"}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="link-button"
                            disabled={busy}
                            onClick={() => handleHide(u)}
                          >
                            {hidingId === u.user_id ? "Hiding…" : "Hide"}
                          </button>
                        )}
                        {u.user_id !== currentUser?.user_id ? (
                          <button
                            type="button"
                            className="link-button"
                            disabled={busy}
                            onClick={() => {
                              setResetError(null);
                              setResetTarget(u);
                            }}
                          >
                            Reset password
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="link-button table-danger-link"
                          disabled={busy}
                          onClick={() => setDeleteTarget(u)}
                        >
                          {deletingId === u.user_id ? "Deleting…" : "Permanently delete"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <Pagination total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
          </>
        )}
      </section>
    </div>
  );
}
