const STATUS_TONE = {
  Active: "tone-active",
  "Under Review": "tone-review",
  Archived: "tone-archived",
};

export function StatusBadge({ status }) {
  const tone = STATUS_TONE[status] ?? "tone-archived";
  return (
    <span className={`status-badge ${tone}`}>
      <span className="status-dot" aria-hidden="true" />
      {status}
    </span>
  );
}

export function RoleBadge({ role }) {
  return <span className="role-badge">{role}</span>;
}
