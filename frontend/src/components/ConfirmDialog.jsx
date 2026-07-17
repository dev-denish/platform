import { useEffect, useRef } from "react";

/**
 * Minimal confirmation modal built on the native <dialog> element: real modal
 * behavior (backdrop, focus trap, Esc-to-cancel) with no extra dependency and
 * almost no bespoke CSS - matches the app's "no new dependencies" convention.
 */
export default function ConfirmDialog({
  open,
  title,
  detail,
  confirmLabel = "Confirm",
  onConfirm,
  onCancel,
  danger = false,
}) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.(); // click on the backdrop itself
      }}
    >
      <h2 className="confirm-dialog-title">{title}</h2>
      {detail ? <p className="confirm-dialog-detail">{detail}</p> : null}
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={danger ? "danger-button" : "primary-button"}
          onClick={onConfirm}
        >
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
