import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../config.js";
import ErrorBanner from "./ErrorBanner.jsx";
import { formatDate } from "../lib/format.js";
import { PERMISSION_REGISTRY } from "../lib/permissions.js";

/**
 * Wave: permission grants. Per-user panel of individually grantable
 * permissions, opened from the Users table's "Manage" link. Renders
 * PERMISSION_REGISTRY generically - a toggle per row, disabled while its own
 * request is in flight - so a second grantable permission is a registry
 * entry, never new UI here. Each toggle applies immediately (its own PUT/
 * DELETE), same "no batched save" convention as ProjectMembers's add/remove.
 */
export default function ManagePermissionsPanel({ open, user, onClose, onChanged }) {
  const ref = useRef(null);
  const [grants, setGrants] = useState(null);
  const [error, setError] = useState(null);
  const [busyName, setBusyName] = useState(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (!open || !user) {
      setGrants(null);
      return;
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, user?.user_id]);

  async function load() {
    setError(null);
    try {
      const res = await apiFetch(`/users/${user.user_id}/permissions`);
      setGrants(res.grants);
    } catch (err) {
      setError(err.message ?? "Could not load this user's permissions.");
    }
  }

  async function toggle(permissionName, granted) {
    setBusyName(permissionName);
    setError(null);
    try {
      await apiFetch(`/users/${user.user_id}/permissions/${permissionName}`, {
        method: granted ? "DELETE" : "PUT",
      });
      await load();
      await onChanged?.();
    } catch (err) {
      setError(err.message ?? "Could not update this permission.");
    } finally {
      setBusyName(null);
    }
  }

  const isAdministrator = user?.role === "Administrator";

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose?.();
      }}
    >
      <h2 className="confirm-dialog-title">Permissions — {user?.username}</h2>
      <p className="field-hint">
        Role: {user?.role} (unchanged here - change role from the Users table.)
      </p>

      <ErrorBanner message={error} />

      {isAdministrator ? (
        <p className="field-hint">
          Administrators already have every permission implicitly - there is nothing to grant.
        </p>
      ) : !grants ? (
        <p className="field-hint">Loading…</p>
      ) : (
        <ul className="permission-toggle-list">
          {PERMISSION_REGISTRY.map((p) => {
            const grant = grants.find((g) => g.permission_name === p.name);
            const granted = !!grant;
            return (
              <li key={p.name} className="permission-toggle-row">
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={granted}
                    disabled={busyName === p.name}
                    onChange={() => toggle(p.name, granted)}
                  />
                  {p.label}
                </label>
                <p className="field-hint">{p.description}</p>
                {granted ? (
                  <p className="field-hint">
                    Granted by {grant.granted_by_username ?? "an account that no longer exists"} on{" "}
                    {formatDate(grant.granted_at)}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onClose}>
          Close
        </button>
      </div>
    </dialog>
  );
}
