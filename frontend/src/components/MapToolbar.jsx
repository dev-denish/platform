import { memo, useEffect, useRef, useState } from "react";
import { Bookmark, Camera, ChevronDown, Columns2, Eye, Frame, MapPin, Minus, Pencil, Plus, Ruler, X } from "lucide-react";
import BasemapToggle from "./BasemapToggle.jsx";
import { parseLatLon } from "../lib/measure.js";

/**
 * The map toolbar's floating panel contents (Wave: floating map controls) -
 * revealed by the wrench toggle in ProjectMap.jsx's map-overlay-topleft, a
 * single small floating card rather than the old full-width docked bar. Every
 * control here is the same tool as before (zoom, Extent/fit-bounds,
 * distance/area measuring, pixel inspection, basemap picker); only where it
 * lives changed. The live Lat/Lon/Zoom/Scale readout no longer lives here at
 * all - it moved to its own floating badge in the map's bottom-right corner
 * (see ProjectMap.jsx's CoordinateBadge), since the toolbar itself is now
 * collapsed by default and the readout needs to stay visible either way.
 *
 * Extent/Measure/Draw/Identify are icon-only (with hover tooltips via
 * title/aria-label, same as every other icon-only control in this app - see
 * FullscreenToggle/JumpToCoords) now that the panel floats free of the side
 * columns that used to force a two-row/readout-row split to fit a narrower
 * width - one row is plenty of room for icons.
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
        className={`map-toolbar-btn${active ? " map-toolbar-btn-active" : ""}`}
        onClick={() => onOpenChange(!isOpen)}
        aria-label="Measure"
        title="Measure"
      >
        <Ruler size={16} strokeWidth={2} className="icon" />
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
        className={`map-toolbar-btn${mode !== "none" ? " map-toolbar-btn-active" : ""}`}
        onClick={() => onOpenChange(!isOpen)}
        aria-label="Draw"
        title="Draw"
      >
        <Pencil size={16} strokeWidth={2} className="icon" />
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
 * Wave: toolbar overflow (fixes the ragged multi-row wrap the Analysis
 * panel's side columns caused - they permanently eat width the toolbar used
 * to have to itself). Compare, Views, and Save image are all occasional
 * actions, not always-needed like zoom/Extent/Measure/Draw/Identify/basemap,
 * so they move into one "More" dropdown instead of competing for main-row
 * space. Compare/Views each keep their own picker - `subOpen` is a SECOND,
 * independent single-open-at-a-time state local to this menu (which of
 * Compare's before/after picker or Views' saved list is expanded), separate
 * from the parent's openMenu (which only tracks whether More itself is
 * open) - the same "one piece of state, not two flags" reasoning as
 * openMenu itself, just one level deeper.
 */
function MoreMenu({ compareProps, bookmarksProps, onExportImage, isOpen, onOpenChange }) {
  const [subOpen, setSubOpen] = useState(null); // "compare" | "views" | null
  const [exporting, setExporting] = useState(false);
  const subMenuProps = (id) => ({
    isOpen: subOpen === id,
    onOpenChange: (next) => setSubOpen(next ? id : null),
  });

  // Reopening More should never resurface whichever sub-picker was left open
  // last time - start collapsed.
  useEffect(() => {
    if (!isOpen) setSubOpen(null);
  }, [isOpen]);

  async function exportImage() {
    setExporting(true);
    try {
      await onExportImage();
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className="map-toolbar-btn map-toolbar-btn-text"
        onClick={() => onOpenChange(!isOpen)}
      >
        More <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {isOpen ? (
        <div className="map-toolbar-menu map-toolbar-menu-more">
          <CompareMenu {...compareProps} {...subMenuProps("compare")} />
          <BookmarksMenu {...bookmarksProps} {...subMenuProps("views")} />
          <button
            type="button"
            className="map-toolbar-btn map-toolbar-btn-text"
            onClick={exportImage}
            disabled={exporting}
            title="Download the current map view as a PNG image"
          >
            <Camera size={14} strokeWidth={2} className="icon" aria-hidden="true" />
            {exporting ? "Saving…" : "Save image"}
          </button>
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
 *
 * Collapsed behind a pin icon until clicked (Wave: toolbar overflow) - an
 * always-visible input+button was the single widest thing on the row; open
 * state is local and closes itself again on a successful jump, same as any
 * other one-shot toolbar action.
 */
function JumpToCoords({ onJump }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function submit(e) {
    e.preventDefault();
    const parsed = parseLatLon(value);
    setInvalid(!parsed);
    // Deliberately does NOT close the form on success - jumping more than
    // once shouldn't mean re-clicking the pin icon every time.
    if (parsed) onJump(parsed.lat, parsed.lon);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="map-toolbar-btn"
        onClick={() => setOpen(true)}
        aria-label="Go to coordinates"
        title="Go to coordinates"
      >
        <MapPin size={16} strokeWidth={2} className="icon" />
      </button>
    );
  }

  return (
    <form className="map-toolbar-jump" onSubmit={submit}>
      <input
        ref={inputRef}
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
        onBlur={() => {
          if (!value) setOpen(false);
        }}
      />
      <button type="submit" className="map-toolbar-btn map-toolbar-btn-text">
        Go
      </button>
    </form>
  );
}

/**
 * memo'd like before - now genuinely stable across the mousemove-driven
 * `mapView` state, since that live readout no longer lives in this
 * component at all (see CoordinateBadge in ProjectMap.jsx) - this only
 * re-renders on an actual toolbar interaction (layer/basemap/mode change).
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
  zoom,
}) {
  // The single source of truth for which dropdown is open: "measure" | "draw" |
  // "more" | null. Setting one necessarily unsets the others, so two menus
  // can never be on screen at the same time. Compare/Views used to be
  // top-level ids here too, but now live inside "more" - see MoreMenu's own
  // `subOpen`, one level deeper for the same reason.
  const [openMenu, setOpenMenu] = useState(null);
  const menuProps = (id) => ({
    isOpen: openMenu === id,
    onOpenChange: (next) => setOpenMenu(next ? id : null),
  });

  return (
    <div className="map-toolbar">
      <button type="button" className="map-toolbar-btn" onClick={onZoomIn} aria-label="Zoom in" title="Zoom in">
        <Plus size={18} strokeWidth={2} className="icon" />
      </button>
      <button type="button" className="map-toolbar-btn" onClick={onZoomOut} aria-label="Zoom out" title="Zoom out">
        <Minus size={18} strokeWidth={2} className="icon" />
      </button>
      <button type="button" className="map-toolbar-btn" onClick={onExtent} aria-label="Extent" title="Extent">
        <Frame size={16} strokeWidth={2} className="icon" />
      </button>
      <MeasureMenu mode={measureMode} onSelect={onSelectMeasureMode} {...menuProps("measure")} />
      <DrawMenu mode={drawMode} onSelect={onSelectDrawMode} {...menuProps("draw")} />
      <button
        type="button"
        className={`map-toolbar-btn${measureMode === "inspect" && drawMode === "none" ? " map-toolbar-btn-active" : ""}`}
        onClick={() => onSelectMeasureMode("inspect")}
        aria-label="Identify"
        title="Identify"
      >
        <Eye size={16} strokeWidth={2} className="icon" />
      </button>
      <span className="map-toolbar-divider" aria-hidden="true" />
      <BasemapToggle mode={basemapMode} onChange={onBasemapChange} />
      <MoreMenu
        compareProps={{ options: compareOptions, compare, onChange: onCompareChange }}
        bookmarksProps={{ projectId, center, zoom, onJump }}
        onExportImage={onExportImage}
        {...menuProps("more")}
      />
      <JumpToCoords onJump={onJump} />
    </div>
  );
}

export default memo(MapToolbar);
