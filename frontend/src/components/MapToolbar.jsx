import { useState } from "react";
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
        Measure ▾
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

export default function MapToolbar({
  onZoomIn,
  onZoomOut,
  onExtent,
  measureMode,
  onSelectMeasureMode,
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
          +
        </button>
        <button type="button" className="map-toolbar-btn" onClick={onZoomOut} aria-label="Zoom out" title="Zoom out">
          −
        </button>
        <button type="button" className="map-toolbar-btn map-toolbar-btn-text" onClick={onExtent}>
          Extent
        </button>
        <MeasureMenu mode={measureMode} onSelect={onSelectMeasureMode} />
        <button
          type="button"
          className={`map-toolbar-btn map-toolbar-btn-text${measureMode === "inspect" ? " map-toolbar-btn-active" : ""}`}
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
