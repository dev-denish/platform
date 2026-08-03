import { useEffect, useRef, useState } from "react";
import ErrorBanner from "./ErrorBanner.jsx";

const MIN_PASSWORD_LENGTH = 8; // mirrors app.core.security.MIN_PASSWORD_LENGTH; the API is the real enforcement point

/**
 * Wave: password reset (Administrator side). No "current password" field -
 * the admin isn't that person, so there's nothing of theirs to verify (see
 * ChangePasswordButton.jsx for the self-service side, which DOES ask for
 * one). Never offered for the logged-in admin's own row - UsersPage hides
 * this action there entirely; self-service change is the only path for
 * your own account.
 */
export default function ResetPasswordDialog({ open, username, error, submitting, onConfirm, onCancel }) {
  const ref = useRef(null);
  const [password, setPassword] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setPassword("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const valid = password.length >= MIN_PASSWORD_LENGTH;

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.();
      }}
    >
      <h2 className="confirm-dialog-title">Reset password for {username}</h2>
      <p className="confirm-dialog-detail">
        Sets a brand-new password immediately - their old password stops working right away.
        No need to know it first.
      </p>
      <label className="field">
        <span className="field-label">New password</span>
        <input
          type="password"
          className="field-input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          disabled={submitting}
        />
        <span className="field-hint">At least {MIN_PASSWORD_LENGTH} characters.</span>
      </label>
      <ErrorBanner message={error} />
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={!valid || submitting}
          onClick={() => onConfirm(password)}
        >
          {submitting ? "Resetting…" : "Reset password"}
        </button>
      </div>
    </dialog>
  );
}
