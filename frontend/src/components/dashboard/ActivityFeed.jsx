import { useEffect, useState } from "react";
import { apiFetch } from "../../config.js";
import ErrorBanner from "../ErrorBanner.jsx";
import EmptyState from "../EmptyState.jsx";
import { formatDate } from "../../lib/format.js";

/** "rename_layer" -> "renamed a layer". Good enough for the verb_noun action
 * names this endpoint uses - not a general English conjugator. */
function humanizeAction(action) {
  if (!action) return "";
  const [verb, ...rest] = action.split("_");
  const noun = rest.join(" ");
  const past = /e$/.test(verb) ? `${verb}d` : `${verb}ed`;
  return noun ? `${past} a ${noun}` : past;
}

/**
 * GET /projects/{id}/activity?limit=20 -> { items: [{actor_name, action,
 * detail, target, created_at}] }, reverse-chronological. Own fetch (not fed
 * by ProjectDetailPage's load()) since it's a Dashboard-tab-only card, same
 * "reload just this section" spirit as ProjectMembers.
 */
export default function ActivityFeed({ projectId }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setItems(null);
    apiFetch(`/projects/${projectId}/activity?limit=20`)
      .then((res) => {
        if (!cancelled) setItems(res.items ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? "Could not load recent activity.");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  if (error) {
    return <ErrorBanner message={error} />;
  }
  if (items === null) {
    return <EmptyState title="Loading activity…" />;
  }
  if (items.length === 0) {
    return <EmptyState title="No activity yet" detail="Actions on this project's layers and members will show up here." />;
  }

  return (
    <ul className="activity-list">
      {items.map((item, i) => (
        <li className="activity-item" key={i}>
          <span className="activity-text">
            <strong>{item.actor_name}</strong> {humanizeAction(item.action)}
            {item.target ? ` "${item.target}"` : ""}
            {item.detail ? ` — ${item.detail}` : ""}
          </span>
          <span className="activity-time mono-cell">{formatDate(item.created_at)}</span>
        </li>
      ))}
    </ul>
  );
}
