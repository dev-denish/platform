/**
 * GEE-style "Map"/"Satellite" pill toggle, docked top-right of the map.
 * "Satellite" is the existing free Esri World Imagery basemap; "Map" is the
 * same free Carto tile source PortfolioMap.jsx already uses - no Google Maps
 * API key, no billing, per the explicit decision to stay on free imagery.
 */
export default function BasemapToggle({ mode, onChange }) {
  return (
    <div className="basemap-toggle" role="group">
      <button
        type="button"
        className={`basemap-toggle-btn${mode === "map" ? " basemap-toggle-btn-active" : ""}`}
        onClick={() => onChange("map")}
      >
        Map
      </button>
      <button
        type="button"
        className={`basemap-toggle-btn${mode === "satellite" ? " basemap-toggle-btn-active" : ""}`}
        onClick={() => onChange("satellite")}
      >
        Satellite
      </button>
    </div>
  );
}
