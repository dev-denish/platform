import { useEffect, useRef, useState } from "react";

/**
 * Wave: bulk select + delete. Bulk equivalent of PermanentDeleteDialog's
 * type-the-username gate - typing each of N usernames doesn't scale, so this
 * requires typing the literal word DELETE once instead, with every affected
 * username listed above the input so it's still a real, deliberate look
 * before confirming, not a silent one-click.
 */
export default function BulkPermanentDeleteDialog({ open, usernames, onConfirm, onCancel }) {
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

  const matches = typed === "DELETE";

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.();
      }}
    >
      <h2 className="confirm-dialog-title">
        Permanently delete {usernames?.length ?? 0} user{usernames?.length === 1 ? "" : "s"}?
      </h2>
      <p className="confirm-dialog-detail">
        This cannot be undone - unlike Deactivate or Hide, there is no restore. Each account row
        will be removed entirely; anything they were attributed on keeps that history text with
        the attribution cleared.
      </p>
      <ul className="bulk-delete-list">
        {(usernames ?? []).map((u) => (
          <li key={u}>{u}</li>
        ))}
      </ul>
      <label className="field">
        <span className="field-label">
          Type <strong>DELETE</strong> to confirm
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
