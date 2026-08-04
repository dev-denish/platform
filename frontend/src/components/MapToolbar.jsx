import { memo, useState } from "react";
import { ChevronDown, Minus, Plus } from "lucide-react";
import BasemapToggle from "./BasemapToggle.jsx";

/**
 * Full-width top toolbar (Wave: map UI redesign) - replaces the old
 * bottom-right/floating GEE-style controls. Every control here is a
 * relocation + restyle of an existing tool (zoom, Extent/fit-bounds,
 * distance/area measuring, pixel inspection, basemap picker); none of their
 * actual behavior changes, only where they live. The live Lat/Lon/Zoom/Scale
 * readout on the right is derived entirely from the map's own live state
 * (see ProjectMap.jsx's MapViewSync) - no backend involved.
 */
function MeasureMenu({ mode, onSelect }) {
  const [open, setOpen] = useState(false);
  const active = mode === "distance" || mode === "area";
  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className={`map-toolbar-btn map-toolbar-btn-text${active ? " map-toolbar-btn-active" : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        Measure <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {open ? (
        <div className="map-toolbar-menu">
          <button
            type="button"
            onClick={() => {
              onSelect("distance");
              setOpen(false);
            }}
          >
            Distance
          </button>
          <button
            type="button"
            onClick={() => {
              onSelect("area");
              setOpen(false);
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
function DrawMenu({ mode, onSelect }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="map-toolbar-dropdown">
      <button
        type="button"
        className={`map-toolbar-btn map-toolbar-btn-text${mode !== "none" ? " map-toolbar-btn-active" : ""}`}
        onClick={() => setOpen((o) => !o)}
      >
        Draw <ChevronDown size={14} strokeWidth={2} className="icon" aria-hidden="true" />
      </button>
      {open ? (
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
                setOpen(false);
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
  lat,
  lon,
  zoom,
  scaleLabel,
}) {
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
        <MeasureMenu mode={measureMode} onSelect={onSelectMeasureMode} />
        <DrawMenu mode={drawMode} onSelect={onSelectDrawMode} />
        <button
          type="button"
          className={`map-toolbar-btn map-toolbar-btn-text${measureMode === "inspect" && drawMode === "none" ? " map-toolbar-btn-active" : ""}`}
          onClick={() => onSelectMeasureMode("inspect")}
        >
          Identify
        </button>
        <BasemapToggle mode={basemapMode} onChange={onBasemapChange} />
      </div>
      <div className="map-toolbar-readout">
        {lat != null && lon != null ? (
          <>
            Lat: {lat.toFixed(5)}° Lon: {lon.toFixed(5)}°
          </>
        ) : (
          "Lat: — Lon: —"
        )}
        {" | "}Zoom: {zoom ?? "—"}
        {" | "}Scale: {scaleLabel ?? "—"}
      </div>
    </div>
  );
}

export default memo(MapToolbar);
