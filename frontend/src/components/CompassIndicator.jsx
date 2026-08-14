import { Compass } from "lucide-react";

/**
 * Static north-up indicator, top-right of the map (Wave: Map Toolbar
 * Enhancement v2, Tier 1) - Leaflet doesn't rotate the map by default, so
 * "north" is always straight up and this never needs to track bearing. Purely
 * informational, not a button (no click behavior to offer while the map can't
 * rotate) - a `title` still reads well on hover for the mouse-driven case
 * `aria-label` covers everything else.
 */
export default function CompassIndicator() {
  return (
    <div className="compass-indicator" title="North is up" aria-label="North is up">
      <Compass size={16} strokeWidth={2} className="icon" aria-hidden="true" />
    </div>
  );
}
