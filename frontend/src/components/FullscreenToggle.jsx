/**
 * GEE-style fullscreen expand/collapse button, top-right of the map (see
 * ProjectMap.jsx for the real Fullscreen API wiring - this component is pure
 * presentation, `active` + `onClick` are the only inputs it needs).
 */
export default function FullscreenToggle({ active, onClick }) {
  return (
    <button
      type="button"
      className="icon-button"
      onClick={onClick}
      aria-label={active ? "Exit fullscreen" : "Enter fullscreen"}
      title={active ? "Exit fullscreen" : "Enter fullscreen"}
    >
      {active ? (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path
            d="M9 4 V9 H4 M15 4 V9 H20 M20 15 H15 V20 M4 15 H9 V20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path
            d="M4 9 V4 H9 M15 4 H20 V9 M20 15 V20 H15 M9 20 H4 V15"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
