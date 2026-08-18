import { useEffect, useRef, useState } from "react";
import { Copy, Crosshair, ExternalLink, MapPin, Table2, ZoomIn } from "lucide-react";

const MENU_WIDTH = 220;
const MENU_EST_HEIGHT = 260;

/**
 * Right-click map context menu (Wave: Map Toolbar Enhancement v2, Tier 3).
 * Positioned from Leaflet's own `containerPoint` (pixel coords relative to
 * the map container, which is what `.map-canvas-wrap` - this menu's
 * positioned ancestor - fills exactly), clamped against that same wrapper's
 * measured size so a click near the map's right/bottom edge doesn't render
 * the menu partly off-screen.
 *
 * No entrance transition (unlike the Layers/Basemap panels): the smoothness
 * requirement for this control is explicitly "no visible delay" - it renders
 * synchronously from coordinates already in hand, no fetch/animation gates
 * the first paint.
 */
export default function MapContextMenu({ point, latlng, containerSize, onWhatsHere, onAttributeTable, onCenterHere, onZoomInHere, onClose }) {
  const [copied, setCopied] = useState(false);
  const menuRef = useRef(null);
  const copyTimerRef = useRef(null);

  useEffect(() => {
    function handlePointerDown(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose();
    }
    function handleKeyDown(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  async function copyAsGeoJson() {
    const feature = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [latlng.lng, latlng.lat] },
      properties: {},
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(feature, null, 2));
      setCopied(true);
      clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => {
        setCopied(false);
        onClose();
      }, 900);
    } catch {
      // Clipboard permission denied / insecure context - leave the menu open
      // rather than pretending it worked.
    }
  }

  function viewInGoogleMaps() {
    window.open(`https://www.google.com/maps/@${latlng.lat},${latlng.lng},18z`, "_blank", "noopener,noreferrer");
    onClose();
  }

  function viewInGoogleEarth() {
    window.open(
      `https://earth.google.com/web/@${latlng.lat},${latlng.lng},0a,1500d,35y,0h,0t,0r`,
      "_blank",
      "noopener,noreferrer"
    );
    onClose();
  }

  const left = Math.min(Math.max(point.x, 0), Math.max(containerSize.width - MENU_WIDTH, 0));
  const top = Math.min(Math.max(point.y, 0), Math.max(containerSize.height - MENU_EST_HEIGHT, 0));

  return (
    <div
      ref={menuRef}
      className="map-context-menu"
      style={{ left, top }}
      role="menu"
      aria-label="Map location actions"
    >
      <div className="map-context-menu-coords">
        {latlng.lat.toFixed(6)}, {latlng.lng.toFixed(6)}
      </div>
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={onWhatsHere}>
        <MapPin size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        What&apos;s here
      </button>
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={copyAsGeoJson}>
        <Copy size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        {copied ? "Copied!" : "Copy as GeoJSON"}
      </button>
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={onCenterHere}>
        <Crosshair size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        Center map here
      </button>
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={onZoomInHere}>
        <ZoomIn size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        Zoom in here
      </button>
      <div className="map-context-menu-divider" role="separator" />
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={onAttributeTable}>
        <Table2 size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        Attribute table
      </button>
      <div className="map-context-menu-divider" role="separator" />
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={viewInGoogleMaps}>
        <ExternalLink size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        View in Google Maps
      </button>
      <button type="button" role="menuitem" className="map-context-menu-item" onClick={viewInGoogleEarth}>
        <ExternalLink size={14} strokeWidth={2} className="icon" aria-hidden="true" />
        View in Google Earth
      </button>
    </div>
  );
}
