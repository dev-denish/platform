export default function EmptyState({ title, detail, action }) {
  return (
    <div className="empty-state">
      <div className="empty-state-glyph" aria-hidden="true">
        <svg viewBox="0 0 48 48" width="40" height="40">
          <path
            d="M6 38 L18 22 L26 30 L42 10"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="3 5"
          />
          <circle cx="42" cy="10" r="2.5" fill="currentColor" />
        </svg>
      </div>
      <p className="empty-state-title">{title}</p>
      {detail ? <p className="empty-state-detail">{detail}</p> : null}
      {action ?? null}
    </div>
  );
}
