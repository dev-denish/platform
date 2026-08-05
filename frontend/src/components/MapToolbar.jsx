import { memo, useEffect, useRef, useState } from "react";
import { Bookmark, Camera, ChevronDown, Columns2, Minus, Plus, X } from "lucide-react";
import BasemapToggle from "./BasemapToggle.jsx";
import { parseLatLon } from "../lib/measure.js";

/**
 * Full-width top toolbar (Wave: map UI redesign) - replaces the old
 * bottom-right/floating GEE-style controls. Every control here is a
 * relocation + restyle of an existing tool (zoom, Extent/fit-bounds,
 * distance/area measuring, pixel inspection, basemap picker); none of their
 * actual behavior changes, only where they live. The live Lat/Lon/Zoom/Scale
 * readout on the right is derived entirely from the map's own live state
 * (see ProjectMap.jsx's MapViewSync) - no backend involved.
 *
 * Wave: map toolbar capabilities adds Compare (before/after swipe), Save image,
 * jump-to-coordinates, click-to-copy on that readout, and per-project view
 * bookmarks - all following the same MeasureMenu/DrawMenu dropdown shape and
 * map-toolbar-btn classes as everything already here.
 *
 * Every dropdown here (Measure/Draw/Compare/Views) is a controlled component:
 * which one is open is ONE piece of state owned by MapToolbar (`openMenu`), not
 * four independent local useState flags. With local flags a user could open
 * Measure, not pick anything, then open Draw - nothing ever told Measure to
 * close, so two menus rendered at once a few buttons apart and visually
 * collided (with each other and with the map's floating measure/draw
 * instruction banner underneath). Opening any one now closes the others by
 * construction. Basemap stays out of this: it's a native <select>, so the
 * browser already closes it for us.
 */
function MeasureMenu({ mode, onSelect, isOpen, onOpenChange }) {
  const active = mode === "distance" || mode === "area";
  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className={`map-toolbar-btn map-toolbar-btn-text${active ? " map-toolbar-btn-active" : ""}`}
        onClick={() => onOpenChange(!isOpen)}
      >
        Measure <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {isOpen ? (
        <div className="map-toolbar-menu">
          <button
            type="button"
            onClick={() => {
              onSelect("distance");
              onOpenChange(false);
            }}
          >
            Distance
          </button>
          <button
            type="button"
            onClick={() => {
              onSelect("area");
              onOpenChange(false);
            }}
          >
            Area
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** Same dropdown shape as MeasureMenu - point/line/polygon draw modes, which
 * share the map-click gesture with the measure tools and so are mutually
 * exclusive with them (ProjectMap's selectDrawMode/selectMeasureMode). */
function DrawMenu({ mode, onSelect, isOpen, onOpenChange }) {
  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className={`map-toolbar-btn map-toolbar-btn-text${mode !== "none" ? " map-toolbar-btn-active" : ""}`}
        onClick={() => onOpenChange(!isOpen)}
      >
        Draw <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {isOpen ? (
        <div className="map-toolbar-menu">
          {[
            ["point", "Point"],
            ["line", "Line"],
            ["polygon", "Polygon"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                onSelect(value);
                onOpenChange(false);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Before/after swipe comparison. The button turns compare mode on with the
 * oldest and newest dated layer already picked (so one click gives a real
 * comparison, not an empty form) and opens the picker for changing either
 * side; clicking it again turns compare off. Hidden entirely with fewer than
 * two dated layers to compare - `options` is already sorted oldest-first by
 * ProjectMap.
 */
function CompareMenu({ options, compare, onChange, isOpen, onOpenChange }) {
  if (options.length < 2) return null;

  function toggle() {
    if (compare) {
      onChange(null);
      onOpenChange(false);
      return;
    }
    onChange({ before: options[0].layer_id, after: options[options.length - 1].layer_id });
    onOpenChange(true);
  }

  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className={`map-toolbar-btn map-toolbar-btn-text${compare ? " map-toolbar-btn-active" : ""}`}
        onClick={toggle}
        title="Slide between two dates"
      >
        <Columns2 size={14} strokeWidth={2} className="icon" aria-hidden="true" /> Compare
      </button>
      {isOpen && compare ? (
        <div className="map-toolbar-menu map-toolbar-menu-wide">
          {[
            ["before", "Before"],
            ["after", "After"],
          ].map(([side, label]) => (
            <label className="map-toolbar-menu-field" key={side}>
              <span>{label}</span>
              <select
                className="map-toolbar-select"
                value={compare[side]}
                onChange={(e) => onChange({ ...compare, [side]: Number(e.target.value) })}
              >
                {options.map((o) => (
                  <option key={o.layer_id} value={o.layer_id}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <button type="button" onClick={() => onOpenChange(false)}>
            Done
          </button>
        </div>
      ) : null}
    </div>
  );
}

const bookmarkKey = (projectId) => `map-bookmarks:${projectId}`;

function readBookmarks(projectId) {
  try {
    const raw = localStorage.getItem(bookmarkKey(projectId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // Corrupt/hand-edited entry - a broken bookmark list must not break the map.
    return [];
  }
}

/**
 * Saved map views, per project, in localStorage - a personal UI convenience
 * (same call already made for the measure tools' last-used unit), keyed by
 * projectId so one project's bookmarks never show up on another's map.
 * `center`/`zoom` come from the same live map state the readout uses.
 */
function BookmarksMenu({ projectId, center, zoom, onJump, isOpen, onOpenChange }) {
  // Only open/closed is lifted; the saved list itself is this menu's own data.
  const [items, setItems] = useState(() => (projectId ? readBookmarks(projectId) : []));

  // Switching projects without unmounting the toolbar must not carry the
  // previous project's list over.
  useEffect(() => {
    setItems(projectId ? readBookmarks(projectId) : []);
  }, [projectId]);

  if (!projectId) return null;

  function write(next) {
    setItems(next);
    localStorage.setItem(bookmarkKey(projectId), JSON.stringify(next));
  }

  function save() {
    if (!center || zoom == null) return;
    const name = window.prompt("Name this view")?.trim();
    if (!name) return;
    write([...items, { name, lat: center.lat, lon: center.lng, zoom }]);
  }

  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className="map-toolbar-btn map-toolbar-btn-text"
        onClick={() => onOpenChange(!isOpen)}
        title="Saved views"
      >
        <Bookmark size={14} strokeWidth={2} className="icon" aria-hidden="true" /> Views
        <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {isOpen ? (
        <div className="map-toolbar-menu map-toolbar-menu-wide">
          <button type="button" onClick={save}>
            + Save current view
          </button>
          {items.length === 0 ? (
            <span className="map-toolbar-menu-empty">No saved views yet</span>
          ) : (
            items.map((b, i) => (
              <span className="map-toolbar-menu-row" key={`${b.name}-${i}`}>
                <button
                  type="button"
                  onClick={() => {
                    onJump(b.lat, b.lon, b.zoom);
                    onOpenChange(false);
                  }}
                >
                  {b.name}
                </button>
                <button
                  type="button"
                  className="map-toolbar-menu-remove"
                  aria-label={`Remove ${b.name}`}
                  onClick={() => write(items.filter((_, j) => j !== i))}
                >
                  <X size={14} strokeWidth={2} className="icon" />
                </button>
              </span>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Raw "lat, lon" entry - deliberately NOT geocoding (that needs an external
 * keyed service this app has no dependency on). Anything unparseable or out of
 * range just marks the field invalid instead of moving the map somewhere
 * meaningless.
 */
function JumpToCoords({ onJump }) {
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);

  function submit(e) {
    e.preventDefault();
    const parsed = parseLatLon(value);
    setInvalid(!parsed);
    if (parsed) onJump(parsed.lat, parsed.lon);
  }

  return (
    <form className="map-toolbar-jump" onSubmit={submit}>
      <input
        type="text"
        className={`map-toolbar-input${invalid ? " map-toolbar-input-invalid" : ""}`}
        placeholder="lat, lon"
        aria-label="Go to coordinates (latitude, longitude)"
        aria-invalid={invalid || undefined}
        title={invalid ? "Enter a latitude between -90 and 90 and a longitude between -180 and 180" : undefined}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setInvalid(false);
        }}
      />
      <button type="submit" className="map-toolbar-btn map-toolbar-btn-text">
        Go
      </button>
    </form>
  );
}

/**
 * memo'd - but note this DOES still re-render on every pointer move over the
 * map, by design: lat/lon/zoom/scaleLabel are the live readout and genuinely
 * change. The memo earns its keep on the OTHER re-renders (layer toggles,
 * opacity slider, pixel popups) where none of its props changed at all. The
 * mousemove cost is bounded by MapViewSync's rAF throttle, not by this memo.
 */
function MapToolbar({
  onZoomIn,
  onZoomOut,
  onExtent,
  measureMode,
  onSelectMeasureMode,
  drawMode,
  onSelectDrawMode,
  basemapMode,
  onBasemapChange,
  compareOptions,
  compare,
  onCompareChange,
  onExportImage,
  onJump,
  projectId,
  center,
  lat,
  lon,
  zoom,
  scaleLabel,
}) {
  const [copied, setCopied] = useState(false);
  const [exporting, setExporting] = useState(false);
  const copyTimerRef = useRef(null);

  // The single source of truth for which dropdown is open: "measure" | "draw" |
  // "compare" | "views" | null. Setting one necessarily unsets the others, so
  // two menus can never be on screen at the same time.
  const [openMenu, setOpenMenu] = useState(null);
  const menuProps = (id) => ({
    isOpen: openMenu === id,
    onOpenChange: (next) => setOpenMenu(next ? id : null),
  });

  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  async function copyCoords() {
    if (lat == null || lon == null) return;
    try {
      await navigator.clipboard.writeText(`${lat.toFixed(5)}, ${lon.toFixed(5)}`);
      setCopied(true);
      // Restart rather than stack, so a second click doesn't have the first
      // click's timer cut its own confirmation short.
      clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard permission denied / insecure context - nothing useful to say
      // in a one-line readout, and the coordinates are still visible to select.
    }
  }

  async function exportImage() {
    setExporting(true);
    try {
      await onExportImage();
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="map-toolbar">
      <div className="map-toolbar-group">
        <button type="button" className="map-toolbar-btn" onClick={onZoomIn} aria-label="Zoom in" title="Zoom in">
          <Plus size={18} strokeWidth={2} className="icon" />
        </button>
        <button type="button" className="map-toolbar-btn" onClick={onZoomOut} aria-label="Zoom out" title="Zoom out">
          <Minus size={18} strokeWidth={2} className="icon" />
        </button>
        <button type="button" className="map-toolbar-btn map-toolbar-btn-text" onClick={onExtent}>
          Extent
        </button>
        <MeasureMenu mode={measureMode} onSelect={onSelectMeasureMode} {...menuProps("measure")} />
        <DrawMenu mode={drawMode} onSelect={onSelectDrawMode} {...menuProps("draw")} />
        <button
          type="button"
          className={`map-toolbar-btn map-toolbar-btn-text${measureMode === "inspect" && drawMode === "none" ? " map-toolbar-btn-active" : ""}`}
          onClick={() => onSelectMeasureMode("inspect")}
        >
          Identify
        </button>
        <CompareMenu
          options={compareOptions}
          compare={compare}
          onChange={onCompareChange}
          {...menuProps("compare")}
        />
        <BookmarksMenu
          projectId={projectId}
          center={center}
          zoom={zoom}
          onJump={onJump}
          {...menuProps("views")}
        />
        <button
          type="button"
          className="map-toolbar-btn map-toolbar-btn-text"
          onClick={exportImage}
          disabled={exporting}
          title="Download the current map view as a PNG image"
        >
          <Camera size={14} strokeWidth={2} className="icon" aria-hidden="true" />{" "}
          {exporting ? "Saving…" : "Save image"}
        </button>
        <BasemapToggle mode={basemapMode} onChange={onBasemapChange} />
        <JumpToCoords onJump={onJump} />
      </div>
      <div className="map-toolbar-readout">
        <button
          type="button"
          className="map-toolbar-copy"
          onClick={copyCoords}
          disabled={lat == null || lon == null}
          title="Copy these coordinates"
        >
          {lat != null && lon != null ? (
            <>
              Lat: {lat.toFixed(5)}° Lon: {lon.toFixed(5)}°
            </>
          ) : (
            "Lat: — Lon: —"
          )}
          {copied ? <span className="map-toolbar-copied"> Copied!</span> : null}
        </button>
        {" | "}Zoom: {zoom ?? "—"}
        {" | "}Scale: {scaleLabel ?? "—"}
      </div>
    </div>
  );
}

export default memo(MapToolbar);
