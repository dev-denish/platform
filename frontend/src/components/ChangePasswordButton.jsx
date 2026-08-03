import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../config.js";
import ErrorBanner from "./ErrorBanner.jsx";
import SuccessBanner from "./SuccessBanner.jsx";

const MIN_PASSWORD_LENGTH = 8; // mirrors app.core.security.MIN_PASSWORD_LENGTH; the API is the real enforcement point

const INITIAL = { currentPassword: "", newPassword: "", confirmPassword: "" };

/**
 * Wave: password reset (self-service side). Self-contained trigger + dialog
 * so AppShell only needs one line - placed next to "Sign out" in the
 * sidebar's user badge, the app's existing spot for account-level actions.
 *
 * Unlike ResetPasswordDialog (Administrator resetting someone ELSE), this
 * asks for the CURRENT password too - the API verifies it against the
 * caller's own stored hash before accepting a new one (same
 * verify_password() login already uses), so an unlocked, already-logged-in
 * browser can't have its password silently changed by whoever's sitting at
 * it without knowing the existing one.
 */
export default function ChangePasswordButton() {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function openDialog() {
    setForm(INITIAL);
    setError(null);
    setSuccess(false);
    setOpen(true);
  }

  function closeDialog() {
    setOpen(false);
  }

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function formValid() {
    return (
      form.currentPassword.length > 0 &&
      form.newPassword.length >= MIN_PASSWORD_LENGTH &&
      form.newPassword === form.confirmPassword
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!formValid()) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: form.currentPassword, new_password: form.newPassword,
        }),
      });
      setSuccess(true);
      setForm(INITIAL);
    } catch (err) {
      setError(err.message ?? "Could not change your password.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button type="button" className="ghost-button" onClick={openDialog}>
        Change password
      </button>
      <dialog
        ref={ref}
        className="confirm-dialog"
        onCancel={closeDialog}
        onClick={(e) => {
          if (e.target === ref.current) closeDialog();
        }}
      >
        <h2 className="confirm-dialog-title">Change your password</h2>
        {success ? (
          <>
            <SuccessBanner message="Password changed. Use it next time you sign in." />
            <div className="form-actions">
              <button type="button" className="primary-button" onClick={closeDialog}>
                Done
              </button>
            </div>
          </>
        ) : (
          <form className="form-grid" onSubmit={handleSubmit}>
            <label className="field">
              <span className="field-label">Current password</span>
              <input
                type="password"
                className="field-input"
                value={form.currentPassword}
                onChange={(e) => update("currentPassword", e.target.value)}
                autoComplete="current-password"
                disabled={submitting}
              />
            </label>
            <label className="field">
              <span className="field-label">New password</span>
              <input
                type="password"
                className="field-input"
                value={form.newPassword}
                onChange={(e) => update("newPassword", e.target.value)}
                autoComplete="new-password"
                disabled={submitting}
              />
              <span className="field-hint">At least {MIN_PASSWORD_LENGTH} characters.</span>
            </label>
            <label className="field">
              <span className="field-label">Confirm new password</span>
              <input
                type="password"
                className="field-input"
                value={form.confirmPassword}
                onChange={(e) => update("confirmPassword", e.target.value)}
                autoComplete="new-password"
                disabled={submitting}
              />
              {form.confirmPassword && form.confirmPassword !== form.newPassword ? (
                <span className="field-hint">Passwords don&apos;t match.</span>
              ) : null}
            </label>
            <ErrorBanner message={error} />
            <div className="form-actions">
              <button type="button" className="ghost-button" onClick={closeDialog} disabled={submitting}>
                Cancel
              </button>
              <button type="submit" className="primary-button" disabled={submitting || !formValid()}>
                {submitting ? "Changing…" : "Change password"}
              </button>
            </div>
          </form>
        )}
      </dialog>
    </>
  );
}
