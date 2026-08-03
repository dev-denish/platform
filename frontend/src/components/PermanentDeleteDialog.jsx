import { useEffect, useRef, useState } from "react";

/**
 * Wave: three-tier user removal. Deliberately NOT a variant of ConfirmDialog:
 * a single confirm click is explicitly called out as unacceptable for an
 * irreversible action, so this requires typing the exact username before the
 * delete button even enables - a real second, deliberate action, not a
 * cosmetic double-click.
 */
export default function PermanentDeleteDialog({ open, username, onConfirm, onCancel }) {
  const ref = useRef(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setTyped("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const matches = username != null && typed === username;

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.();
      }}
    >
      <h2 className="confirm-dialog-title">Permanently delete this user?</h2>
      <p className="confirm-dialog-detail">
        This cannot be undone - unlike Deactivate or Hide, there is no restore. &quot;
        {username}&quot;&apos;s account row will be removed entirely. Anything they were
        attributed on (added a project member, past audit log entries, etc.) is kept, with
        that attribution cleared - it will still show their username as plain text.
      </p>
      <label className="field">
        <span className="field-label">
          Type <strong>{username}</strong> to confirm
        </span>
        <input
          className="field-input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
        />
      </label>
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="danger-button" disabled={!matches} onClick={onConfirm}>
          Permanently delete
        </button>
      </div>
    </dialog>
  );
}
